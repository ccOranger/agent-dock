"""运营台「场景包」接口：聚合 manifest 资产 + 整体启停（Repo/DB mock）。

约定：
- 包启用 = manifest.enabled 且包内至少一个代码 Agent 启用（DB 无行视为启用）；
- PATCH 只批量写 agents.enabled，不新增包设置表；
- manifest 停用的包不可运行时开启，返回 409。
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_packs as ap
from app.models.repositories import AgentRepo
from app.packs import loader

PATH = ap.router.prefix  # /admin/packs


def _agent_row(name, *, enabled=True, source="code", display_name=None):
    return SimpleNamespace(
        name=name,
        enabled=enabled,
        source=source,
        display_name=display_name or name,
    )


class _FakeAgentDB:
    """内存版 agents 表：list_all/create/set_enabled_many 共享同一份状态。"""

    def __init__(self, rows=None):
        self.rows = {r.name: r for r in (rows or [])}
        self.created: list[str] = []

    async def list_all(self):
        return list(self.rows.values())

    async def create(self, name, display_name=None, description=None):
        row = _agent_row(name, display_name=display_name)
        self.rows[name] = row
        self.created.append(name)
        return row

    async def set_enabled_many(self, names, enabled):
        hit = 0
        for name in names:
            if name in self.rows:
                self.rows[name].enabled = enabled
                hit += 1
        return hit


def _install_db(monkeypatch, db: _FakeAgentDB) -> None:
    monkeypatch.setattr(AgentRepo, "list_all", db.list_all)
    monkeypatch.setattr(AgentRepo, "create", db.create)
    monkeypatch.setattr(AgentRepo, "set_enabled_many", db.set_enabled_many)


def _pack(items: list[dict], name: str) -> dict:
    return next(i for i in items if i["name"] == name)


@pytest.fixture
def admin_client(make_test_app):
    app = make_test_app([ap.router], prefix="")
    return TestClient(make_test_app.as_role(app, "admin"))


# ===== GET：聚合全部包 =====
def test_list_packs_aggregates_manifest_assets(admin_client, monkeypatch):
    _install_db(monkeypatch, _FakeAgentDB())
    resp = admin_client.get(PATH)
    assert resp.status_code == 200
    items = resp.json()["data"]
    assert {i["name"] for i in items} == {"customer_service", "it_helpdesk"}

    cs = _pack(items, "customer_service")
    assert cs["manifest_enabled"] is True and cs["enabled"] is True
    assert cs["features"] == ["tickets"]
    assert {a["name"] for a in cs["agents"]} == {"knowledge_qa", "order_service", "complaint_handler"}
    assert len(cs["tools"]) == 9
    assert cs["knowledge_namespaces"] == ["cs_default", "cs_order", "cs_after_sales"]

    it = _pack(items, "it_helpdesk")
    assert it["manifest_enabled"] is True and it["enabled"] is True
    assert it["features"] == ["tickets"]
    assert {a["name"] for a in it["agents"]} == {"it_knowledge", "it_incident", "it_asset"}
    assert {t["name"] for t in it["tools"]} == {
        "create_it_ticket",
        "update_it_ticket",
        "escalate_it",
        "query_it_asset",
    }
    assert it["knowledge_namespaces"] == ["it_default", "it_network", "it_assets"]


def test_pack_enabled_is_any_agent_enabled(admin_client, monkeypatch):
    """只停用部分 Agent 时包仍启用；全部停用才算包停用。"""
    _install_db(monkeypatch, _FakeAgentDB([_agent_row("order_service", enabled=False)]))
    cs = _pack(admin_client.get(PATH).json()["data"], "customer_service")
    assert cs["enabled"] is True

    _install_db(
        monkeypatch,
        _FakeAgentDB(
            [
                _agent_row("knowledge_qa", enabled=False),
                _agent_row("order_service", enabled=False),
                _agent_row("complaint_handler", enabled=False),
            ]
        ),
    )
    cs = _pack(admin_client.get(PATH).json()["data"], "customer_service")
    assert cs["enabled"] is False
    assert cs["manifest_enabled"] is True


# ===== PATCH：整体启停 =====
def test_toggle_pack_disables_all_agents(admin_client, monkeypatch):
    db = _FakeAgentDB([_agent_row(n) for n in ("knowledge_qa", "order_service", "complaint_handler")])
    _install_db(monkeypatch, db)

    resp = admin_client.patch(f"{PATH}/customer_service", json={"enabled": False})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is False
    assert all(a["enabled"] is False for a in data["agents"])
    # 工具随 manifest 装载，停用包不卸载工具
    assert len(data["tools"]) == 9
    assert db.created == []


def test_toggle_pack_enable_creates_missing_rows(admin_client, monkeypatch):
    """开启时先补齐缺失的 agents 行，再批量置启用，避免“无行=默认启用”掩盖开关。"""
    db = _FakeAgentDB([_agent_row("order_service", enabled=False)])
    _install_db(monkeypatch, db)

    resp = admin_client.patch(f"{PATH}/customer_service", json={"enabled": True})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["enabled"] is True
    assert all(a["enabled"] is True for a in data["agents"])
    assert set(db.created) == {"knowledge_qa", "complaint_handler"}


def test_toggle_it_pack_roundtrip(admin_client, monkeypatch):
    names = ("it_knowledge", "it_incident", "it_asset")
    db = _FakeAgentDB([_agent_row(n) for n in names])
    _install_db(monkeypatch, db)

    off = admin_client.patch(f"{PATH}/it_helpdesk", json={"enabled": False}).json()["data"]
    assert off["enabled"] is False
    assert all(db.rows[n].enabled is False for n in names)

    on = admin_client.patch(f"{PATH}/it_helpdesk", json={"enabled": True}).json()["data"]
    assert on["enabled"] is True
    assert all(db.rows[n].enabled is True for n in names)


def test_toggle_manifest_disabled_pack_conflicts(admin_client, monkeypatch):
    retired = SimpleNamespace(
        name="retired_pack",
        display_name="已下线包",
        version="0.1.0",
        enabled=False,
        features=(),
        agents=(),
        tools=(),
        knowledge_namespaces=(),
    )
    monkeypatch.setattr(loader, "scan_manifests", lambda: [retired])
    _install_db(monkeypatch, _FakeAgentDB())

    # 停用操作幂等放行（包本来就是停的）
    assert admin_client.patch(f"{PATH}/retired_pack", json={"enabled": False}).status_code == 200
    # 开启被拒绝：需改 manifest 后重启
    resp = admin_client.patch(f"{PATH}/retired_pack", json={"enabled": True})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == 3002


def test_toggle_unknown_pack_returns_404(admin_client, monkeypatch):
    _install_db(monkeypatch, _FakeAgentDB())
    resp = admin_client.patch(f"{PATH}/ghost_pack", json={"enabled": True})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


# ===== RBAC =====
def test_staff_can_list_packs(make_test_app, monkeypatch):
    _install_db(monkeypatch, _FakeAgentDB())
    app = make_test_app([ap.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "agent"))
    assert client.get(PATH).status_code == 200


def test_customer_forbidden_on_list(make_test_app):
    app = make_test_app([ap.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "customer"))
    resp = client.get(PATH)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003


def test_agent_forbidden_on_toggle(make_test_app):
    app = make_test_app([ap.router], prefix="")
    client = TestClient(make_test_app.as_role(app, "agent"))
    resp = client.patch(f"{PATH}/it_helpdesk", json={"enabled": False})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003
