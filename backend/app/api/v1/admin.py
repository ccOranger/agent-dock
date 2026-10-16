"""管理接口：Agent（代码 + 动态）/ MCP 工具 / 指标"""
import asyncio

from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agents.registry import AGENT_REGISTRY, list_agents
from app.api.deps import require_admin, require_staff
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import AgentRepo, AuditRepo, KnowledgeBaseRepo, ToolRepo
from app.schemas.api import (
    AgentToggleRequest,
    DynamicAgentCreate,
    DynamicAgentUpdate,
    MCPServerCreate,
    MCPServerUpdate,
)
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin", tags=["admin"])


# ===== Agent 列表 / 详情 / 开关 =====
@router.get("/agents", response_model=ApiResponse[list])
async def agents(user=Depends(require_admin)):
    """代码注册表 + DB 动态 Agent 合并返回（代码 Agent 的 MCP 绑定只读）"""
    db_rows = {r.name: r for r in await AgentRepo.list_all()}
    result = []
    # 1) 代码 Agent（定义来自 Python 类，DB 仅存开关/展示）
    for item in list_agents():
        meta = db_rows.get(item["name"])
        result.append(
            {
                **item,
                "source": "code",
                "enabled": meta.enabled if meta else True,
                "display_name": (meta.display_name if meta and meta.display_name else item["name"]),
                "system_prompt": None,
            }
        )
    # 2) 动态 Agent（定义全部来自 DB）
    for name, meta in db_rows.items():
        if meta.source != "dynamic":
            continue
        result.append(
            {
                "name": meta.name,
                "description": meta.description or meta.name,
                "model": meta.llm_model or "",
                "rag_namespace": meta.rag_namespace,
                "mcp_tools": list(meta.mcp_tool_names or []),
                "source": "dynamic",
                "enabled": meta.enabled,
                "display_name": meta.display_name or meta.name,
                "system_prompt": meta.system_prompt,
                "rag_namespaces": list(meta.rag_namespaces or []),
                "search_all_knowledge_bases": meta.search_all_knowledge_bases,
                "sort_order": meta.sort_order,
            }
        )
    return ApiResponse(data=result)


@router.get("/agents/{name}", response_model=ApiResponse[dict])
async def agent_detail(name: str, user=Depends(require_admin)):
    meta = await AgentRepo.get_by_name(name)
    if meta is not None and meta.source == "dynamic":
        return ApiResponse(
            data={
                "name": meta.name,
                "description": meta.description or meta.name,
                "model": meta.llm_model or "",
                "rag_namespace": meta.rag_namespace,
                "mcp_tools": list(meta.mcp_tool_names or []),
                "source": "dynamic",
                "enabled": meta.enabled,
                "display_name": meta.display_name or meta.name,
                "system_prompt": meta.system_prompt,
                "rag_namespaces": list(meta.rag_namespaces or []),
                "search_all_knowledge_bases": meta.search_all_knowledge_bases,
            }
        )
    for item in list_agents():
        if item["name"] == name:
            return ApiResponse(data={**item, "source": "code", "enabled": meta.enabled if meta else True})
    raise NotFound("Agent 不存在")


@router.post("/agents", response_model=ApiResponse[dict])
async def create_agent(body: DynamicAgentCreate, user=Depends(require_admin)):
    """创建动态 Agent（不允许与代码 Agent 或已有 Agent 重名）"""
    if body.name in AGENT_REGISTRY:
        raise Conflict(f"名称与代码 Agent 冲突: {body.name}")
    if await AgentRepo.get_by_name(body.name) is not None:
        raise Conflict(f"Agent 已存在: {body.name}")
    await _validate_mcp_bindings(body.mcp_tool_names)
    if body.rag_namespace and await KnowledgeBaseRepo.get(body.rag_namespace) is None:
        raise ParamError(f"知识库 namespace 不存在: {body.rag_namespace}")
    await _validate_rag_namespaces(body.rag_namespaces)

    row = await AgentRepo.create_dynamic(
        name=body.name,
        display_name=body.display_name,
        description=body.description,
        system_prompt=body.system_prompt,
        llm_model=body.llm_model,
        rag_namespace=body.rag_namespace,
        rag_namespaces=body.rag_namespaces,
        search_all_knowledge_bases=body.search_all_knowledge_bases,
        mcp_tool_names=body.mcp_tool_names,
        enabled=body.enabled,
        sort_order=body.sort_order,
    )
    await AuditRepo.write(action="admin.agent.create", user_id=str(user.id), target_type="agent", target_id=row.name)
    return ApiResponse(data={"name": row.name})


@router.put("/agents/{name}", response_model=ApiResponse[dict])
async def update_agent(name: str, body: DynamicAgentUpdate, user=Depends(require_admin)):
    meta = await AgentRepo.get_by_name(name)
    if meta is None or meta.source != "dynamic":
        raise ParamError("仅动态 Agent（source=dynamic）可编辑")
    if body.mcp_tool_names is not None:
        await _validate_mcp_bindings(body.mcp_tool_names)
    if body.rag_namespace and await KnowledgeBaseRepo.get(body.rag_namespace) is None:
        raise ParamError(f"知识库 namespace 不存在: {body.rag_namespace}")
    if body.rag_namespaces is not None:
        await _validate_rag_namespaces(body.rag_namespaces)

    fields = body.model_dump(exclude_unset=True)
    if fields:
        updated = await AgentRepo.update_dynamic(name, **fields)
        if updated is None:
            raise NotFound("Agent 不存在")
    await AuditRepo.write(action="admin.agent.update", user_id=str(user.id), target_type="agent", target_id=name)
    return ApiResponse(data={"name": name})


@router.delete("/agents/{name}", response_model=ApiResponse)
async def delete_agent(name: str, user=Depends(require_admin)):
    meta = await AgentRepo.get_by_name(name)
    if meta is None:
        raise NotFound("Agent 不存在")
    if meta.source != "dynamic":
        raise ParamError("代码 Agent 不可删除，仅可在后台禁用")
    await AgentRepo.delete_dynamic(name)
    await AuditRepo.write(action="admin.agent.delete", user_id=str(user.id), target_type="agent", target_id=name)
    return ApiResponse()


@router.patch("/agents/{name}/toggle", response_model=ApiResponse)
async def toggle_agent(name: str, body: AgentToggleRequest, user=Depends(require_admin)):
    meta = await AgentRepo.get_by_name(name)
    if meta is None:
        raise NotFound("Agent 未登记")
    await AgentRepo.set_enabled(name, body.enabled)
    return ApiResponse()


# ===== MCP 服务管理 =====
@router.get("/mcp-servers", response_model=ApiResponse[list])
async def list_mcp_servers(user=Depends(require_staff)):
    rows = await ToolRepo.list_by_type("mcp")
    # 反查每个服务被哪些 Agent 绑定（代码 Agent 的绑定来自类属性）
    bound_code: dict[str, list[str]] = {}
    for cls in AGENT_REGISTRY.values():
        for t in cls.mcp_tools:
            bound_code.setdefault(t, []).append(cls.name)
    dyn_rows = {r.name: list(r.mcp_tool_names or []) for r in await AgentRepo.list_dynamic(enabled_only=False)}
    dyn_back: dict[str, list[str]] = {}
    for agent_name, tools in dyn_rows.items():
        for t in tools:
            dyn_back.setdefault(t, []).append(agent_name)

    data = []
    for r in rows:
        used = sorted(set(bound_code.get(r.name, []) + dyn_back.get(r.name, [])))
        data.append(
            {
                "id": str(r.id),
                "name": r.name,
                "type": r.type,
                "enabled": r.enabled,
                "config": r.config,
                "used_by_agents": used,
            }
        )
    return ApiResponse(data=data)


@router.post("/mcp-servers", response_model=ApiResponse[dict])
async def create_mcp_server(body: MCPServerCreate, user=Depends(require_admin)):
    if await ToolRepo.get_by_name(body.name) is not None:
        raise Conflict(f"MCP 服务已存在: {body.name}")
    config = _build_mcp_config(body)
    row = await ToolRepo.create_mcp(name=body.name, config=config)
    await AuditRepo.write(action="admin.mcp.create", user_id=str(user.id), target_type="tool", target_id=row.name)
    return ApiResponse(data={"id": str(row.id), "name": row.name})


@router.put("/mcp-servers/{server_id}", response_model=ApiResponse[dict])
async def update_mcp_server(server_id: str, body: MCPServerUpdate, user=Depends(require_admin)):
    row = await ToolRepo.get(server_id)
    if row is None or row.type != "mcp":
        raise NotFound("MCP 服务不存在")
    merged = MCPServerCreate(
        name=row.name,
        **{**_config_to_body(row.config), **body.model_dump(exclude_unset=True)},
    )
    config = _build_mcp_config(merged)
    fields = {"config": config}
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    await ToolRepo.update(server_id, **fields)
    await AuditRepo.write(action="admin.mcp.update", user_id=str(user.id), target_type="tool", target_id=row.name)
    return ApiResponse(data={"id": str(row.id), "name": row.name})


@router.delete("/mcp-servers/{server_id}", response_model=ApiResponse)
async def delete_mcp_server(server_id: str, user=Depends(require_admin)):
    row = await ToolRepo.get(server_id)
    if row is None or row.type != "mcp":
        raise NotFound("MCP 服务不存在")
    # 若被动态 Agent 绑定则拒绝，避免悬空引用
    for agent in await AgentRepo.list_dynamic(enabled_only=False):
        if row.name in (agent.mcp_tool_names or []):
            raise ParamError(f"MCP 服务被 Agent 引用，无法删除: {agent.name}")
    await ToolRepo.delete(server_id)
    await AuditRepo.write(action="admin.mcp.delete", user_id=str(user.id), target_type="tool", target_id=row.name)
    return ApiResponse()


@router.post("/mcp-servers/test", response_model=ApiResponse[dict])
async def test_mcp_server(body: MCPServerCreate, user=Depends(require_admin)):
    """连通性测试：尝试连接并列出远端提供的工具（不落库，纯探测）。"""
    from app.tools.mcp_loader import _build_mcp_server_source

    cfg = {"name": body.name, "config": _build_mcp_config(body)}
    source = _build_mcp_server_source(cfg)
    if not source:
        raise ParamError("缺少 endpoint/command，无法测试")
    try:
        from hello_agents.protocols.mcp.client import MCPClient

        async with MCPClient(source) as client:
            tools = await client.list_tools()
        return ApiResponse(data={"ok": True, "tool_count": len(tools), "tools": tools})
    except Exception as e:  # noqa: BLE001
        return ApiResponse(code=0, data={"ok": False, "error": str(e)})


@router.get("/mcp-servers/{server_id}/tools", response_model=ApiResponse[dict])
async def list_mcp_server_tools(server_id: str, user=Depends(require_staff)):
    """连接该 MCP 服务并返回其全部工具列表（按已存配置探测，不落库）。"""
    row = await ToolRepo.get(server_id)
    if row is None or row.type != "mcp":
        raise NotFound("MCP 服务不存在")
    from app.tools.mcp_loader import _build_mcp_server_source

    source = _build_mcp_server_source({"name": row.name, "config": row.config or {}})
    if not source:
        raise ParamError("缺少 endpoint/command，无法连接")
    timeout = int((row.config or {}).get("timeout_seconds") or 15)
    try:
        from hello_agents.protocols.mcp.client import MCPClient

        async def _fetch() -> list:
            async with MCPClient(source) as client:
                return await client.list_tools()

        tools = await asyncio.wait_for(_fetch(), timeout=timeout)
        items = [
            {
                "name": t.get("name"),
                "description": t.get("description") or "",
                "input_schema": t.get("input_schema") or {},
            }
            for t in tools
        ]
        return ApiResponse(data={"ok": True, "server_name": row.name, "tools": items})
    except Exception as e:  # noqa: BLE001
        return ApiResponse(code=0, data={"ok": False, "error": str(e) or "连接失败"})


# ===== 工具（保留旧接口） =====
@router.get("/tools", response_model=ApiResponse[list])
async def tools(user=Depends(require_staff)):
    rows = await ToolRepo.list_all()
    return ApiResponse(
        data=[
            {"id": str(r.id), "name": r.name, "type": r.type, "enabled": r.enabled}
            for r in rows
        ]
    )


@router.get("/metrics")
async def metrics(user=Depends(require_admin)):
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ===== 内部辅助 =====
async def _validate_mcp_bindings(names: list[str]) -> None:
    """校验绑定的 MCP 工具均存在且启用。"""
    if not names:
        return
    existing = {r.name: r for r in await ToolRepo.list_by_type("mcp")}
    for n in names:
        row = existing.get(n)
        if row is None:
            raise ParamError(f"MCP 工具不存在: {n}")
        if not row.enabled:
            raise ParamError(f"MCP 工具已禁用: {n}")


async def _validate_rag_namespaces(names: list[str]) -> None:
    """校验显式多知识库的每个 namespace 均存在（启用与否不强制，交由检索时过滤）。"""
    if not names:
        return
    existing = {r.namespace for r in await KnowledgeBaseRepo.list_all()}
    for n in names:
        if n not in existing:
            raise ParamError(f"知识库 namespace 不存在: {n}")


def _build_mcp_config(body: MCPServerCreate) -> dict:
    """将 MCP 服务请求体归一化为 tools.config 存储结构。"""
    config: dict = {"transport": body.transport, "timeout_seconds": body.timeout_seconds}
    if body.transport == "stdio":
        config["command"] = body.command
        config["args"] = body.args or []
    else:
        config["endpoint"] = body.endpoint
        if body.headers:
            config["headers"] = body.headers
        if body.auth:
            config["auth"] = body.auth
    return config


def _config_to_body(config: dict) -> dict:
    """从存储的 config 还原为可回填写的请求字段（用于 PUT 合并）。"""
    return {
        "transport": config.get("transport", "sse"),
        "endpoint": config.get("endpoint"),
        "command": config.get("command"),
        "args": config.get("args") or [],
        "headers": config.get("headers"),
        "auth": config.get("auth"),
        "timeout_seconds": config.get("timeout_seconds", 15),
        "enabled": True,
    }

