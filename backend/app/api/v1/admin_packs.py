"""管理接口：场景包列表与启停

场景包 = manifest 声明的 Agent / 工具 / 知识库命名空间集合。本接口只聚合并批量
开关包内代码 Agent（复用 agents.enabled），不新增 pack_settings 表：

- 包内至少一个 Agent 启用 → 包视为启用（部分启用时 PATCH 可整体对齐）；
- manifest enabled=false 的包不参与装载，也不能在运行时开启（需改 manifest 后重启）；
- 工具随 manifest 装载，停用包内 Agent 不会卸载已注册的工具；彻底移除需改 manifest。
"""
from fastapi import APIRouter, Depends

from app.api.deps import require_admin, require_staff
from app.core.exceptions import Conflict, NotFound
from app.models.repositories import AgentRepo
from app.packs import loader
from app.schemas.api import PackToggleRequest
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/admin/packs", tags=["admin-packs"])


def _agent_enabled(meta) -> bool:
    """代码 Agent 默认启用；仅 source=code 且显式 enabled=false 才视为停用。"""
    if meta is None or meta.source != "code":
        return True
    return bool(meta.enabled)


def _build_item(manifest, *, agent_classes, tool_classes, agent_packs, tool_packs, db_rows) -> dict:
    agent_names = [n for n, p in agent_packs.items() if p == manifest.name]
    tool_names = [n for n, p in tool_packs.items() if p == manifest.name]

    agents = []
    for name in agent_names:
        cls = agent_classes.get(name)
        meta = db_rows.get(name)
        agents.append(
            {
                "name": name,
                "display_name": (meta.display_name if meta and meta.display_name else name),
                "description": cls.description if cls else "",
                "enabled": _agent_enabled(meta),
            }
        )
    tools = [
        {
            "name": name,
            "description": tool_classes[name].description if name in tool_classes else "",
        }
        for name in tool_names
    ]

    # manifest 停用的包不可能启用；包内 Agent 全停时包也视为停用
    pack_enabled = manifest.enabled and (
        any(a["enabled"] for a in agents) if agents else True
    )
    return {
        "name": manifest.name,
        "display_name": manifest.display_name,
        "version": manifest.version,
        "enabled": pack_enabled,
        "manifest_enabled": manifest.enabled,
        "features": list(manifest.features),
        "agents": agents,
        "tools": tools,
        "knowledge_namespaces": list(manifest.knowledge_namespaces),
    }


async def _collect_packs() -> list[dict]:
    db_rows = {r.name: r for r in await AgentRepo.list_all()}
    agent_classes = loader.get_code_agents()
    tool_classes = loader.get_custom_tools()
    agent_packs = loader.get_code_agent_packs()
    tool_packs = loader.get_custom_tool_packs()
    return [
        _build_item(
            manifest,
            agent_classes=agent_classes,
            tool_classes=tool_classes,
            agent_packs=agent_packs,
            tool_packs=tool_packs,
            db_rows=db_rows,
        )
        for manifest in loader.scan_manifests()
    ]


async def _ensure_agent_rows(names: list[str]) -> None:
    """确保包内 Agent 在 agents 表有行（代码 Agent 的开关依赖 DB 行）。"""
    existing = {r.name for r in await AgentRepo.list_all()}
    classes = loader.get_code_agents()
    for name in names:
        if name in existing:
            continue
        cls = classes.get(name)
        await AgentRepo.create(
            name=name,
            display_name=(cls.description[:64] if cls and cls.description else name),
            description=cls.description if cls else None,
        )


@router.get("", response_model=ApiResponse[list])
async def list_packs(user=Depends(require_staff)):
    """全部场景包（含 manifest 停用项）及包内 Agent/工具/知识库命名空间。"""
    return ApiResponse(data=await _collect_packs())


@router.patch("/{name}", response_model=ApiResponse[dict])
async def toggle_pack(name: str, body: PackToggleRequest, user=Depends(require_admin)):
    """整体启停场景包：批量开关包内全部代码 Agent。"""
    manifest = next((m for m in loader.scan_manifests() if m.name == name), None)
    if manifest is None:
        raise NotFound("场景包不存在")
    if body.enabled and not manifest.enabled:
        raise Conflict("该场景包在 manifest 中已停用，需修改 manifest 后重启生效")

    agent_names = [n for n, p in loader.get_code_agent_packs().items() if p == name]
    if body.enabled:
        await _ensure_agent_rows(agent_names)
    await AgentRepo.set_enabled_many(agent_names, body.enabled)

    items = await _collect_packs()
    return ApiResponse(data=next(i for i in items if i["name"] == name))
