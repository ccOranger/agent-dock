"""RBAC 角色/菜单权限接口与 require_perm 守卫的单测（不连真库，仓储用桩）。

这里锁两件事：
- 接口层的白名单与保护：非法菜单 key / 非法 access 等级必须被过滤掉，内置角色不可删；
- require_perm 的三种走向：admin 超管旁路、已授权人员放行、无授权 403（read 不满足 write）。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import current_user, require_perm
from app.api.v1 import admin_rbac as rbac_api
from app.core.rbac import MENU_KEYS
from app.models.repositories import AuditRepo, RoleRepo

PATH = rbac_api.router.prefix  # /admin/rbac
ROLE_ID = str(uuid.uuid4())


def _role(key="viewer", name="只读访客", is_system=False, role_id=ROLE_ID):
    return SimpleNamespace(
        id=uuid.UUID(role_id),
        key=key,
        name=name,
        description=None,
        is_system=is_system,
    )


def _client(make_test_app, role="admin", user_id=str(uuid.uuid4())):
    app = make_test_app([rbac_api.router], prefix="")
    user = SimpleNamespace(
        id=user_id, username="admin", role=role, display_name="管理员", is_active=True
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


# ===== 菜单模块清单 =====
def test_menus_returns_all_modules(admin_client):
    data = admin_client.get(f"{PATH}/menus").json()["data"]
    assert {m["key"] for m in data} == MENU_KEYS
    assert all(m.get("group") for m in data)


# ===== 角色列表 =====
def test_list_roles_attaches_permissions_and_counts(make_test_app, monkeypatch):
    async def fake_list_roles():
        return [_role(key="admin", name="管理员", is_system=True), _role()]

    async def fake_perms(role_id):
        return {"takeover": "write"}

    async def fake_counts():
        return {ROLE_ID: 3}

    monkeypatch.setattr(RoleRepo, "list_roles", fake_list_roles)
    monkeypatch.setattr(RoleRepo, "get_permissions", fake_perms)
    monkeypatch.setattr(RoleRepo, "user_counts", fake_counts)

    data = _client(make_test_app).get(f"{PATH}/roles").json()["data"]
    assert [r["key"] for r in data] == ["admin", "viewer"]
    assert data[1]["permissions"] == {"takeover": "write"}
    assert data[1]["user_count"] == 3


# ===== 创建角色 =====
def test_create_role_conflict_on_existing_key(make_test_app, monkeypatch):
    async def fake_get_by_key(key):
        return _role(key=key)

    monkeypatch.setattr(RoleRepo, "get_role_by_key", fake_get_by_key)
    resp = _client(make_test_app).post(f"{PATH}/roles", json={"key": "viewer", "name": "x"})
    assert resp.status_code == 409


def test_create_role_filters_illegal_keys_and_levels(make_test_app, monkeypatch):
    captured: dict = {}

    async def fake_get_by_key(key):
        return None

    async def fake_create_role(key, name, description=None):
        return _role(key=key, name=name)

    async def fake_set_permissions(role_id, permissions):
        captured["permissions"] = permissions
        return permissions

    monkeypatch.setattr(RoleRepo, "get_role_by_key", fake_get_by_key)
    monkeypatch.setattr(RoleRepo, "create_role", fake_create_role)
    monkeypatch.setattr(RoleRepo, "set_permissions", fake_set_permissions)
    _mute_audit(monkeypatch)

    resp = _client(make_test_app).post(
        f"{PATH}/roles",
        json={
            "key": "ops",
            "name": "运营",
            "permissions": {
                "takeover": "write",
                "not_a_menu": "read",  # 非法 key → 丢弃
                "scenes": "admin",  # 非法等级 → 丢弃（schema 422 之前先在接口层过滤）
            },
        },
    )
    # scenes: "admin" 不合法，RoleCreate 的 Literal 会先拦截成 422
    assert resp.status_code == 422

    # 换成合法等级再提交，验证非法 key 被过滤、合法授权落库
    resp2 = _client(make_test_app).post(
        f"{PATH}/roles",
        json={
            "key": "ops",
            "name": "运营",
            "permissions": {"takeover": "write", "not_a_menu": "read"},
        },
    )
    assert resp2.status_code == 200
    assert captured["permissions"] == {"takeover": "write"}


# ===== 删除角色 =====
def test_delete_system_role_rejected(make_test_app, monkeypatch):
    async def fake_get_role(role_id):
        return _role(key="admin", name="管理员", is_system=True)

    monkeypatch.setattr(RoleRepo, "get_role", fake_get_role)
    resp = _client(make_test_app).delete(f"{PATH}/roles/{ROLE_ID}")
    assert resp.status_code == 400
    assert "内置角色" in resp.json()["detail"]["message"]


def test_delete_custom_role_ok(make_test_app, monkeypatch):
    async def fake_get_role(role_id):
        return _role()

    async def fake_delete(role_id):
        return True

    monkeypatch.setattr(RoleRepo, "get_role", fake_get_role)
    monkeypatch.setattr(RoleRepo, "delete_role", fake_delete)
    written = _mute_audit(monkeypatch)

    resp = _client(make_test_app).delete(f"{PATH}/roles/{ROLE_ID}")
    assert resp.status_code == 200
    assert written[0]["action"] == "admin.rbac.role_delete"


# ===== 授权矩阵整体替换 =====
def test_set_permissions_filters_illegal(make_test_app, monkeypatch):
    captured: dict = {}

    async def fake_get_role(role_id):
        return _role()

    async def fake_set_permissions(role_id, permissions):
        captured["permissions"] = permissions
        return permissions

    monkeypatch.setattr(RoleRepo, "get_role", fake_get_role)
    monkeypatch.setattr(RoleRepo, "set_permissions", fake_set_permissions)
    _mute_audit(monkeypatch)

    resp = _client(make_test_app).put(
        f"{PATH}/roles/{ROLE_ID}/permissions",
        json={"permissions": {"knowledge": "read", "ghost": "write"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"] == {"knowledge": "read"}
    assert captured["permissions"] == {"knowledge": "read"}


# ===== require_perm 守卫 =====
def _perm_app(menu_key, access):
    """构造只有一个受 require_perm 保护端点的裸应用，便于验证守卫三种走向。"""
    app = FastAPI()
    router = APIRouter()

    @router.get(f"/x/{menu_key}", dependencies=[Depends(require_perm(menu_key, access))])
    async def _endpoint():
        return {"ok": True}

    app.include_router(router)
    return app


def _perm_client(app, role, user_id=str(uuid.uuid4())):
    user = SimpleNamespace(id=user_id, username="u", role=role, is_active=True)
    app.dependency_overrides[current_user] = lambda: user
    return TestClient(app)


def test_require_perm_admin_bypass(make_test_app, monkeypatch):
    async def boom(user_id):  # admin 不该走到查库
        raise AssertionError("admin 应旁路，不应查询权限")

    monkeypatch.setattr(RoleRepo, "resolve_permissions", boom)
    client = _perm_client(_perm_app("scenes", "write"), "admin")
    assert client.get("/x/scenes").status_code == 200


def test_require_perm_denies_non_staff(make_test_app):
    client = _perm_client(_perm_app("scenes", "write"), "customer")
    assert client.get("/x/scenes").status_code == 403


def test_require_perm_granted_write_passes(make_test_app, monkeypatch):
    async def fake_resolve(user_id):
        return {"scenes": "write"}

    monkeypatch.setattr(RoleRepo, "resolve_permissions", fake_resolve)
    client = _perm_client(_perm_app("scenes", "write"), "agent")
    assert client.get("/x/scenes").status_code == 200


def test_require_perm_read_not_enough_for_write(make_test_app, monkeypatch):
    async def fake_resolve(user_id):
        return {"scenes": "read"}

    monkeypatch.setattr(RoleRepo, "resolve_permissions", fake_resolve)
    client = _perm_client(_perm_app("scenes", "write"), "agent")
    assert client.get("/x/scenes").status_code == 403


def test_require_perm_read_enough_for_read(make_test_app, monkeypatch):
    async def fake_resolve(user_id):
        return {"scenes": "read"}

    monkeypatch.setattr(RoleRepo, "resolve_permissions", fake_resolve)
    client = _perm_client(_perm_app("scenes", "read"), "agent")
    assert client.get("/x/scenes").status_code == 200


def test_require_perm_missing_module_denies(make_test_app, monkeypatch):
    async def fake_resolve(user_id):
        return {"takeover": "write"}

    monkeypatch.setattr(RoleRepo, "resolve_permissions", fake_resolve)
    client = _perm_client(_perm_app("scenes", "read"), "agent")
    assert client.get("/x/scenes").status_code == 403
