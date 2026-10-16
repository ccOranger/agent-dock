"""
AgentRegistry：业务 Agent 类注册表
每个业务 Agent 是一个类，独立开发，注册到这里即可被路由分发
"""
import ast
import inspect
import sys
import textwrap

from app.agents.base_business_agent import BaseBusinessAgent
from app.agents.business.complaint_agent import ComplaintAgent
from app.agents.business.knowledge_agent import KnowledgeAgent
from app.agents.business.order_agent import OrderAgent
from app.core.config import settings
from app.tools import custom_catalog
from app.tools.custom.base import BaseTool

# 新增业务 Agent：只需 import 并加入此表
AGENT_REGISTRY: dict[str, type[BaseBusinessAgent]] = {
    KnowledgeAgent.name: KnowledgeAgent,
    OrderAgent.name: OrderAgent,
    ComplaintAgent.name: ComplaintAgent,
    # RefundAgent.name: RefundAgent,
}


def get_agent_class(name: str) -> type[BaseBusinessAgent] | None:
    return AGENT_REGISTRY.get(name)


def _registered_tool_names(cls: type[BaseBusinessAgent]) -> list[str]:
    """静态解析代码 Agent 的 build_tools，取回其注册的工具名。

    直接实例化会触发 memory/rag 工具连接 Neo4j/Qdrant，故用 AST 解析源码中的
    ``registry.register_tool(<X>())`` 调用：自定义工具类读其类级 ``name``（无需实例化），
    memory/rag 工厂归一为稳定标签。仅用于后台展示，解析失败返回空列表不影响主流程。
    """
    names: list[str] = []
    try:
        src = textwrap.dedent(inspect.getsource(cls.build_tools))
        tree = ast.parse(src)
    except (OSError, TypeError, SyntaxError):
        return names
    ns = getattr(sys.modules.get(cls.__module__), "__dict__", {})
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "register_tool" or not node.args:
            continue
        arg = node.args[0]
        root = arg.func if isinstance(arg, ast.Call) else arg
        sym = root.id if isinstance(root, ast.Name) else (root.attr if isinstance(root, ast.Attribute) else None)
        if not sym:
            continue
        obj = ns.get(sym)
        if isinstance(obj, type) and issubclass(obj, BaseTool) and obj.name:
            names.append(obj.name)
        elif sym == "get_memory_tool":
            names.append("memory")
        elif sym == "get_rag_tool":
            names.append("rag")
    return names


def list_agents() -> list[dict]:
    result = []
    for cls in AGENT_REGISTRY.values():
        tools = _registered_tool_names(cls)
        result.append(
            {
                "name": cls.name,
                "description": cls.description,
                "model": cls.llm_model or settings.llm_model_id,
                "system_prompt": cls.system_prompt,
                "rag_namespace": cls.rag_namespace,
                "rag_namespaces": list(cls.rag_namespaces),
                "search_all_knowledge_bases": cls.search_all_knowledge_bases,
                "mcp_tools": cls.mcp_tools,
                # build_tools 中注册的自定义工具（仅代码目录内的），供后台「自定义工具」列展示
                "custom_tool_names": [t for t in tools if custom_catalog.exists(t)],
                # 全部注册工具名（含 memory/rag），供需要完整视图的场景使用
                "registered_tools": tools,
            }
        )
    return result


# ===== 动态 Agent（DB 驱动）支持 =====
async def list_dynamic_agents(enabled_only: bool = True) -> list[dict]:
    """返回 DB 中 source=dynamic 的 Agent 摘要（供路由与后台展示）。"""
    from app.models.repositories import AgentRepo

    rows = await AgentRepo.list_dynamic(enabled_only=enabled_only)
    return [
        {
            "name": r.name,
            "description": r.description or r.name,
            "model": r.llm_model or settings.llm_model_id,
            "rag_namespace": r.rag_namespace,
            "mcp_tools": list(r.mcp_tool_names or []),
        }
        for r in rows
    ]


async def list_effective_agents() -> list[dict]:
    """代码注册表（排除后台禁用项）+ 启用中的动态 Agent 合并清单（路由分发器据此识别意图）。

    代码 Agent 默认启用：DB 无对应行时视为启用；仅当 DB 中 source=code 且 enabled=false 才剔除。
    """
    from app.models.repositories import AgentRepo

    disabled_code = {r.name for r in await AgentRepo.list_all() if r.source == "code" and not r.enabled}
    code_agents = [a for a in list_agents() if a["name"] not in disabled_code]
    return code_agents + await list_dynamic_agents(enabled_only=True)


async def resolve_business_agent(name: str, user_id: str, session_id: str | None = None):
    """按名称解析出业务 Agent 实例：优先代码注册表，其次 DB 动态 Agent。

    两类均尊重后台禁用开关：代码 Agent 若在 DB（source=code）被置 enabled=false，
    或动态 Agent enabled=false，则返回 None（调用方按未注册处理）。
    """
    from app.agents.business.dynamic_agent import DynamicBusinessAgent
    from app.models.repositories import AgentRepo

    meta = await AgentRepo.get_by_name(name)
    cls = get_agent_class(name)
    if cls is not None:
        if meta is not None and meta.source == "code" and not meta.enabled:
            return None
        return cls(user_id=user_id, session_id=session_id)

    if meta is not None and meta.source == "dynamic" and meta.enabled:
        return DynamicBusinessAgent(meta=meta, user_id=user_id, session_id=session_id)
    return None
