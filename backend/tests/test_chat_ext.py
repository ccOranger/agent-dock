"""单租户降级：聊天自定义埋点 ext、chat_events 旁路与场景自动授权。

全部走 Repo mock，不连真实库。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.v1 import admin_scenes as scenes_api
from app.api.v1.chat import _record_chat_event
from app.models import repositories as repo_mod
from app.models.repositories import AuditRepo, ChatEventRepo, IntegrationRepo, SceneRepo
from app.schemas.api import EXT_MAX_KEYS, ChatRequest

ADMIN_PATH = scenes_api.router.prefix


# ===== ext 契约 =====
def test_ext_accepts_flat_scalars():
    req = ChatRequest(
        message="你好",
        ext={"platform": " Web ", "tenant_hint": "t_1", "vip": True, "age": 3},
    )
    # 校验只做限额与类型收敛，不改写业务值：渠道（platform）等设备信息就是普通键
    assert req.ext == {"platform": " Web ", "tenant_hint": "t_1", "vip": True, "age": 3}


@pytest.mark.parametrize(
    "ext",
    [
        {"Platform": "web"},  # 大写字段名
        {"1st": "x"},  # 数字开头
        {"token": "secret"},  # 敏感键
        {"k" * 33: "v"},  # 键超长
        {"platform": "x" * 129},  # 值超长
        {"platform": {"nested": 1}},  # 非标量（对象）
        {"platform": ["web"]},  # 非标量（数组）
        {f"k{i}": "v" for i in range(EXT_MAX_KEYS + 1)},  # 键数量超限
        {f"key_{i}": "x" * 120 for i in range(10)},  # 序列化后超 1KB
    ],
)
def test_ext_rejects_invalid_payload(ext):
    with pytest.raises(ValidationError):
        ChatRequest(message="hi", ext=ext)


# ===== 埋点旁路 =====
@pytest.mark.asyncio
async def test_record_chat_event_passes_ext_through(monkeypatch):
    captured: dict = {}

    async def record(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ChatEventRepo, "record", record)
    body = ChatRequest(
        message="你好",
        session_id="not-a-uuid",
        visitor_id=str(uuid.uuid4()),
        ext={"platform": "MiniApp", "vip": True},
    )
    await _record_chat_event(
        trace_id="trace-1",
        scene_code="mall",
        body=body,
        user=None,
        integration_id="i-1",
    )
    # 渠道不再单独拍平成列：ext 原样交给 Repo 落 JSONB
    assert "platform" not in captured
    assert captured["ext"] == {"platform": "MiniApp", "vip": True}
    assert captured["message_chars"] == len("你好")
    assert captured["session_id"] == "not-a-uuid"


@pytest.mark.asyncio
async def test_record_chat_event_failure_does_not_raise(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(ChatEventRepo, "record", boom)
    # 埋点失败只告警，不能把对话主流程带崩
    await _record_chat_event(
        trace_id="trace-1",
        scene_code="mall",
        body=ChatRequest(message="hi"),
        user=SimpleNamespace(id=uuid.uuid4()),
        integration_id=None,
    )


class _FakeResult:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeDb:
    def __init__(self):
        self.added: list = []
        self.statements: list = []
        self.committed = 0
        self.rowcount = 1

    def add(self, obj):
        self.added.append(obj)

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _FakeResult(self.rowcount)

    async def commit(self):
        self.committed += 1


def _install_fake_db(monkeypatch) -> _FakeDb:
    fake = _FakeDb()

    class _CM:
        async def __aenter__(self):
            return fake

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(repo_mod, "SessionLocal", lambda: _CM())
    return fake


@pytest.mark.asyncio
async def test_chat_event_repo_sanitizes_ids(monkeypatch):
    fake = _install_fake_db(monkeypatch)
    session_id = str(uuid.uuid4())
    await ChatEventRepo.record(
        trace_id="t" * 80,
        scene_code="s" * 40,
        session_id=session_id,
        visitor_id="not-a-uuid",
        integration_id=str(uuid.uuid4()),
        ext={"platform": "web"},
        message_chars=-5,
    )
    event = fake.added[0]
    assert event.trace_id == "t" * 64
    assert event.scene_code == "s" * 32
    assert str(event.session_id) == session_id
    # 客户端传入非 UUID 时落库为 NULL，不能让整条埋点写入失败
    assert event.visitor_id is None
    assert event.message_chars == 0
    # 渠道只存在于 ext 里，不再有独立列
    assert event.ext == {"platform": "web"}
    assert not hasattr(event, "platform")
    assert fake.committed == 1


@pytest.mark.asyncio
async def test_attach_identity_skips_when_no_usable_uuid(monkeypatch):
    """没有可回填的 UUID 时不能发 UPDATE：v_xxx 这类宿主标识不能覆盖已有列。"""
    fake = _install_fake_db(monkeypatch)
    assert (
        await ChatEventRepo.attach_identity(trace_id="t-1", session_id="v_demo", visitor_id=None)
        == 0
    )
    assert (await ChatEventRepo.attach_identity(trace_id="", session_id=str(uuid.uuid4()))) == 0
    assert fake.statements == []
    assert fake.committed == 0


@pytest.mark.asyncio
async def test_attach_identity_writes_real_ids_by_trace(monkeypatch):
    fake = _install_fake_db(monkeypatch)
    session_id = uuid.uuid4()
    visitor_id = uuid.uuid4()
    updated = await ChatEventRepo.attach_identity(
        trace_id="t-1",
        session_id=str(session_id),
        visitor_id=str(visitor_id),
        user_id="not-a-uuid",  # 无效值跳过，不能写成 NULL 覆盖已有列
    )
    assert updated == 1
    params = fake.statements[0].compile().params
    assert params["session_id"] == session_id
    assert params["visitor_id"] == visitor_id
    assert "user_id" not in params
    assert fake.committed == 1


# ===== 新建场景自动授权 =====
def _scene(**kw) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "code": "presale",
        "display_name": "售前咨询",
        "description": None,
        "status": "active",
        "is_default": False,
        "router_mode": "auto",
        "router_model": None,
        "router_confidence_threshold": None,
        "router_max_intents": None,
        "fallback_agent_id": None,
        "fallback_text": None,
        "extra_router_prompt": None,
        "publish_key": None,
        "allowed_origins": [],
        "widget_config": {},
        "created_at": None,
        "updated_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([scenes_api.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def _install_create(monkeypatch, scene, grant=None) -> None:
    async def get_by_code(code):
        return None

    async def create(**fields):
        return scene

    async def audit(*args, **kwargs):
        return None

    monkeypatch.setattr(SceneRepo, "get_by_code", get_by_code)
    monkeypatch.setattr(SceneRepo, "create", create)
    monkeypatch.setattr(AuditRepo, "write", audit)
    if grant is not None:
        monkeypatch.setattr(IntegrationRepo, "grant_scene_to_all", grant)


def test_create_scene_grants_new_scene_to_all_integrations(admin_client, monkeypatch):
    scene = _scene()
    granted: list[str] = []

    async def grant(scene_id):
        granted.append(str(scene_id))
        return 1

    _install_create(monkeypatch, scene, grant)
    resp = admin_client.post(ADMIN_PATH, json={"code": "presale", "display_name": "售前咨询"})
    assert resp.status_code == 200
    # 已有 Key 必须能直接调用新场景，否则后台建完场景就是 403
    assert granted == [str(scene.id)]


def test_create_scene_survives_grant_failure(admin_client, monkeypatch):
    scene = _scene()

    async def grant(scene_id):
        raise RuntimeError("db down")

    _install_create(monkeypatch, scene, grant)
    resp = admin_client.post(ADMIN_PATH, json={"code": "presale", "display_name": "售前咨询"})
    # 授权是旁路：失败只告警，场景创建仍然成功
    assert resp.status_code == 200
    assert resp.json()["data"]["code"] == "presale"
