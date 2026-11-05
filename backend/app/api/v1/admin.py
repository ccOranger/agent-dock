"""管理接口：Agent（代码 + 动态）/ MCP 工具 / 指标"""
import asyncio
import inspect
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from app.agents.registry import AGENT_REGISTRY, list_agents
from app.api.deps import require_perm, require_staff
from app.core.exceptions import Conflict, NotFound, ParamError
from app.models.repositories import AgentRepo, AuditRepo, KnowledgeBaseRepo, ToolRepo
from app.schemas.api import (
    AgentToggleRequest,
    CustomToolCodeUpdate,
    CustomToolDynamicCreate,
    CustomToolRegister,
    CustomToolTestRunRequest,
    CustomToolToggle,
    CustomToolValidateRequest,
    DynamicAgentCreate,
    DynamicAgentUpdate,
    MCPServerCreate,
    MCPServerUpdate,
)
from app.schemas.common import ApiResponse
from app.schemas.scene import DEFAULT_ASSISTANT_NAME
from app.services.quota_service import KIND_AGENT, create_guard
from app.tools import custom_catalog, tool_code_loader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ===== Agent 列表 / 详情 / 开关 =====
@router.get("/agents", response_model=ApiResponse[list])
async def agents(user=Depends(require_perm("agents", "read"))):
    """代码注册表 + DB 动态 Agent 合并返回（代码 Agent 的 MCP 绑定只读）"""
    db_rows = {r.name: r for r in await AgentRepo.list_all()}
    code_items = list_agents()
    registry_names = {i["name"] for i in code_items}
    result = []
    # 1) 代码 Agent（定义来自 Python 类，DB 仅存开关/展示）
    for item in code_items:
        meta = db_rows.get(item["name"])
        result.append(
            {
                **item,
                "source": "code",
                "stale": False,
                "enabled": meta.enabled if meta else True,
                "display_name": (meta.display_name if meta and meta.display_name else item["name"]),
                "scene_id": None,
            }
        )
    # 2) 动态 Agent + 已失效的孤儿代码行（定义均来自 DB）
    for name, meta in db_rows.items():
        scene_id = str(meta.scene_id) if getattr(meta, "scene_id", None) else None
        if meta.source == "dynamic":
            result.append(
                {
                    "name": meta.name,
                    "description": meta.description or meta.name,
                    "routing_keywords": list(meta.routing_keywords or []),
                    "routing_examples": list(meta.routing_examples or []),
                    "model": meta.llm_model or "",
                    "rag_namespace": meta.rag_namespace,
                    "mcp_tools": list(meta.mcp_tool_names or []),
                    "custom_tool_names": list(meta.custom_tool_names or []),
                    "source": "dynamic",
                    "stale": False,
                    "enabled": meta.enabled,
                    "display_name": meta.display_name or meta.name,
                    "system_prompt": meta.system_prompt,
                    "rag_namespaces": list(meta.rag_namespaces or []),
                    "search_all_knowledge_bases": meta.search_all_knowledge_bases,
                    "registered_tools": _dynamic_registered_tools(meta),
                    "sort_order": meta.sort_order,
                    "scene_id": scene_id,
                }
            )
        elif meta.source == "code" and name not in registry_names:
            # 代码已下线但库中仍残留 source=code 的行 → “已失效”，仅能查看/删除（不可编辑/开关）
            result.append(
                {
                    "name": meta.name,
                    "description": meta.description or meta.name,
                    "model": meta.llm_model or "",
                    "rag_namespace": meta.rag_namespace,
                    "mcp_tools": list(meta.mcp_tool_names or []),
                    "custom_tool_names": list(meta.custom_tool_names or []),
                    "source": "code",
                    "stale": True,
                    "enabled": meta.enabled,
                    "display_name": meta.display_name or meta.name,
                    "system_prompt": meta.system_prompt,
                    "scene_id": scene_id,
                }
            )
    return ApiResponse(data=result)


@router.get("/agents/{name}", response_model=ApiResponse[dict])
async def agent_detail(name: str, user=Depends(require_perm("agents", "read"))):
    meta = await AgentRepo.get_by_name(name)
    scene_id = str(meta.scene_id) if meta is not None and getattr(meta, "scene_id", None) else None
    if meta is not None and meta.source == "dynamic":
        return ApiResponse(
            data={
                "name": meta.name,
                "description": meta.description or meta.name,
                "routing_keywords": list(meta.routing_keywords or []),
                "routing_examples": list(meta.routing_examples or []),
                "model": meta.llm_model or "",
                "rag_namespace": meta.rag_namespace,
                "mcp_tools": list(meta.mcp_tool_names or []),
                "custom_tool_names": list(meta.custom_tool_names or []),
                "source": "dynamic",
                "enabled": meta.enabled,
                "display_name": meta.display_name or meta.name,
                "system_prompt": meta.system_prompt,
                "rag_namespaces": list(meta.rag_namespaces or []),
                "search_all_knowledge_bases": meta.search_all_knowledge_bases,
                "registered_tools": _dynamic_registered_tools(meta),
                "scene_id": scene_id,
            }
        )
    for item in list_agents():
        if item["name"] == name:
            return ApiResponse(
                data={
                    **item,
                    "source": "code",
                    "stale": False,
                    "enabled": meta.enabled if meta else True,
                    "scene_id": None,
                }
            )
    # 代码已下线的孤儿 source=code 行：以 DB 镜像回显，标记已失效
    if meta is not None and meta.source == "code":
        return ApiResponse(
            data={
                "name": meta.name,
                "description": meta.description or meta.name,
                "model": meta.llm_model or "",
                "rag_namespace": meta.rag_namespace,
                "mcp_tools": list(meta.mcp_tool_names or []),
                "custom_tool_names": list(meta.custom_tool_names or []),
                "source": "code",
                "stale": True,
                "enabled": meta.enabled,
                "display_name": meta.display_name or meta.name,
                "system_prompt": meta.system_prompt,
                "rag_namespaces": list(meta.rag_namespaces or []),
                "search_all_knowledge_bases": meta.search_all_knowledge_bases,
                "scene_id": scene_id,
            }
        )
    raise NotFound("Agent 不存在")


@router.post("/agents", response_model=ApiResponse[dict])
async def create_agent(body: DynamicAgentCreate, user=Depends(require_perm("agents", "write"))):
    """创建动态 Agent（不允许与代码 Agent 或已有 Agent 重名）"""
    if body.name in AGENT_REGISTRY:
        raise Conflict(f"名称与代码 Agent 冲突: {body.name}")
    if await AgentRepo.get_by_name(body.name) is not None:
        raise Conflict(f"Agent 已存在: {body.name}")
    await _validate_mcp_bindings(body.mcp_tool_names)
    if body.rag_namespace and await KnowledgeBaseRepo.get(body.rag_namespace) is None:
        raise ParamError(f"知识库 namespace 不存在: {body.rag_namespace}")
    await _validate_rag_namespaces(body.rag_namespaces)
    await _validate_custom_tool_bindings(body.custom_tool_names)

    async with create_guard(user, KIND_AGENT):
        row = await AgentRepo.create_dynamic(
            name=body.name,
            display_name=body.display_name,
            description=body.description,
            routing_keywords=[k.strip() for k in body.routing_keywords if str(k).strip()],
            routing_examples=[e.strip() for e in body.routing_examples if str(e).strip()],
            system_prompt=body.system_prompt,
            llm_model=body.llm_model,
            rag_namespace=body.rag_namespace,
            rag_namespaces=body.rag_namespaces,
            search_all_knowledge_bases=body.search_all_knowledge_bases,
            mcp_tool_names=body.mcp_tool_names,
            custom_tool_names=body.custom_tool_names,
            enabled=body.enabled,
            sort_order=body.sort_order,
            created_by=user.id,
        )
    await AuditRepo.write(action="admin.agent.create", user_id=str(user.id), target_type="agent", target_id=row.name)
    return ApiResponse(data={"name": row.name})


@router.put("/agents/{name}", response_model=ApiResponse[dict])
async def update_agent(name: str, body: DynamicAgentUpdate, user=Depends(require_perm("agents", "write"))):
    meta = await AgentRepo.get_by_name(name)
    if meta is None or meta.source != "dynamic":
        raise ParamError("仅动态 Agent（source=dynamic）可编辑")
    if body.mcp_tool_names is not None:
        await _validate_mcp_bindings(body.mcp_tool_names)
    if body.rag_namespace and await KnowledgeBaseRepo.get(body.rag_namespace) is None:
        raise ParamError(f"知识库 namespace 不存在: {body.rag_namespace}")
    if body.rag_namespaces is not None:
        await _validate_rag_namespaces(body.rag_namespaces)
    if body.custom_tool_names is not None:
        await _validate_custom_tool_bindings(body.custom_tool_names)

    fields = body.model_dump(exclude_unset=True)
    for key in ("routing_keywords", "routing_examples"):
        if fields.get(key) is not None:
            fields[key] = [str(v).strip() for v in fields[key] if str(v).strip()]
    if fields:
        updated = await AgentRepo.update_dynamic(name, **fields)
        if updated is None:
            raise NotFound("Agent 不存在")
    await AuditRepo.write(action="admin.agent.update", user_id=str(user.id), target_type="agent", target_id=name)
    return ApiResponse(data={"name": name})


@router.delete("/agents/{name}", response_model=ApiResponse)
async def delete_agent(name: str, user=Depends(require_perm("agents", "write"))):
    meta = await AgentRepo.get_by_name(name)
    if meta is None:
        raise NotFound("Agent 不存在")
    if meta.source == "code":
        if name in AGENT_REGISTRY:
            raise ParamError("代码 Agent 不可删除，仅可在后台禁用")
        # 代码已下线的孤儿行（不在代码注册表）：允许清理
        await AgentRepo.delete_by_name(name)
    else:
        await AgentRepo.delete_dynamic(name)
    await AuditRepo.write(action="admin.agent.delete", user_id=str(user.id), target_type="agent", target_id=name)
    return ApiResponse()


@router.patch("/agents/{name}/toggle", response_model=ApiResponse)
async def toggle_agent(name: str, body: AgentToggleRequest, user=Depends(require_perm("agents", "write"))):
    meta = await AgentRepo.get_by_name(name)
    if meta is None:
        raise NotFound("Agent 未登记")
    # 默认场景只绑定这一个兜底 Agent：停用它等于把无 scene_code 的请求全部打挂
    if name == DEFAULT_ASSISTANT_NAME and not body.enabled:
        raise ParamError("默认场景的兜底助手不可停用")
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
                "description": r.config.get("description") if r.config else None,
                "type": r.type,
                "enabled": r.enabled,
                "config": r.config,
                "used_by_agents": used,
            }
        )
    return ApiResponse(data=data)


@router.post("/mcp-servers", response_model=ApiResponse[dict])
async def create_mcp_server(body: MCPServerCreate, user=Depends(require_perm("mcp-servers", "write"))):
    if await ToolRepo.get_by_name(body.name) is not None:
        raise Conflict(f"MCP 服务已存在: {body.name}")
    config = _build_mcp_config(body)
    row = await ToolRepo.create_mcp(name=body.name, config=config)
    await AuditRepo.write(action="admin.mcp.create", user_id=str(user.id), target_type="tool", target_id=row.name)
    return ApiResponse(data={"id": str(row.id), "name": row.name})


@router.put("/mcp-servers/{server_id}", response_model=ApiResponse[dict])
async def update_mcp_server(server_id: str, body: MCPServerUpdate, user=Depends(require_perm("mcp-servers", "write"))):
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
async def delete_mcp_server(server_id: str, user=Depends(require_perm("mcp-servers", "write"))):
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
async def test_mcp_server(body: MCPServerCreate, user=Depends(require_perm("mcp-servers", "write"))):
    """连通性测试：尝试连接并列出远端提供的工具（不落库，纯探测）。"""
    from app.tools.mcp_loader import _build_mcp_server_source

    cfg = {"name": body.name, "config": _build_mcp_config(body)}
    source = _build_mcp_server_source(cfg)
    if not source:
        raise ParamError("缺少 endpoint/command，无法测试")
    try:
        from app.core.ports.adapters.hello_agents import MCPClient

        async def _probe() -> list:
            async with MCPClient(source) as client:
                return await client.list_tools()

        # 与相邻的"查看工具列表"保持一致：MCPClient 内部无超时，不响应的服务会永久挂起
        tools = await asyncio.wait_for(_probe(), timeout=body.timeout_seconds)
        return ApiResponse(data={"ok": True, "tool_count": len(tools), "tools": tools})
    except Exception as e:  # noqa: BLE001
        return ApiResponse(code=0, data={"ok": False, "error": str(e) or "连接失败"})


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
        from app.core.ports.adapters.hello_agents import MCPClient

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


# ===== 自定义工具管理（实现来自代码目录，后台登记/开关）=====
@router.get("/custom-tools", response_model=ApiResponse[list])
async def list_custom_tools(user=Depends(require_staff)):
    """自定义工具清单：代码目录工具 + 后台写源码的动态工具，并标注登记/启用状态。"""
    registered = {r.name: r for r in await ToolRepo.list_by_type("custom")}
    data = []
    for spec in custom_catalog.list_specs():
        row = registered.pop(spec["name"], None)
        cfg = (row.config or {}) if row else {}
        data.append(
            {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": spec["parameters"],
                "registered": row is not None,
                "enabled": bool(row.enabled) if row else False,
                "display_description": cfg.get("description"),
                # source: code=代码目录托管（只能开关）；dynamic=后台源码托管（可编辑/删除）
                "source": "code",
                "has_code": False,
                "code_updated_at": None,
            }
        )
    # 剩下的都是 DB 里带源码的动态工具（旧行无 code 不展示，避免与代码目录混淆）
    for row in registered.values():
        cfg = row.config or {}
        if not cfg.get("code"):
            continue
        data.append(
            {
                "name": row.name,
                "description": (row.schema_ or {}).get("description") or cfg.get("description") or row.name,
                "parameters": row.schema_ or {},
                "registered": True,
                "enabled": bool(row.enabled),
                "display_description": cfg.get("description"),
                "source": "dynamic",
                "has_code": True,
                "code_updated_at": cfg.get("code_updated_at"),
            }
        )
    data.sort(key=lambda d: (d["source"] != "code", d["name"]))
    return ApiResponse(data=data)


@router.post("/custom-tools", response_model=ApiResponse[dict])
async def register_custom_tool(body: CustomToolRegister, user=Depends(require_perm("custom-tools", "write"))):
    """把代码目录中的某个工具登记到 tools 表（type=custom）。"""
    if not custom_catalog.exists(body.name):
        raise ParamError(f"工具不在代码目录中: {body.name}")
    if await _get_custom_row(body.name) is not None:
        raise Conflict(f"自定义工具已登记: {body.name}")
    if await ToolRepo.get_by_name(body.name) is not None:
        raise Conflict(f"名称已被 MCP 工具占用: {body.name}")
    spec = next((s for s in custom_catalog.list_specs() if s["name"] == body.name), {})
    config = {"description": body.description} if body.description else {}
    row = await ToolRepo.create_custom(
        name=body.name, schema_=spec.get("parameters", {}), config=config, enabled=body.enabled
    )
    await AuditRepo.write(
        action="admin.custom_tool.create", user_id=str(user.id), target_type="tool", target_id=row.name
    )
    return ApiResponse(data={"name": row.name})


# ===== 动态自定义工具（方案 A：后台在线编写源码，无需重启）=====
@router.post("/custom-tools/dynamic", response_model=ApiResponse[dict])
async def create_dynamic_custom_tool(
    body: CustomToolDynamicCreate, user=Depends(require_perm("custom-tools", "write"))
):
    """新建动态自定义工具：先安全校验 + 编译，全部通过才落库（存 config.code）。"""
    if custom_catalog.exists(body.name):
        raise Conflict(f"名称已由代码目录工具提供，请换个名字: {body.name}")
    if await _get_custom_row(body.name) is not None:
        raise Conflict(f"自定义工具已存在: {body.name}")
    if await ToolRepo.get_by_name(body.name) is not None:
        raise Conflict(f"名称已被 MCP 工具占用: {body.name}")
    meta, errors = _compile_dynamic_or_400(body.code, body.name)
    config: dict = {"source": "dynamic", "code": body.code, **_code_stamp(body.code)}
    if body.description:
        config["description"] = body.description
    row = await ToolRepo.create_custom(
        name=body.name,
        schema_={**meta["parameters"], "description": meta["description"]},
        config=config,
        enabled=body.enabled,
    )
    await AuditRepo.write(
        action="admin.custom_tool.dynamic_create", user_id=str(user.id), target_type="tool", target_id=row.name
    )
    return ApiResponse(data={"name": row.name, "description": meta["description"]})


@router.get("/custom-tools/{name}/code", response_model=ApiResponse[dict])
async def get_dynamic_custom_tool_code(name: str, user=Depends(require_staff)):
    """取动态工具的当前源码与元信息（供后台编辑器回显）。"""
    row = await _get_custom_row(name)
    cfg = (row.config or {}) if row else {}
    if row is None or not cfg.get("code"):
        raise NotFound("该工具不是动态源码工具（代码目录工具无可编辑源码）")
    return ApiResponse(
        data={
            "name": row.name,
            "code": cfg.get("code"),
            "description": cfg.get("description"),
            "enabled": bool(row.enabled),
            "parameters": row.schema_ or {},
            **_code_stamp(cfg.get("code") or ""),
        }
    )


@router.put("/custom-tools/{name}/code", response_model=ApiResponse[dict])
async def update_dynamic_custom_tool_code(
    name: str, body: CustomToolCodeUpdate, user=Depends(require_perm("custom-tools", "write"))
):
    """更新动态工具源码：编译失败保持旧版可用，成功则下一轮对话即时生效（靠指纹重编译）。"""
    row = await _get_custom_row(name)
    cfg = (row.config or {}) if row else {}
    if row is None or not cfg.get("code"):
        raise NotFound("动态自定义工具不存在（代码目录工具不可在线改源码）")
    meta, errors = _compile_dynamic_or_400(body.code, name)
    new_cfg: dict = {**cfg, "source": "dynamic", "code": body.code, **_code_stamp(body.code)}
    if body.description is not None:
        # 空串=清除覆盖，回到用代码里的 description
        if body.description.strip():
            new_cfg["description"] = body.description
        else:
            new_cfg.pop("description", None)
    await ToolRepo.update(
        row.id, config=new_cfg, schema_={**meta["parameters"], "description": meta["description"]}
    )
    await AuditRepo.write(
        action="admin.custom_tool.dynamic_update", user_id=str(user.id), target_type="tool", target_id=name
    )
    return ApiResponse(data={"name": name, "description": meta["description"]})


@router.post("/custom-tools/validate", response_model=ApiResponse[dict])
async def validate_dynamic_custom_tool_code(
    body: CustomToolValidateRequest, user=Depends(require_perm("custom-tools", "write"))
):
    """只校验不入库：返回安全/编译错误明细与解析出的工具元信息（编辑器实时检查）。"""
    cls, errors = tool_code_loader.compile_tool(body.code, expected_name=body.name or None)
    if cls is None:
        return ApiResponse(data={"ok": False, "errors": errors})
    return ApiResponse(
        data={
            "ok": True,
            "errors": [],
            "name": cls.name,
            "description": cls.description,
            "parameters": cls.parameters,
            "timeout": getattr(cls, "timeout", None),
        }
    )


@router.post("/custom-tools/test-run", response_model=ApiResponse[dict])
async def test_run_dynamic_custom_tool(
    body: CustomToolTestRunRequest, user=Depends(require_perm("custom-tools", "write"))
):
    """试运行：用给定入参实际执行一次，结果回显（不影响已登记版本）。

    风险提醒：试运行会产生真实副作用（建单/改单类工具会写数据），因此
    使用独立的测试身份 user_id=admin-test:<操作员id>，便于事后追溯。
    """
    code = body.code
    if not code:
        row = await _get_custom_row(body.name)
        code = ((row.config or {}).get("code") if row else None) or ""
        if not code:
            raise NotFound("未提供源码且该名称无已登记动态工具")
    cls, errors = tool_code_loader.compile_tool(code, expected_name=body.name)
    if cls is None:
        raise ParamError("代码校验未通过：" + "；".join(errors))
    tool = cls()
    declared = {p.name for p in tool.get_parameters()}
    args = dict(body.args or {})
    for key in ("user_id", "session_id"):
        if key in declared and not args.get(key):
            args[key] = f"admin-test:{user.id}" if key == "user_id" else "admin-test"
    try:
        if inspect.iscoroutinefunction(tool.run):
            result = await asyncio.wait_for(tool.run(**args), timeout=body.timeout_seconds)
        else:
            # 同步写法（少见）：丢到线程避免阻塞后台接口本身
            result = await asyncio.to_thread(tool.run, args)
    except TimeoutError:
        raise ParamError(f"试运行超时（{body.timeout_seconds}s），请检查工具是否在做耗时网络调用") from None
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"动态工具试运行失败: {body.name}")
        return ApiResponse(data={"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return ApiResponse(data={"ok": True, "result": _json_safe(result)})


@router.patch("/custom-tools/{name}/toggle", response_model=ApiResponse)
async def toggle_custom_tool(name: str, body: CustomToolToggle, user=Depends(require_perm("custom-tools", "write"))):
    row = await _get_custom_row(name)
    if row is None:
        raise NotFound("自定义工具未登记")
    await ToolRepo.update(row.id, enabled=body.enabled)
    await AuditRepo.write(
        action="admin.custom_tool.toggle", user_id=str(user.id), target_type="tool", target_id=name
    )
    return ApiResponse()


@router.delete("/custom-tools/{name}", response_model=ApiResponse)
async def delete_custom_tool(name: str, user=Depends(require_perm("custom-tools", "write"))):
    row = await _get_custom_row(name)
    if row is None:
        raise NotFound("自定义工具未登记")
    for agent in await AgentRepo.list_dynamic(enabled_only=False):
        if name in (agent.custom_tool_names or []):
            raise ParamError(f"自定义工具被 Agent 引用，无法删除: {agent.name}")
    await ToolRepo.delete(row.id)
    # 动态工具同步释放编译缓存，避免已删工具的对象长期驻留
    tool_code_loader.invalidate(name)
    await AuditRepo.write(
        action="admin.custom_tool.delete", user_id=str(user.id), target_type="tool", target_id=name
    )
    return ApiResponse()


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
async def metrics(user=Depends(require_perm("dashboard", "read"))):
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


async def _get_custom_row(name: str):
    """按名取 tools 表 type=custom 的 ORM 行（含 id/enabled/config）；get_by_name_and_type 只回 dict 不够用。"""
    for r in await ToolRepo.list_by_type("custom"):
        if r.name == name:
            return r
    return None


def _dynamic_registered_tools(meta) -> list[str]:
    """动态 Agent 运行时 build_tools 静态可知的注册工具：memory（必挂）+ rag（有主库时）+ 已登记自定义工具。

    MCP 属外部工具（运行时发现），单独以 mcp_tools 展示，不并入此列表（与代码 Agent 口径一致）。
    """
    tools = ["memory"]
    namespaces = list(meta.rag_namespaces or [])
    primary = meta.rag_namespace or (namespaces[0] if namespaces else None)
    if primary:
        tools.append("rag")
    tools.extend(meta.custom_tool_names or [])
    return tools


async def _validate_custom_tool_bindings(names: list[str]) -> None:
    """校验绑定的自定义工具均可用：代码目录工具或已填源码的动态工具，且已登记启用。"""
    if not names:
        return
    registered = {r.name: r for r in await ToolRepo.list_by_type("custom")}
    for n in names:
        in_code_catalog = custom_catalog.exists(n)
        row = registered.get(n)
        if not in_code_catalog:
            # 不在代码目录 → 只允许带源码且编译可用的动态工具
            cfg = (row.config or {}) if row else {}
            if not cfg.get("code"):
                raise ParamError(f"自定义工具不存在（既不在代码目录，也无动态源码）: {n}")
            cls, errors = tool_code_loader.get_compiled_class(n, cfg["code"])
            if cls is None:
                raise ParamError(f"自定义工具源码不可用: {n}（{'；'.join(errors)}）")
        if row is None:
            raise ParamError(f"自定义工具未登记: {n}")
        if not row.enabled:
            raise ParamError(f"自定义工具已禁用: {n}")


def _compile_dynamic_or_400(code: str, name: str) -> tuple[dict, list[str]]:
    """保存前的强校验：安全 + 编译 + 类内 name 一致性，失败直接 400 明细。

    返回 (元信息, 错误列表)；错误列表始终为空（非空已抛异常），便于调用方统一书写。
    """
    cls, errors = tool_code_loader.compile_tool(code, expected_name=name)
    if cls is None:
        raise ParamError("代码校验未通过：" + "；".join(errors))
    meta = {
        "name": cls.name,
        "description": cls.description,
        "parameters": cls.parameters or {},
    }
    return meta, []


def _code_stamp(code: str) -> dict:
    """随源码一起存入 config 的审计位：指纹 + 保存时间，供后台展示“改动是否已生效”。"""
    return {
        "code_sha256": tool_code_loader.fingerprint(code)[:16] if code else "",
        "code_updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _json_safe(value):
    """把工具返回值收敛为可 JSON 序列化的结构（datetime/Decimal 等转字符串）。"""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _build_mcp_config(body: MCPServerCreate) -> dict:
    """将 MCP 服务请求体归一化为 tools.config 存储结构。"""
    config: dict = {"transport": body.transport, "timeout_seconds": body.timeout_seconds}
    if body.description:
        config["description"] = body.description
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
        "description": config.get("description"),
        "endpoint": config.get("endpoint"),
        "command": config.get("command"),
        "args": config.get("args") or [],
        "headers": config.get("headers"),
        "auth": config.get("auth"),
        "timeout_seconds": config.get("timeout_seconds", 15),
        "enabled": True,
    }

