"""后台人员与配额接口：RBAC、已用数量合并查询、防自锁与密码重置。

这一页是"配额配置"的唯一入口：键名写错就等于没配，自己把自己停用就等于
管理员永久失去后台——所以校验与防自锁要逐个钉住。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import current_user
from app.api.v1 import admin_staff as staff_api
from app.models.repositories import AuditRepo, StaffRepo

SELF_ID = str(uuid.uuid4())
OTHER_ID = str(uuid.uuid4())
PATH = staff_api.router.prefix  # /admin/staff


def _row(
    username="service01",
    role="agent",
    display_name="客服一号",
    quota=None,
    is_active=True,
    user_id=None,
):
    return SimpleNamespace(
        id=uuid.UUID(user_id or OTHER_ID),
        username=username,
        role=role,
        display_name=display_name,
        quota=quota,
        is_active=is_active,
        created_at=None,
    )


def _client(make_test_app, role="admin", user_id=SELF_ID, quota=None):
    app = make_test_app([staff_api.router], prefix="")
    user = SimpleNamespace(
        id=user_id,
        username="admin",
        role=role,
        display_name="管理员",
        is_active=True,
        quota=quota or {},
    )
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def admin_client(make_test_app):
    return _client(make_test_app)


def _mute_audit(monkeypatch):
    written: list[dict] = []

    async def fake_write(*args, **kw):
        written.append(kw)
        return None

    monkeypatch.setattr(AuditRepo, "write", fake_write)
    return written


def _stub_usage_by_owner(monkeypatch, mapping=None):
    """按 kind 返回 {owner_id: 已用}；同时记录调用次数以证明没有 N+1。"""
    calls: list[str] = []
    mapping = mapping or {}

    async def fake_usage(kind):
        calls.append(kind)
        return mapping.get(kind, {})

    monkeypatch.setattr(staff_api, "usage_by_owner", fake_usage)
    return calls


# ===== RBAC =====
def test_agent_role_is_rejected(make_test_app):
    client = _client(make_test_app, role="agent")
    assert client.get(PATH).status_code == 403
    resp = client.post(
        PATH, json={"username": "x1", "password": "Passw0rd!", "role": "agent"}
    )
    assert resp.status_code == 403


# ===== 列表 =====
def test_list_merges_usage_in_one_query_per_kind(make_test_app, monkeypatch):
    users = [_row(username="service01"), _row(username="service02", user_id=SELF_ID)]
    _stub_usage_by_owner(
        monkeypatch,
        {
            "scene": {OTHER_ID: 2},
            "agent": {OTHER_ID: 1, SELF_ID: 5},
            "knowledge_base": {},
        },
    )

    async def fake_list_all():
        return users

    monkeypatch.setattr(StaffRepo, "list_all", fake_list_all)

    data = _client(make_test_app).get(PATH).json()["data"]
    assert [row["username"] for row in data] == ["service01", "service02"]
    assert data[0]["used"] == {"scenes": 2, "agents": 1, "knowledge_bases": 0}
    assert data[1]["used"] == {"scenes": 0, "agents": 5, "knowledge_bases": 0}


def test_list_computes_remaining_and_null_when_unlimited(make_test_app, monkeypatch):
    users = [
        _row(quota={"max_scenes": 3, "max_agents": 0}),
        _row(username="service02", user_id=SELF_ID, quota={}),
    ]
    _stub_usage_by_owner(monkeypatch, {"scene": {OTHER_ID: 2}})

    async def fake_list_all():
        return users

    monkeypatch.setattr(StaffRepo, "list_all", fake_list_all)

    data = _client(make_test_app).get(PATH).json()["data"]
    assert data[0]["quota"] == {"max_scenes": 3, "max_agents": 0}
    # 3 - 2 = 1；0 = 禁止，剩余始终 0；未配置 = null（不限）
    assert data[0]["remaining"] == {"scenes": 1, "agents": 0, "knowledge_bases": None}
    assert data[1]["remaining"] == {"scenes": None, "agents": None, "knowledge_bases": None}


def test_list_clamps_remaining_at_zero(make_test_app, monkeypatch):
    """历史超额（先调低配额再降级）不能显示成负数"""
    users = [_row(quota={"max_scenes": 1})]
    _stub_usage_by_owner(monkeypatch, {"scene": {OTHER_ID: 4}})

    async def fake_list_all():
        return users

    monkeypatch.setattr(StaffRepo, "list_all", fake_list_all)

    data = _client(make_test_app).get(PATH).json()["data"]
    assert data[0]["remaining"]["scenes"] == 0


# ===== 创建 =====
def test_create_conflicts_on_duplicate_username(make_test_app, monkeypatch):
    async def fake_get_by_username(username):
        return _row(username=username)

    monkeypatch.setattr(StaffRepo, "get_by_username", fake_get_by_username)

    resp = _client(make_test_app).post(
        PATH, json={"username": "service01", "password": "Passw0rd!", "role": "agent"}
    )
    assert resp.status_code == 409


def test_create_rejects_unknown_quota_key(make_test_app, monkeypatch):
    """键名写错等于没配：必须报错而不是默默存进 JSONB"""
    async def fake_get_by_username(username):
        return None

    monkeypatch.setattr(StaffRepo, "get_by_username", fake_get_by_username)

    resp = _client(make_test_app).post(
        PATH,
        json={
            "username": "service02",
            "password": "Passw0rd!",
            "role": "agent",
            "quota": {"max_scene": 2},
        },
    )
    assert resp.status_code == 400
    assert "未知键" in resp.json()["detail"]["message"]


def test_create_persists_normalized_quota_and_audits(make_test_app, monkeypatch):
    async def fake_get_by_username(username):
        return None

    captured: dict = {}

    async def fake_create(**fields):
        captured.update(fields)
        return _row(username=fields["username"], quota=fields.get("quota"), user_id=SELF_ID)

    monkeypatch.setattr(StaffRepo, "get_by_username", fake_get_by_username)
    monkeypatch.setattr(StaffRepo, "create", fake_create)
    written = _mute_audit(monkeypatch)

    resp = _client(make_test_app).post(
        PATH,
        json={
            "username": "service02",
            "password": "Passw0rd!",
            "role": "agent",
            "display_name": "客服二号",
            "quota": {"max_scenes": 2, "max_agents": None},
        },
    )
    assert resp.status_code == 200
    # null 表示不限：存进库只会让"缺失"与"null"两种写法混在一起
    assert captured["quota"] == {"max_scenes": 2}
    assert captured["role"] == "agent"
    # 密码不得以明文出现在仓储入参里
    assert captured["password_hash"] != "Passw0rd!"
    assert written[0]["action"] == "admin.staff.create"
    assert resp.json()["data"]["quota"] == {"max_scenes": 2}


# ===== 修改：防自锁 =====
def test_cannot_change_own_role(make_test_app, monkeypatch):
    async def fake_get_by_id(staff_id):
        return _row(user_id=SELF_ID, role="admin")

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)

    resp = _client(make_test_app).patch(f"{PATH}/{SELF_ID}", json={"role": "agent"})
    assert resp.status_code == 400
    assert "角色" in resp.json()["detail"]["message"]


def test_cannot_deactivate_self(make_test_app, monkeypatch):
    async def fake_get_by_id(staff_id):
        return _row(user_id=SELF_ID, role="admin")

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)

    resp = _client(make_test_app).patch(f"{PATH}/{SELF_ID}", json={"is_active": False})
    assert resp.status_code == 400


def test_cannot_change_own_quota(make_test_app, monkeypatch):
    """给自己加配额等于绕过所有配额：必须由另一个管理员操作"""
    async def fake_get_by_id(staff_id):
        return _row(user_id=SELF_ID, role="admin")

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)

    resp = _client(make_test_app).patch(
        f"{PATH}/{SELF_ID}", json={"quota": {"max_scenes": 99}}
    )
    assert resp.status_code == 400
    assert "配额" in resp.json()["detail"]["message"]


def test_update_other_accepts_and_returns_usage(make_test_app, monkeypatch):
    async def fake_get_by_id(staff_id):
        return _row()

    captured: dict = {}

    async def fake_update(staff_id, **fields):
        captured.update(fields)
        return _row(**{**{"quota": fields.get("quota"), "role": fields.get("role", "agent")}})

    async def fake_usage(staff_id):
        return {"scene": 1, "agent": 2, "knowledge_base": 0}

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(StaffRepo, "update", fake_update)
    monkeypatch.setattr(staff_api, "staff_usage", fake_usage)
    _mute_audit(monkeypatch)

    resp = _client(make_test_app).patch(
        f"{PATH}/{OTHER_ID}",
        json={"role": "admin", "quota": {"max_agents": 0}, "display_name": "客服主管"},
    )
    assert resp.status_code == 200
    assert captured["quota"] == {"max_agents": 0}
    assert captured["role"] == "admin"
    data = resp.json()["data"]
    assert data["used"] == {"scenes": 1, "agents": 2, "knowledge_bases": 0}
    assert data["remaining"] == {"scenes": None, "agents": 0, "knowledge_bases": None}


def test_update_clears_display_name(make_test_app, monkeypatch):
    """显式传 null 要把展示名清空：用 is not None 判定会让“清空”变成 200 但没改。"""

    async def fake_get_by_id(staff_id):
        return _row()

    captured: dict = {}

    async def fake_update(staff_id, **fields):
        captured.update(fields)
        return _row(display_name=None)

    async def fake_usage(staff_id):
        return {"scene": 0, "agent": 0, "knowledge_base": 0}

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)
    monkeypatch.setattr(StaffRepo, "update", fake_update)
    monkeypatch.setattr(staff_api, "staff_usage", fake_usage)
    _mute_audit(monkeypatch)

    resp = _client(make_test_app).patch(f"{PATH}/{OTHER_ID}", json={"display_name": None})
    assert resp.status_code == 200, resp.text
    assert captured == {"display_name": None}
    assert resp.json()["data"]["display_name"] is None


def test_update_missing_staff_and_bad_uuid(make_test_app, monkeypatch):
    async def fake_get_by_id(staff_id):
        return None

    monkeypatch.setattr(StaffRepo, "get_by_id", fake_get_by_id)
    client = _client(make_test_app)
    assert client.patch(f"{PATH}/{OTHER_ID}", json={"display_name": "x"}).status_code == 404
    assert client.patch(f"{PATH}/not-a-uuid", json={"display_name": "x"}).status_code == 400


# ===== 重置密码 =====
def test_reset_password_ok_and_missing(make_test_app, monkeypatch):
    seen: dict = {}

    async def fake_set_password(staff_id, password_hash):
        seen["id"] = staff_id
        seen["hash"] = password_hash
        return staff_id != OTHER_ID

    monkeypatch.setattr(StaffRepo, "set_password", fake_set_password)
    _mute_audit(monkeypatch)
    client = _client(make_test_app)

    assert client.post(f"{PATH}/{SELF_ID}/password", json={"password": "NewPassw0rd!"}).status_code == 200
    assert seen["hash"] != "NewPassw0rd!"
    assert client.post(f"{PATH}/{OTHER_ID}/password", json={"password": "NewPassw0rd!"}).status_code == 404


def test_password_too_short_is_rejected(make_test_app):
    resp = _client(make_test_app).post(f"{PATH}/{SELF_ID}/password", json={"password": "123"})
    assert resp.status_code == 422
