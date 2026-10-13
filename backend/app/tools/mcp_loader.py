"""
MCPLoader：从 DB 读取 MCP 配置，加载为 MCPTool 并注册
"""
import logging

from hello_agents.tools import MCPTool, ToolRegistry

logger = logging.getLogger(__name__)


async def fetch_mcp_configs(names: list[str]) -> dict[str, dict | None]:
    """在调用方（主）event loop 上异步读取 MCP 配置。

    必须在 FastAPI 请求所在的 event loop（如 Orchestrator.dispatch）中 await，
    避免在临时线程/新建 loop 中访问绑定主循环的 asyncpg 连接。
    """
    from app.models.repositories import ToolRepo

    configs: dict[str, dict | None] = {}
    for name in names:
        configs[name] = await ToolRepo.get_by_name_and_type(name, "mcp")
    return configs


def register_mcp_tools(
    registry: ToolRegistry, names: list[str], configs: dict[str, dict | None]
) -> None:
    """纯同步注册：仅根据已预取的配置构建 MCPTool，不再访问 DB/事件循环。"""
    for name in names:
        cfg = configs.get(name)
        if not cfg:
            logger.warning(f"MCP 配置不存在: {name}")
            continue
        try:
            tool = MCPTool(
                endpoint=cfg["config"]["endpoint"],
                tool_name=cfg["name"],
                transport=cfg["config"].get("transport", "http"),
                auth=cfg["config"].get("auth"),
                timeout=cfg["config"].get("timeout_seconds", 15),
            )
            registry.register_tool(tool)
            logger.info(f"✅ MCP 工具注册成功: {name}")
        except Exception as e:
            logger.error(f"MCP 工具注册失败: {name}, err={e}")
