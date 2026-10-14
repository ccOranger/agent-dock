"""
MCPLoader：从 DB 读取 MCP 配置，加载为 MCPTool 并注册
"""
import logging
import threading
from typing import Any

from hello_agents.tools import MCPTool, ToolRegistry

logger = logging.getLogger(__name__)

# 单个 MCP 服务工具发现（建连 + list_tools）的硬超时上限（秒）
DEFAULT_MCP_TIMEOUT = 15


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


def _build_mcp_server_source(cfg: dict) -> dict | list:
    """将 DB 中的 MCP 配置映射为 hello_agents MCPTool/MCPClient 可识别的 server_source。

    支持两类传输：
    - 远程（sse / http）：config 含 endpoint，转成 {"transport", "url", "headers", "auth"} 配置字典。
    - 本地（stdio）：config 含 command（可选 args），转成命令列表。
    """
    conf = cfg.get("config") or {}
    transport = (conf.get("transport") or "").lower()

    # 本地 stdio：显式提供 command
    command = conf.get("command")
    if command or transport == "stdio":
        args = conf.get("args") or []
        return [command, *args] if command else args

    # 远程 sse / http
    url = conf.get("endpoint") or conf.get("url")
    source: dict = {"transport": "sse" if transport == "sse" else "http", "url": url}
    if conf.get("headers"):
        source["headers"] = conf["headers"]
    if conf.get("auth"):
        source["auth"] = conf["auth"]
    return source


def _register_one(registry: ToolRegistry, name: str, cfg: dict, timeout_seconds: int) -> list[str]:
    """在独立守护线程中构造并注册 MCPTool，带硬超时；返回本次新注册的工具名。

    MCPTool.__init__ 会执行 _discover_tools()（对远端建连并拉取工具列表），
    内部是无超时的阻塞等待；一旦远端不响应就会永久卡住。放到守护线程并 join(timeout)
    可以把影响限制在“这一个工具”上：超时则跳过该工具，不阻断整个 Agent 构建。
    """
    outcome: dict[str, Any] = {}
    before = set(registry.list_tools())

    def _work() -> None:
        try:
            server_source = _build_mcp_server_source(cfg)
            if not server_source:
                outcome["skip"] = "配置缺少 endpoint/command"
                return
            tool = MCPTool(name=cfg["name"], server_command=server_source)
            # hello_agents 0.2.9 的 MCPTool 默认 expandable=False，会被当作单个“元工具”
            # 注册（参数只有 action/tool_name/arguments），模型看不到真实函数而无法调用。
            # 置 expandable=True 后，register_tool 会调用 get_expanded_tools()，把远端每个
            # 工具展开为独立的 MCPWrappedTool（带真实名称/参数），模型才能发起工具调用。
            tool.expandable = True
            registry.register_tool(tool)
            outcome["tool_count"] = len(getattr(tool, "_available_tools", []) or [])
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=_work, name=f"mcp-register-{name}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)

    if worker.is_alive():
        logger.warning(f"⚠️ MCP 工具发现超时({timeout_seconds}s)，跳过: {name}")
        return []
    if "error" in outcome:
        logger.error(f"MCP 工具注册失败: {name}, err={outcome['error']}")
        return []
    if "skip" in outcome:
        logger.warning(f"⚠️ {outcome['skip']}，跳过: {name}")
        return []
    added = sorted(set(registry.list_tools()) - before)
    count = outcome.get("tool_count", 0)
    if count == 0:
        logger.warning(f"⚠️ MCP 已连接但远端未返回任何工具（模型将无可用函数）: {name}")
    else:
        logger.info(f"✅ MCP 工具注册成功: {name}，展开 {count} 个工具")
    return added


def register_mcp_tools(
    registry: ToolRegistry, names: list[str], configs: dict[str, dict | None]
) -> list[str]:
    """纯同步注册：仅根据已预取的配置构建 MCPTool，不再访问 DB/事件循环。

    返回实际注册成功的工具名列表（远端工具展开后的独立工具名），
    供上层判断是否需要向 system_prompt 强调“必须调用工具”。

    hello_agents 0.2.9 的 MCPTool 不接受 endpoint/transport 参数，而是通过
    server_command 接受：命令列表（stdio）/ 配置字典（sse/http）/ URL 字符串，
    由内部 MCPClient._prepare_server_source 自行识别传输类型。server 仅用于内存
    FastMCP 实例，此处保持 None。

    调用约定：本函数为阻塞实现，上层（Orchestrator）必须在工作线程中调用，
    不得在事件循环线程上直接执行。
    """
    registered: list[str] = []
    for name in names:
        cfg = configs.get(name)
        if not cfg:
            logger.warning(f"MCP 配置不存在: {name}")
            continue
        timeout = int((cfg.get("config") or {}).get("timeout_seconds") or DEFAULT_MCP_TIMEOUT)
        registered.extend(_register_one(registry, name, cfg, timeout))
    return registered
