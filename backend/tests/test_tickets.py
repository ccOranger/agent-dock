"""工单独立 tickets 表：ORM/DDL 对齐、工具落表、后台增改查 API（Repo 层 mock，不依赖真实 DB）"""
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import admin_tickets as at
from app.models.entities import Ticket
from app.packs.customer_service.tools.ticket_tool import CreateTicketTool, EscalateTool, UpdateTicketTool


# ---------------------------------------------------------------- 建表 DDL 与 ORM 对齐
def test_tickets_ddl_and_orm_columns_stay_in_sync():
    """工单新增字段容易只改 ORM 忘改 init_db.sql，真到写库才 500。"""
    sql_path = Path(__file__).resolve().parents[2] / "scripts" / "init_db.sql"
    block = re.search(
        r"CREATE TABLE (?:IF NOT EXISTS )?tickets \((.*?)\n\);",
        sql_path.read_text(encoding="utf-8"),
        re.S,
    )
    assert block, "init_db.sql 里找不到 tickets 建表语句"
    ddl_cols = {
        line.strip().split()[0]
        for line in block.group(1).splitlines()
        if line.strip() and not line.strip().startswith("--")
    }
    orm_cols = {c.name for c in Ticket.__table__.columns}
    assert orm_cols == ddl_cols


# ---------------------------------------------------------------- 工具改为落 tickets 表
async def test_create_ticket_tool_writes_to_repo(monkeypatch):
    captured = {}

    async def fake_create(
        content,
        *,
        ticket_no=None,
        user_id=None,
        session_id=None,
        category="general",
        urgency="medium",
        title=None,
    ):
        captured.update({"content": content, "user_id": user_id, "category": category, "urgency": urgency})
        return SimpleNamespace(ticket_no="TK-ABC12345", status="open")

    from app.models.repositories import TicketRepo

    monkeypatch.setattr(TicketRepo, "create", fake_create)
    res = await CreateTicketTool().run(user_id="U1", content="快递延误", category="logistics", urgency="high")
    assert res["data"]["ticket_id"] == "TK-ABC12345"
    assert captured["content"] == "快递延误"
    assert captured["category"] == "logistics"
    assert captured["urgency"] == "high"


async def test_update_ticket_tool_appends(monkeypatch):
    from app.models.repositories import TicketRepo

    seen = {}

    async def fake_append(ticket_no, content):
        seen["ticket_no"] = ticket_no
        seen["content"] = content
        return SimpleNamespace(ticket_no=ticket_no, status="in_progress")

    monkeypatch.setattr(TicketRepo, "append_update", fake_append)
    res = await UpdateTicketTool().run(ticket_id="TK-ABC12345", content="订单号3444444")
    assert res["ok"] is True
    assert res["data"]["updated"] is True
    assert seen == {"ticket_no": "TK-ABC12345", "content": "订单号3444444"}


async def test_update_ticket_tool_missing_ticket(monkeypatch):
    from app.models.repositories import TicketRepo

    async def fake_append(ticket_no, content):
        return None

    monkeypatch.setattr(TicketRepo, "append_update", fake_append)
    res = await UpdateTicketTool().run(ticket_id="TK-NOPE", content="x")
    assert res["ok"] is False
    assert res["data"]["found"] is False


async def test_escalate_tool_marks_ticket_escalated(monkeypatch):
    from app.models.repositories import AuditRepo, TicketRepo

    escalated = {}

    async def fake_latest(user_id=None, session_id=None):
        return SimpleNamespace(ticket_no="TK-ABC12345")

    async def fake_set_status(ticket_no, status, note=None):
        escalated["ticket_no"] = ticket_no
        escalated["status"] = status
        return SimpleNamespace(ticket_no=ticket_no, status=status)

    async def fake_audit(**kw):
        escalated["audit"] = kw

    monkeypatch.setattr(TicketRepo, "latest_open_for", fake_latest)
    monkeypatch.setattr(TicketRepo, "set_status", fake_set_status)
    monkeypatch.setattr(AuditRepo, "write", fake_audit)

    res = await EscalateTool().run(session_id="S1", reason="客户情绪激动", user_id="U1")
    assert res["data"]["handover"] is True
    assert res["data"]["ticket_id"] == "TK-ABC12345"
    assert escalated["ticket_no"] == "TK-ABC12345"
    assert escalated["status"] == "escalated"


# ---------------------------------------------------------------- 后台增改查 API
SAMPLE_LIST = [
    {
        "id": "t-1",
        "ticket_no": "TK-ABC12345",
        "user_id": "u-c1",
        "session_id": "s-1",
        "category": "logistics",
        "urgency": "high",
        "status": "open",
        "title": "快递延误",
        "content": "快递延误投诉",
        "update_count": 0,
        "created_at": datetime(2025, 1, 1, 10, 0, 0).isoformat(),
        "updated_at": datetime(2025, 1, 1, 10, 5, 0).isoformat(),
    }
]


@pytest.fixture
def client(make_test_app):
    app = make_test_app([at.router], prefix="")
    return TestClient(make_test_app.as_role(app, "agent"))


def test_admin_list_tickets_passthrough(client, monkeypatch):
    captured = {}

    async def fake_list(*, user_id, status, category, urgency, keyword, date_from, date_to, page, page_size):
        captured.update({"status": status, "urgency": urgency, "keyword": keyword, "page": page})
        return SAMPLE_LIST, 7

    from app.models.repositories import TicketRepo

    monkeypatch.setattr(TicketRepo, "list_filtered", fake_list)
    resp = client.get(
        f"{at.router.prefix}",
        params={"status": "open", "urgency": "high", "keyword": "延误", "page": 2},
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["total"] == 7
    assert body["items"][0]["ticket_no"] == "TK-ABC12345"
    assert captured["status"] == "open"
    assert captured["keyword"] == "延误"
    assert captured["page"] == 2


def test_admin_get_ticket_detail(client, monkeypatch):
    from app.models.repositories import TicketRepo

    row = SimpleNamespace(ticket_no="TK-ABC12345")

    monkeypatch.setattr(TicketRepo, "get_by_ticket_no", lambda tn: _ret(row))
    monkeypatch.setattr(
        TicketRepo,
        "to_dict",
        lambda t, include_updates=False: {"ticket_no": t.ticket_no, "updates": []},
    )
    resp = client.get(f"{at.router.prefix}/TK-ABC12345")
    assert resp.status_code == 200
    assert resp.json()["data"]["ticket_no"] == "TK-ABC12345"


def test_admin_get_ticket_not_found(client, monkeypatch):
    from app.models.repositories import TicketRepo

    monkeypatch.setattr(TicketRepo, "get_by_ticket_no", lambda tn: _ret(None))
    resp = client.get(f"{at.router.prefix}/TK-NOPE")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == 3001


def test_admin_update_status(client, monkeypatch):
    from app.models.repositories import TicketRepo

    calls = {}

    async def fake_set_status(ticket_no, status, note=None):
        calls["ticket_no"] = ticket_no
        calls["status"] = status
        return SimpleNamespace(ticket_no=ticket_no, status=status)

    monkeypatch.setattr(TicketRepo, "set_status", fake_set_status)
    monkeypatch.setattr(
        TicketRepo,
        "to_dict",
        lambda t, include_updates=False: {"ticket_no": t.ticket_no, "status": t.status},
    )
    resp = client.patch(f"{at.router.prefix}/TK-ABC12345/status", json={"status": "resolved", "note": "已赔付"})
    assert resp.status_code == 200
    assert calls == {"ticket_no": "TK-ABC12345", "status": "resolved"}
    assert resp.json()["data"]["status"] == "resolved"


def test_admin_add_note(client, monkeypatch):
    from app.models.repositories import TicketRepo

    seen = {}

    async def fake_append(ticket_no, content):
        seen["content"] = content
        return SimpleNamespace(ticket_no=ticket_no, status="in_progress")

    monkeypatch.setattr(TicketRepo, "append_update", fake_append)
    monkeypatch.setattr(TicketRepo, "to_dict", lambda t, include_updates=False: {"ticket_no": t.ticket_no})
    resp = client.post(f"{at.router.prefix}/TK-ABC12345/notes", json={"content": "订单号3444444"})
    assert resp.status_code == 200
    assert seen["content"] == "订单号3444444"


def test_customer_forbidden_on_tickets(make_test_app):
    app = make_test_app([at.router], prefix="")
    anon = TestClient(make_test_app.as_role(app, "customer"))
    resp = anon.get(f"{at.router.prefix}")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == 2003


async def _ret(value):
    return value
