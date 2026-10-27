"""M5 对外接入：公开配置、访客签发、聊天鉴权/限流、动态 CORS、后台嵌入配置。

全部走 Repo/Redis mock，不连真实库；CORS 的 TestClient 应用单独构造，
避免启动 lifespan（自检会连数据库）。
"""
import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import admin_scenes as scenes_api
from app.api.v1 import public as public_api
from app.api.v1.chat import _resolve_and_authorize
from app.core import cors as cors_mod
from app.core import ratelimit as rl
from app.core.config import settings
from app.core.cors import SceneCORSMiddleware
from app.core.exceptions import Forbidden, RateLimited, Unauthorized
from app.models.repositories import AuditRepo, IntegrationRepo, SceneRepo, VisitorRepo
from app.schemas.scene import SceneContext, SceneQuota
from app.services.scene_service import authorize_scene_access

PUBLIC_PATH = public_api.router.prefix  # /public/scenes
ADMIN_PATH = scenes_api.router.prefix  # /admin/scenes


def _scene(**kw) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "code": "mall",
        "display_name": "商城订单",
        "description": "订单与物流咨询",
        "status": "active",
        "is_default": False,
        "router_mode": "auto",
        "router_model": None,
        "router_confidence_threshold": None,
        "router_max_intents": None,
        "fallback_agent_id": None,
        "fallback_text": None,
        "extra_router_prompt": None,
        "widget_config": {},
        "allowed_origins": [],
        "publish_key": None,
        "created_at": None,
        "updated_at": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _install_scene(monkeypatch, scene) -> None:
    async def get_by_code(code):
        return scene if code == scene.code else None

    async def get(scene_id):
        return scene

    async def update(scene_id, **fields):
        for key, value in fields.items():
            setattr(scene, key, value)
        return scene

    async def audit(*args, **kwargs):
        return None

    async def bound_ids(scene_id):
        return []

    monkeypatch.setattr(SceneRepo, "get_by_code", get_by_code)
    monkeypatch.setattr(SceneRepo, "get", get)
    monkeypatch.setattr(SceneRepo, "update", update)
    monkeypatch.setattr(SceneRepo, "get_bound_agent_ids", bound_ids)
    monkeypatch.setattr(AuditRepo, "write", audit)


@pytest.fixture
def public_client(make_test_app):
    app = make_test_app([public_api.router], prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([scenes_api.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


# ===== 公开场景配置 =====
def test_public_scene_hides_orchestration(public_client, monkeypatch):
    _install_scene(monkeypatch, _scene())
    resp = public_client.get(f"/api/v1{PUBLIC_PATH}/mall")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["code"] == "mall"
    assert data["allow_anonymous"] is False
    assert data["auth_modes"] == ["customer_token", "api_key"]
    # 展示配置：默认值 + 标题回退场景名
    assert data["widget"]["welcome"] == "您好，请问有什么可以帮您？"
    assert data["widget"]["title"] == "商城订单"
    # 编排细节不能泄露给宿主页
    leaked = {
        "candidates",
        "direct_agent",
        "agent_names",
        "fallback_text",
        "router_model",
        "router_mode",
        "extra_router_prompt",
    }
    assert leaked.isdisjoint(data)


def test_public_scene_published_and_widget_override(public_client, monkeypatch):
    scene = _scene(
        publish_key="pk_demo",
        widget_config={"title": "小助手", "theme_color": "#123456", "welcome": "hi"},
        allowed_origins=["https://mall.example.com"],
    )
    _install_scene(monkeypatch, scene)
    data = public_client.get(f"/api/v1{PUBLIC_PATH}/mall").json()["data"]
    assert data["allow_anonymous"] is True
    assert data["auth_modes"][0] == "anonymous"
    assert data["publish_key"] == "pk_demo"
    assert data["widget"]["title"] == "小助手"
    assert data["widget"]["theme_color"] == "#123456"
    # 来源配置也不出公开配置（避免把其它站点地址泄露出去）
    assert "allowed_origins" not in data


def test_public_scene_unknown_and_paused(public_client, monkeypatch):
    _install_scene(monkeypatch, _scene())
    missing = public_client.get(f"/api/v1{PUBLIC_PATH}/nope")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == 3003

    _install_scene(monkeypatch, _scene(status="paused"))
    paused = public_client.get(f"/api/v1{PUBLIC_PATH}/mall")
    assert paused.status_code == 404
    assert paused.json()["detail"]["code"] == 3004


# ===== 访客签发 =====
def test_issue_visitor_requires_published_scene(public_client, monkeypatch):
    _install_scene(monkeypatch, _scene())
    resp = public_client.post(f"/api/v1{PUBLIC_PATH}/mall/visitors")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003


def test_issue_visitor_returns_external_key(public_client, monkeypatch):
    scene = _scene(publish_key="pk_demo")
    _install_scene(monkeypatch, scene)
    captured: dict = {}

    async def fake_get_or_create(scene_id, external_key=None, integration_id=None):
        captured["scene_id"] = str(scene_id)
        captured["external_key"] = external_key
        captured["integration_id"] = integration_id
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(VisitorRepo, "get_or_create", fake_get_or_create)
    resp = public_client.post(f"/api/v1{PUBLIC_PATH}/mall/visitors")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["visitor_id"].startswith("v_")
    assert data["scene_code"] == "mall"
    assert captured["external_key"] == data["visitor_id"]
    assert captured["scene_id"] == str(scene.id)
    assert captured["integration_id"] is None
    assert data["integration_code"] is None


def _install_integration(monkeypatch, integration) -> None:
    async def resolve(raw):
        return integration if raw == integration["public_code"] else None

    monkeypatch.setattr(IntegrationRepo, "resolve_public_code", resolve)


def test_public_scene_returns_integration_attribution(public_client, monkeypatch):
    scene = _scene(publish_key="pk_demo")
    _install_scene(monkeypatch, scene)
    _install_integration(
        monkeypatch,
        {"id": "i-1", "name": "宿主 A", "public_code": "ic_a", "scene_ids": {str(scene.id)}},
    )

    data = public_client.get(
        f"/api/v1{PUBLIC_PATH}/mall", headers={"X-Integration-Code": "ic_a"}
    ).json()["data"]
    assert data["integration"] == {"code": "ic_a", "name": "宿主 A"}

    # 也支持 query 形式，便于 iframe 直接携带
    via_query = public_client.get(
        f"/api/v1{PUBLIC_PATH}/mall?integration=ic_a"
    ).json()["data"]
    assert via_query["integration"]["code"] == "ic_a"


def test_public_scene_rejects_invalid_or_unauthorized_integration(public_client, monkeypatch):
    scene = _scene(publish_key="pk_demo")
    _install_scene(monkeypatch, scene)
    _install_integration(
        monkeypatch,
        {"id": "i-1", "name": "宿主 A", "public_code": "ic_a", "scene_ids": set()},
    )

    invalid = public_client.get(
        f"/api/v1{PUBLIC_PATH}/mall", headers={"X-Integration-Code": "ic_missing"}
    )
    assert invalid.status_code == 401

    unauthorized = public_client.get(
        f"/api/v1{PUBLIC_PATH}/mall", headers={"X-Integration-Code": "ic_a"}
    )
    assert unauthorized.status_code == 403
    assert unauthorized.json()["detail"]["code"] == 2003


def test_issue_visitor_binds_integration_namespace(public_client, monkeypatch):
    scene = _scene(publish_key="pk_demo")
    _install_scene(monkeypatch, scene)
    _install_integration(
        monkeypatch,
        {"id": "i-1", "name": "宿主 A", "public_code": "ic_a", "scene_ids": {str(scene.id)}},
    )
    captured: dict = {}

    async def fake_get_or_create(scene_id, external_key=None, integration_id=None):
        captured.update(integration_id=integration_id, external_key=external_key)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(VisitorRepo, "get_or_create", fake_get_or_create)
    resp = public_client.post(
        f"/api/v1{PUBLIC_PATH}/mall/visitors", headers={"X-Integration-Code": "ic_a"}
    )
    assert resp.status_code == 200
    assert captured["integration_id"] == "i-1"
    assert resp.json()["data"]["integration_code"] == "ic_a"


def test_issue_visitor_rejects_wrong_publish_key(public_client, monkeypatch):
    _install_scene(monkeypatch, _scene(publish_key="pk_demo"))
    resp = public_client.post(
        f"/api/v1{PUBLIC_PATH}/mall/visitors", headers={"X-Publish-Key": "pk_other"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == 2001


# ===== 聊天鉴权 =====
def _ctx(**kw) -> SceneContext:
    base = {
        "scene_id": str(uuid.uuid4()),
        "code": "mall",
        "name": "商城订单",
        "status": "active",
        "is_default": False,
        "router_mode": "auto",
        "agent_names": ("mall_agent",),
        "direct_agent": None,
    }
    base.update(kw)
    return SceneContext(**base)


def test_chat_requires_credentials_when_not_published():
    with pytest.raises(Unauthorized):
        authorize_scene_access(_ctx(), user=None)


def test_chat_allows_published_scene_anonymous():
    authorize_scene_access(_ctx(publish_key="pk_demo"), user=None)


def test_chat_api_key_resolves_to_authorized_integration():
    scene = _ctx()
    integration = {"id": "i-1", "name": "宿主 A", "scene_ids": {scene.scene_id}}
    authorize_scene_access(scene, user=None, integration=integration)
    # Key 有效但未被授权访问该场景：403，不降级为匿名
    with pytest.raises(Forbidden):
        authorize_scene_access(
            scene, user=None, integration={"id": "i-2", "name": "宿主 B", "scene_ids": set()}
        )


def _request(**headers) -> SimpleNamespace:
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()})


@pytest.mark.asyncio
async def test_anonymous_public_code_resolves_integration(monkeypatch):
    scene = _ctx(publish_key="pk_demo")
    integration = {
        "id": "i-1",
        "name": "宿主 A",
        "public_code": "ic_a",
        "via": "public_code",
        "scene_ids": {scene.scene_id},
    }

    async def resolve(raw):
        return integration if raw == "ic_a" else None

    monkeypatch.setattr(IntegrationRepo, "resolve_public_code", resolve)
    got = await _resolve_and_authorize(_request(**{"X-Integration-Code": "ic_a"}), scene, None)
    assert got["id"] == "i-1"


@pytest.mark.asyncio
async def test_anonymous_public_code_rejects_invalid_and_unauthorized(monkeypatch):
    scene = _ctx(publish_key="pk_demo")

    async def resolve(raw):
        if raw != "ic_b":
            return None
        return {
            "id": "i-2",
            "name": "宿主 B",
            "public_code": "ic_b",
            "via": "public_code",
            "scene_ids": set(),
        }

    monkeypatch.setattr(IntegrationRepo, "resolve_public_code", resolve)
    with pytest.raises(Unauthorized):
        await _resolve_and_authorize(_request(**{"X-Integration-Code": "ic_missing"}), scene, None)
    with pytest.raises(Forbidden):
        await _resolve_and_authorize(_request(**{"X-Integration-Code": "ic_b"}), scene, None)


def test_chat_wrong_publish_key_is_rejected():
    scene = _ctx(publish_key="pk_demo")
    with pytest.raises(Unauthorized):
        authorize_scene_access(scene, user=None, publish_key="pk_other")
    # 非 ASCII 头不能把 compare_digest 打崩成 500
    with pytest.raises(Unauthorized):
        authorize_scene_access(scene, user=None, publish_key="发布标识")


def test_chat_logged_in_customer_always_allowed():
    customer = SimpleNamespace(id="c-1", role="customer")
    authorize_scene_access(_ctx(), user=customer)
    staff = SimpleNamespace(id="s-1", role="admin")
    with pytest.raises(Forbidden):
        authorize_scene_access(_ctx(), user=staff)


# ===== 限流 =====
@pytest.fixture
def redis_on(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "redis://test")
    monkeypatch.setattr(settings, "public_chat_rate_limit_per_minute", 0)
    monkeypatch.setattr(settings, "public_chat_daily_quota", 0)
    monkeypatch.setattr(settings, "public_chat_max_concurrent", 0)


@pytest.mark.asyncio
async def test_rate_limit_disabled_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "redis_url", "")
    slot = await rl.acquire_chat_slot("mall", "ip:1.1.1.1")
    assert slot._key is None


@pytest.mark.asyncio
async def test_minute_limit_exceeded(redis_on, monkeypatch):
    monkeypatch.setattr(settings, "public_chat_rate_limit_per_minute", 2)
    rolled_back: list[str] = []

    async def fake_incr_many(buckets):
        return [3 for _ in buckets]

    async def fake_decr_many(keys):
        rolled_back.extend(keys)

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr_many", fake_decr_many)
    with pytest.raises(RateLimited) as exc:
        await rl.acquire_chat_slot("mall", "ip:1.1.1.1")
    assert exc.value.code == 4029
    # 被拒绝的请求不能白白扣掉次数
    assert rolled_back and rolled_back[0].startswith("rl:chat:m:mall:ip:1.1.1.1:")


@pytest.mark.asyncio
async def test_over_limit_rolls_back_every_bucket(redis_on, monkeypatch):
    """超限必须回滚本次 INCR 的全部桶：只回滚到失败桶会让并发槽与场景日额度白扣。

    并发槽带 300s TTL 且每次重试都刷新，若被扣住不放，用户会被
    “当前对话尚未结束”锁死；场景日额度被白扣则会连累全场景。
    """
    monkeypatch.setattr(settings, "public_chat_rate_limit_per_minute", 2)
    monkeypatch.setattr(settings, "public_chat_daily_quota", 100)
    monkeypatch.setattr(settings, "public_chat_max_concurrent", 3)
    rolled_back: list[str] = []

    async def fake_incr_many(buckets):
        # 第一个桶（主体分钟）就超限，但其后四个桶已经一并 INCR 了
        return [3] + [1] * (len(buckets) - 1)

    async def fake_decr_many(keys):
        rolled_back.extend(keys)

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr_many", fake_decr_many)
    with pytest.raises(RateLimited):
        await rl.acquire_chat_slot(
            "mall", "ip:1.1.1.1", SceneQuota(per_minute=10, daily=100)
        )
    assert len(rolled_back) == 5
    assert any(k.startswith("rl:chat:c:mall:ip:1.1.1.1") for k in rolled_back)
    assert any(k.startswith("rl:chat:sd:mall:") for k in rolled_back)
    assert any(k.startswith("rl:chat:d:mall:ip:1.1.1.1:") for k in rolled_back)


@pytest.mark.asyncio
async def test_daily_quota_exceeded(redis_on, monkeypatch):
    monkeypatch.setattr(settings, "public_chat_daily_quota", 1)

    async def fake_incr_many(buckets):
        return [2 for _ in buckets]

    async def fake_decr_many(keys):
        return None

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr_many", fake_decr_many)
    with pytest.raises(RateLimited):
        await rl.acquire_chat_slot("mall", "visitor:v_1")


@pytest.mark.asyncio
async def test_scene_daily_quota_exceeded(redis_on, monkeypatch):
    """场景总额度是独立于主体的桶：默认不限，配了才生效。"""
    seen: list[list[str]] = []

    async def fake_incr_many(buckets):
        seen.append([key for key, _ in buckets])
        return [3 for _ in buckets]

    async def fake_decr_many(keys):
        return None

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr_many", fake_decr_many)
    quota = SceneQuota(per_minute=0, daily=2)
    with pytest.raises(RateLimited, match="本场景今日对话额度已用完"):
        await rl.acquire_chat_slot("mall", "ip:2.2.2.2", quota)
    assert any(key.startswith("rl:chat:sd:mall:") for key in seen[0])


@pytest.mark.asyncio
async def test_scene_quota_unlimited_by_default(redis_on, monkeypatch):
    """场景未配额时不应新增任何桶（旧行为不变）。"""
    monkeypatch.setattr(settings, "public_chat_max_concurrent", 1)
    seen: list[list[str]] = []

    async def fake_incr_many(buckets):
        seen.append([key for key, _ in buckets])
        return [1 for _ in buckets]

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    slot = await rl.acquire_chat_slot("mall", "ip:1.1.1.1", SceneQuota(per_minute=0, daily=0))
    assert slot._key == "rl:chat:c:mall:ip:1.1.1.1"
    assert not any(key.startswith("rl:chat:s") for key in seen[0])


@pytest.mark.asyncio
async def test_concurrent_stream_limit(redis_on, monkeypatch):
    monkeypatch.setattr(settings, "public_chat_max_concurrent", 1)
    released: list[str] = []

    async def fake_incr_many(buckets):
        return [2 for _ in buckets]

    async def fake_decr_many(keys):
        released.extend(keys)

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr_many", fake_decr_many)
    with pytest.raises(RateLimited, match="尚未结束"):
        await rl.acquire_chat_slot("mall", "ip:1.1.1.1")
    # 超限时立即归还自己加的计数，不能把后续正常请求也堵死
    assert released and released[0].startswith("rl:chat:c:mall:")


@pytest.mark.asyncio
async def test_concurrent_slot_released_once(redis_on, monkeypatch):
    monkeypatch.setattr(settings, "public_chat_max_concurrent", 1)
    released: list[str] = []

    async def fake_incr_many(buckets):
        return [1 for _ in buckets]

    async def fake_decr(key):
        released.append(key)

    monkeypatch.setattr(rl, "_incr_many", fake_incr_many)
    monkeypatch.setattr(rl, "_decr", fake_decr)
    slot = await rl.acquire_chat_slot("mall", "ip:1.1.1.1")
    assert slot._key is not None
    await slot.release()
    await slot.release()
    assert len(released) == 1


# ===== 动态 CORS =====
def _cors_app(monkeypatch, scene) -> TestClient:
    _install_scene(monkeypatch, scene)
    cors_mod.clear_origin_cache()
    app = FastAPI()
    app.add_middleware(SceneCORSMiddleware)

    @app.get("/api/v1/public/scenes/{code}")
    async def read(code: str):  # pragma: no cover - 只验证中间件
        return {"code": code}

    @app.get("/api/v1/chat/ping")
    async def ping():  # pragma: no cover
        return {"ok": True}

    return TestClient(app)


def test_cors_allows_scene_origin(monkeypatch):
    client = _cors_app(
        monkeypatch, _scene(allowed_origins=["https://mall.example.com"])
    )
    resp = client.get(
        "/api/v1/public/scenes/mall", headers={"Origin": "https://mall.example.com"}
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://mall.example.com"
    assert "origin" in resp.headers["vary"].lower()


def test_cors_denies_unlisted_origin(monkeypatch):
    client = _cors_app(monkeypatch, _scene())
    resp = client.get(
        "/api/v1/public/scenes/mall", headers={"Origin": "https://evil.example.com"}
    )
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers


def test_cors_global_whitelist_still_works(monkeypatch):
    client = _cors_app(monkeypatch, _scene())
    origin = settings.allowed_origin_list[0]
    resp = client.get("/api/v1/chat/ping", headers={"Origin": origin})
    assert resp.headers["access-control-allow-origin"] == origin


def test_cors_preflight_and_rejection(monkeypatch):
    client = _cors_app(
        monkeypatch, _scene(allowed_origins=["https://mall.example.com"])
    )
    ok = client.options(
        "/api/v1/chat/ping?scene_code=mall",
        headers={
            "Origin": "https://mall.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert ok.status_code == 204
    assert ok.headers["access-control-allow-origin"] == "https://mall.example.com"
    assert "POST" in ok.headers["access-control-allow-methods"]
    assert ok.headers["access-control-allow-headers"] == "content-type"

    denied = client.options(
        "/api/v1/chat/ping?scene_code=mall",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 2003


def test_cors_cache_is_invalidated_by_admin(monkeypatch):
    scene = _scene(allowed_origins=["https://mall.example.com"])
    _install_scene(monkeypatch, scene)
    cors_mod.clear_origin_cache()
    calls: list[str] = []
    original = SceneRepo.get_by_code

    async def counting(code):
        calls.append(code)
        return await original(code)

    monkeypatch.setattr(SceneRepo, "get_by_code", counting)

    async def run():
        return await cors_mod._scene_origins("mall")

    asyncio.run(run())
    asyncio.run(run())
    assert calls == ["mall"]
    cors_mod.invalidate_origin_cache("mall")
    asyncio.run(run())
    assert calls == ["mall", "mall"]


# ===== 后台嵌入配置与 key 轮换 =====
def test_update_widget_normalizes_and_publishes(admin_client, monkeypatch):
    scene = _scene()
    _install_scene(monkeypatch, scene)
    resp = admin_client.put(
        f"{ADMIN_PATH}/{scene.id}/widget",
        json={
            "widget_config": {
                "title": "订单助手",
                "theme_color": "red",
                "position": "left",
                "unknown": "x",
            },
            "allowed_origins": ["https://mall.example.com/chat/"],
            "allow_anonymous": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["widget_config"]["title"] == "订单助手"
    # 非法颜色被丢弃，未知键不进库
    assert "theme_color" not in data["widget_config"]
    assert "unknown" not in data["widget_config"]
    assert data["allowed_origins"] == ["https://mall.example.com"]
    assert data["publish_key"].startswith("pk_")


def test_update_widget_keeps_existing_publish_key(admin_client, monkeypatch):
    scene = _scene(publish_key="pk_existing")
    _install_scene(monkeypatch, scene)
    data = admin_client.put(
        f"{ADMIN_PATH}/{scene.id}/widget", json={"allow_anonymous": True}
    ).json()["data"]
    assert data["publish_key"] == "pk_existing"


def test_update_widget_can_close_anonymous(admin_client, monkeypatch):
    scene = _scene(publish_key="pk_existing")
    _install_scene(monkeypatch, scene)
    data = admin_client.put(
        f"{ADMIN_PATH}/{scene.id}/widget", json={"allow_anonymous": False}
    ).json()["data"]
    assert data["publish_key"] is None


def test_update_widget_rejects_bad_origin(admin_client, monkeypatch):
    scene = _scene()
    _install_scene(monkeypatch, scene)
    resp = admin_client.put(
        f"{ADMIN_PATH}/{scene.id}/widget",
        json={"allowed_origins": ["ftp://mall.example.com"]},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001
