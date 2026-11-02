"""自定义工具目录：代码托管 BaseTool 的可发现清单。

设计要点（见「自定义工具管理功能」方案）：
- 工具实现在 app/tools/custom/ 下，本模块集中登记并暴露 name/description/parameters
  （parameters 即 JSON Schema），供后台扫描展示、勾选登记到 tools 表（type=custom），
  再由动态 Agent 按名绑定。
- 新增工具：实现 BaseTool 子类，并在 _TOOL_CLASSES 中补一行。
- 所有工具均为无参构造（会话上下文 user_id/session_id 由 orchestrator 执行时按参数
  声明自动注入），故 build() 直接 cls() 实例化。
- 本模块只托管「代码目录工具」；后台在线编写源码的动态工具走 tool_code_loader，
  两者由 custom_loader.register_custom_tools 统一装配（同名时代码目录优先）。
"""
from app.tools.custom.base import BaseTool
from app.tools.custom.echo_tool import EchoTool
from app.tools.custom.it_asset_tool import QueryItAssetTool
from app.tools.custom.it_ticket_tool import CreateItTicketTool, EscalateItTool, UpdateItTicketTool
from app.tools.custom.logistics_tool import QueryLogisticsTool
from app.tools.custom.order_tool import ListOrdersTool, ModifyOrderTool, QueryOrderTool
from app.tools.custom.refund_tool import QueryRefundTool, SubmitRefundTool
from app.tools.custom.ticket_tool import CreateTicketTool, EscalateTool, UpdateTicketTool

# 代码托管的全部自定义工具：name 由类属性声明，全局唯一
_TOOL_CLASSES: tuple[type[BaseTool], ...] = (
    EchoTool,
    QueryOrderTool,
    ListOrdersTool,
    ModifyOrderTool,
    QueryLogisticsTool,
    SubmitRefundTool,
    QueryRefundTool,
    CreateTicketTool,
    UpdateTicketTool,
    EscalateTool,
    CreateItTicketTool,
    UpdateItTicketTool,
    EscalateItTool,
    QueryItAssetTool,
)

_tool_names = [cls.name for cls in _TOOL_CLASSES]
_duplicates = sorted({n for n in _tool_names if _tool_names.count(n) > 1})
if _duplicates:
    raise RuntimeError(f"自定义工具名称重复: {_duplicates}")

# name -> 工具类（与 tools.name / MCP 名共用唯一约束）
CATALOG: dict[str, type[BaseTool]] = {cls.name: cls for cls in _TOOL_CLASSES}


def exists(name: str) -> bool:
    return name in CATALOG


def build(name: str) -> BaseTool | None:
    """按名实例化目录中的工具；未登记返回 None。"""
    cls = CATALOG.get(name)
    return cls() if cls is not None else None


def spec_of(name: str) -> dict:
    """按名取单个工具的元信息；未登记返回 ok=False（与 tool_code_loader.preview 同构）。"""
    cls = CATALOG.get(name)
    if cls is None:
        return {"ok": False, "errors": [f"工具不在代码目录: {name}"]}
    return {
        "ok": True,
        "errors": [],
        "name": cls.name,
        "description": cls.description or "",
        "parameters": cls.parameters or {},
        "timeout": getattr(cls, "timeout", None),
    }


def list_specs() -> list[dict]:
    """返回代码目录全部工具的名称/描述/参数 Schema（供后台扫描展示与登记）。"""
    return [
        {
            "name": cls.name,
            "description": cls.description or "",
            "parameters": cls.parameters or {},
        }
        for cls in CATALOG.values()
    ]
