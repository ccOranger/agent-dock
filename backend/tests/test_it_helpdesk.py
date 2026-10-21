"""IT 服务台工具：资产假数据与 IT 工单工具契约。

工具不连真实 DB：TicketRepo / AuditRepo 均以 monkeypatch 替换，只校验
参数透传、metadata 来源标记（source=it_helpdesk）与返回值结构。
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.models.repositories import AuditRepo, TicketRepo
from app.tools.custom import mock_assets
from app.tools.custom.it_asset_tool import QueryItAssetTool
from app.tools.custom.it_ticket_tool import (
    CreateItTicketTool,
    EscalateItTool,
    UpdateItTicketTool,
)


def test_ticket_to_dict_exposes_metadata():
    """to_dict 需透出 metadata，保留创建来源等扩展信息。"""
    row = SimpleNamespace(
        id="t-1",
        ticket_no="TK-IT0001",
        user_id=None,
        session_id=None,
        category="it_network",
        urgency="high",
        status="open",
        title="财务部 5F 无法连接 Wi-Fi",
        content="整层无法连接 Wi-Fi",
        updates=[],
        metadata_={"source": "it_helpdesk"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    data = TicketRepo.to_dict(row, include_updates=True)
    assert data["metadata"] == {"source": "it_helpdesk"}
    assert data["updates"] == []


# ===== 资产假数据 =====
def test_find_assets_by_asset_no_returns_copy():
    assets = mock_assets.find_assets("IT-LT-1001")
    assert len(assets) == 1
    assert assets[0]["owner"] == "张伟"
    assert assets[0]["sn"] == "5CD4K3X7Q2"
    # 返回副本：调用方改动不污染进程内快照
    assets[0]["owner"] = "被改坏"
    assert mock_assets.find_assets("IT-LT-1001")[0]["owner"] == "张伟"


def test_find_assets_supports_alias_owner_and_case_insensitive():
    assert len(mock_assets.find_assets("电脑")) == 3  # 笔记本 2 + 台式机 1
    assert {a["owner"] for a in mock_assets.find_assets("张伟")} == {"张伟"}
    assert len(mock_assets.find_assets("张伟")) == 2
    assert mock_assets.find_assets("it-lt-1001")[0]["asset_no"] == "IT-LT-1001"
    assert mock_assets.find_assets("   ") == []
    assert mock_assets.find_assets("不存在的编号") == []


def test_find_assets_respects_limit():
    # “Dell”命中 2 条（笔记本 + 显示器），limit 截断生效
    assert len(mock_assets.find_assets("Dell")) == 2
    assert len(mock_assets.find_assets("Dell", limit=1)) == 1


# ===== 资产工具 =====
async def test_asset_tool_hit_and_miss():
    tool = QueryItAssetTool()
    hit = await tool.run(keyword="IT-PRN-4001")
    assert hit["ok"] is True
    assert hit["data"]["count"] == 1
    assert hit["data"]["assets"][0]["type"] == "打印机"

    miss = await tool.run(keyword="不存在的编号")
    assert miss["ok"] is False
    assert "未查询到匹配资产" in miss["error"]


# ===== IT 工单工具 =====
async def test_create_it_ticket_marks_source_and_passes_fields(monkeypatch):
    captured: dict = {}

    async def fake_create(**kw):
        captured.update(kw)
        return SimpleNamespace(ticket_no="TK-IT9999")

    monkeypatch.setattr(TicketRepo, "create", fake_create)
    result = await CreateItTicketTool().run(
        user_id="zhangsan",
        content="笔记本开不了机，一直黑屏",
        session_id="s-1",
        category="it_hardware",
        urgency="high",
    )
    assert result == {"ok": True, "data": {"ticket_id": "TK-IT9999", "status": "created"}}
    assert captured["metadata_"] == {"source": "it_helpdesk"}
    assert (captured["category"], captured["urgency"]) == ("it_hardware", "high")
    assert (captured["user_id"], captured["session_id"]) == ("zhangsan", "s-1")


async def test_update_it_ticket_found_and_missing(monkeypatch):
    async def fake_append(ticket_no, content):
        assert ticket_no == "TK-IT0001"
        assert content == "补充：报错代码 0x8007007E"
        return SimpleNamespace(ticket_no=ticket_no, status="in_progress")

    monkeypatch.setattr(TicketRepo, "append_update", fake_append)
    ok = await UpdateItTicketTool().run(ticket_id="TK-IT0001", content="补充：报错代码 0x8007007E")
    assert ok["ok"] is True and ok["data"]["updated"] is True

    async def fake_missing(ticket_no, content):
        return None

    monkeypatch.setattr(TicketRepo, "append_update", fake_missing)
    miss = await UpdateItTicketTool().run(ticket_id="TK-404", content="x")
    assert miss["ok"] is False and miss["data"]["found"] is False


async def test_escalate_it_marks_ticket_and_audit(monkeypatch):
    status_calls: list[tuple] = []
    audit: dict = {}

    async def fake_latest(user_id=None, session_id=None):
        return SimpleNamespace(ticket_no="TK-IT0002")

    async def fake_set_status(ticket_no, status, note=None):
        status_calls.append((ticket_no, status, note))

    async def fake_audit(**kw):
        audit.update(kw)

    monkeypatch.setattr(TicketRepo, "latest_open_for", fake_latest)
    monkeypatch.setattr(TicketRepo, "set_status", fake_set_status)
    monkeypatch.setattr(AuditRepo, "write", fake_audit)

    result = await EscalateItTool().run(session_id="s-1", user_id="zhangsan", reason="整层断网")
    assert result["ok"] is True
    assert result["data"]["ticket_id"] == "TK-IT0002"
    assert result["data"]["handover"] is True
    assert status_calls and status_calls[0][1] == "escalated"
    assert audit["action"] == "escalate"
    assert audit["payload"]["source"] == "it_helpdesk"
    # 演示账号名不是 UUID：审计写入降级为空，但不阻断升级
    assert audit["user_id"] is None


async def test_escalate_it_without_open_ticket(monkeypatch):
    audit: dict = {}

    async def fake_latest(user_id=None, session_id=None):
        return None

    async def fake_audit(**kw):
        audit.update(kw)

    monkeypatch.setattr(TicketRepo, "latest_open_for", fake_latest)
    monkeypatch.setattr(AuditRepo, "write", fake_audit)

    result = await EscalateItTool().run(session_id="s-2", reason="账号被盗")
    assert result["data"]["ticket_id"] is None
    assert audit["payload"]["ticket_no"] is None
