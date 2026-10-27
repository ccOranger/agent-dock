"""后台接入方管理：场景授权、Key 签发/吊销与删除保护。

全部走 Repo mock，不连真实库；鉴权与访客命名空间的数据库行为由
scripts/verify_schema.py 与实际端到端验证覆盖。
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_integrations as integrations_api
from app.models.repositories import AuditRepo, IntegrationRepo, SceneRepo

PATH = integrations_api.router.prefix  # /admin/integrations


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([integrations_api.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


def _integration(**kw) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "name": "宿主站 A",
        "description": "商城官网",
        "status": "active",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _install(monkeypatch, integration, detail=None) -> None:
    async def get(integration_id):
        return integration

    async def repo_detail(integration_id):
        return detail or {
            "id": str(integration.id),
            "name": integration.name,
            "description": integration.description,
            "status": integration.status,
            "key_count": 0,
            "keys": [],
            "scenes": [],
        }

    async def audit(*args, **kwargs):
        return None

    monkeypatch.setattr(IntegrationRepo, "get", get)
    monkeypatch.setattr(IntegrationRepo, "detail", repo_detail)
    monkeypatch.setattr(AuditRepo, "write", audit)


def test_create_integration_authorizes_scenes(admin_client, monkeypatch):
    scene_id = str(uuid.uuid4())
    created: dict = {}

    async def existing_ids(ids):
        return {str(x) for x in ids}

    async def create(name, description=None, scene_ids=None):
        created.update(name=name, description=description, scene_ids=scene_ids)
        return SimpleNamespace(id=uuid.uuid4())

    async def repo_detail(integration_id):
        return {"id": "i-1", "name": created["name"], "scenes": [], "keys": []}

    async def audit(*args, **kwargs):
        return None

    monkeypatch.setattr(SceneRepo, "existing_ids", existing_ids)
    monkeypatch.setattr(IntegrationRepo, "create", create)
    monkeypatch.setattr(IntegrationRepo, "detail", repo_detail)
    monkeypatch.setattr(AuditRepo, "write", audit)

    resp = admin_client.post(
        PATH, json={"name": " 宿主站 A ", "description": "官网", "scene_ids": [scene_id]}
    )
    assert resp.status_code == 200
    assert created == {"name": "宿主站 A", "description": "官网", "scene_ids": [scene_id]}


def test_create_integration_rejects_unknown_scene(admin_client, monkeypatch):
    async def existing_ids(ids):
        return set()

    monkeypatch.setattr(SceneRepo, "existing_ids", existing_ids)
    resp = admin_client.post(PATH, json={"name": "宿主站 A", "scene_ids": [str(uuid.uuid4())]})
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == 1001


def test_issue_key_returns_plaintext_once(admin_client, monkeypatch):
    integration = _integration()
    _install(monkeypatch, integration)
    captured: dict = {}

    async def create_key(integration_id, raw, hint):
        captured.update(integration_id=str(integration_id), raw=raw, hint=hint)
        return SimpleNamespace(id=uuid.uuid4(), hint=hint)

    monkeypatch.setattr(IntegrationRepo, "create_key", create_key)

    resp = admin_client.post(f"{PATH}/{integration.id}/keys")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["api_key"].startswith("sk_")
    assert data["hint"] == captured["hint"] == captured["raw"][-6:]
    assert data["api_key"] == captured["raw"]
    assert captured["integration_id"] == str(integration.id)
    # 响应里只有明文尾号，不回显哈希
    assert "key_hash" not in data


def test_revoke_key_not_found(admin_client, monkeypatch):
    integration = _integration()
    _install(monkeypatch, integration)

    async def revoke_key(integration_id, key_id):
        return False

    monkeypatch.setattr(IntegrationRepo, "revoke_key", revoke_key)
    resp = admin_client.delete(f"{PATH}/{integration.id}/keys/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


def test_delete_integration_blocked_by_visitors(admin_client, monkeypatch):
    integration = _integration()
    _install(monkeypatch, integration)

    async def has_visitors(integration_id):
        return True

    monkeypatch.setattr(IntegrationRepo, "has_visitors", has_visitors)
    resp = admin_client.delete(f"{PATH}/{integration.id}")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == 3002


def test_update_integration_without_scene_ids_keeps_authorization(admin_client, monkeypatch):
    integration = _integration()
    _install(monkeypatch, integration)
    captured: dict = {}

    async def update(integration_id, scene_ids=None, **fields):
        captured.update(integration_id=str(integration_id), scene_ids=scene_ids, fields=fields)
        return integration

    monkeypatch.setattr(IntegrationRepo, "update", update)
    resp = admin_client.put(f"{PATH}/{integration.id}", json={"name": "宿主站 B"})
    assert resp.status_code == 200
    assert captured["fields"]["name"] == "宿主站 B"
    # 未传 scene_ids：保持原授权，不做全量清空
    assert captured["scene_ids"] is None
