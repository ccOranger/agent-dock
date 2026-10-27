"""
MCPLoader：从 DB 读取 MCP 配置，加载为 MCPTool 并注册
"""
import hashlib
import json
import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from hello_agents.tools import MCPTool, ToolRegistry

logger = logging.getLogger(__name__)

# 单个 MCP 服务工具发现（建连 + list_tools）的硬超时上限（秒）
DEFAULT_MCP_TIMEOUT = 15

# 已发现工具的 MCPTool 实例缓存有效期（秒）。
# MCPTool.__init__ 的 _discover_tools() 会建连并拉取工具列表，而每轮对话都会构建
# Agent；缓存后同一服务在 TTL 内不再重复建连，只有配置变更（指纹变化）才重新发现。
MCP_TOOL_CACHE_TTL = 300
_tool_cache: dict[str, tuple[float, "MCPTool"]] = {}
_tool_cache_lock = threading.Lock()

# 工具发现失败（超时/报错）的负缓存有效期（秒）：坏服务在 TTL 内直接跳过，
# 避免每轮对话都重复创建发现线程 + MCP 子进程（泄漏随请求数线性累积）；
# 配置变更时指纹变化，自动绕过负缓存重新尝试。
MCP_FAILURE_CACHE_TTL = 60
_failure_cache: dict[str, float] = {}

# backend 根目录（本文件位于 backend/app/tools/mcp_loader.py）：stdio 服务脚本按此重定位
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


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


def _resolve_stdio_command(command: str | None) -> str:
    """把配置里的解释器解析为当前主机上可用的命令。

    - 留空 → 用运行进程自己的 sys.executable（跨 Windows/Linux/容器都可移植）；
    - 裸命令名（不含路径分隔符，如 python/uv/npx/docker）→ 交给 PATH 解析；
      Windows 上 npx/npm/yarn/pnpm 等实为 .cmd 包装，create_subprocess_exec 直接传裸名
      会 FileNotFoundError，故当本机 PATH 上存在 <name>.cmd 时补上 .cmd 后缀（DB 仍存
      跨平台裸名，Linux 上保持原样）；
    - 绝对路径解释器 → 仅当本机确实存在才用，否则回退 sys.executable（兼容旧库写死的异机绝对路径）。
    """
    if not command:
        return sys.executable
    norm = str(command).replace("\\", "/")
    if "/" not in norm:
        if os.name == "nt" and not command.lower().endswith((".cmd", ".exe", ".bat")):
            if shutil.which(command + ".cmd"):
                return command + ".cmd"
        return command
    return command if Path(norm).exists() else sys.executable


def _resolve_stdio_arg(arg: str) -> str:
    """把服务脚本参数解析为当前主机上的真实路径（仅处理路径类参数）。

    - 选项 token（以 - 开头，如 --port）原样保留；
    - 含 mcp_servers 段的（新库相对路径 / 旧库异机绝对路径）→ 取 mcp_servers/ 之后
      的相对片段，按 _BACKEND_ROOT 重定位；
    - 其它当前能命中的相对路径 → 绝对化；其余（参数值等）原样。
    """
    s = str(arg)
    norm = s.replace("\\", "/")
    if norm.startswith("-"):
        return s
    parts = [x for x in norm.split("/") if x]
    if "mcp_servers" in parts:
        rel = "/".join(parts[parts.index("mcp_servers"):])
        return str((_BACKEND_ROOT / rel).resolve())
    p = Path(norm)
    if p.exists():
        return str(p.resolve())
    return s


def _build_mcp_server_source(cfg: dict) -> dict | list:
    """将 DB 中的 MCP 配置映射为 hello_agents MCPTool/MCPClient 可识别的 server_source。

    支持两类传输：
    - 本地（stdio）：解释器与脚本路径在加载时按当前运行环境重解析（方案 A），
      因此 DB 里只存相对路径/留空解释器也能跨环境跑；旧库的绝对路径自动回退。
    - 远程（sse / http）：config 含 endpoint，转成 {"transport", "url", "headers", "auth"} 配置字典。
    """
    conf = cfg.get("config") or {}
    transport = (conf.get("transport") or "").lower()

    # 本地 stdio：command 可留空（回退运行解释器），args 里的脚本路径按 backend 根重定位
    command = conf.get("command")
    if command or transport == "stdio":
        args = conf.get("args") or []
        resolved_args = [_resolve_stdio_arg(a) for a in args]
        return [_resolve_stdio_command(command), *resolved_args]

    # 远程 sse / http
    url = conf.get("endpoint") or conf.get("url")
    source: dict = {"transport": "sse" if transport == "sse" else "http", "url": url}
    if conf.get("headers"):
        source["headers"] = conf["headers"]
    if conf.get("auth"):
        source["auth"] = conf["auth"]
    return source


def _cache_key(name: str, server_source: dict | list, timeout_seconds: int) -> str:
    """按服务名 + 最终 server_source + 超时生成配置指纹（配置变更即自然失效）。"""
    raw = json.dumps(
        {"name": name, "source": server_source, "timeout": timeout_seconds},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _get_cached_tool(key: str) -> "MCPTool | None":
    now = time.monotonic()
    with _tool_cache_lock:
        entry = _tool_cache.get(key)
        if entry is None:
            return None
        created_at, tool = entry
        if now - created_at > MCP_TOOL_CACHE_TTL:
            _tool_cache.pop(key, None)
            return None
        return tool


def _cache_tool(key: str, tool: "MCPTool") -> None:
    with _tool_cache_lock:
        _tool_cache[key] = (time.monotonic(), tool)
        _failure_cache.pop(key, None)


def _recent_failure(key: str) -> bool:
    """该配置指纹是否在负缓存有效期内失败过。"""
    now = time.monotonic()
    with _tool_cache_lock:
        failed_at = _failure_cache.get(key)
        if failed_at is None:
            return False
        if now - failed_at > MCP_FAILURE_CACHE_TTL:
            _failure_cache.pop(key, None)
            return False
        return True


def _mark_failure(key: str) -> None:
    with _tool_cache_lock:
        _failure_cache[key] = time.monotonic()


def _register_one(registry: ToolRegistry, name: str, cfg: dict, timeout_seconds: int) -> list[str]:
    """在独立守护线程中构造并注册 MCPTool，带硬超时；返回本次新注册的工具名。

    MCPTool.__init__ 会执行 _discover_tools()（对远端建连并拉取工具列表），
    内部是无超时的阻塞等待；一旦远端不响应就会永久卡住。放到守护线程并 join(timeout)
    可以把影响限制在“这一个工具”上：超时则跳过该工具，不阻断整个 Agent 构建。
    """
    outcome: dict[str, Any] = {}
    before = set(registry.list_tools())
    server_source = _build_mcp_server_source(cfg)
    if not server_source:
        logger.warning(f"⚠️ 配置缺少 endpoint/command，跳过: {name}")
        return []

    # 先查缓存：命中则只把包装工具注册进本轮 registry，不再建连
    key = _cache_key(name, server_source, timeout_seconds)
    cached = _get_cached_tool(key)
    if cached is not None:
        cached.expandable = True
        registry.register_tool(cached)
        added = sorted(set(registry.list_tools()) - before)
        logger.info(f"♻️ MCP 工具命中缓存: {name}，复用 {len(added)} 个工具")
        return added
    if _recent_failure(key):
        # 坏服务在负缓存有效期内直接跳过，不再新建发现线程/子进程
        logger.info(f"⏭️ MCP 工具发现近期已失败，负缓存跳过: {name}")
        return []

    def _work() -> None:
        try:
            tool = MCPTool(name=cfg["name"], server_command=server_source)
            # hello_agents 0.2.9 的 MCPTool 默认 expandable=False，会被当作单个“元工具”
            # 注册（参数只有 action/tool_name/arguments），模型看不到真实函数而无法调用。
            # 置 expandable=True 后，register_tool 会调用 get_expanded_tools()，把远端每个
            # 工具展开为独立的 MCPWrappedTool（带真实名称/参数），模型才能发起工具调用。
            tool.expandable = True
            registry.register_tool(tool)
            outcome["tool"] = tool
            outcome["tool_count"] = len(getattr(tool, "_available_tools", []) or [])
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=_work, name=f"mcp-register-{name}", daemon=True)
    worker.start()
    worker.join(timeout_seconds)

    if worker.is_alive():
        logger.warning(f"⚠️ MCP 工具发现超时({timeout_seconds}s)，跳过: {name}")
        _mark_failure(key)
        return []
    if "error" in outcome:
        logger.error(f"MCP 工具注册失败: {name}, err={outcome['error']}")
        _mark_failure(key)
        return []
    tool = outcome.get("tool")
    if tool is not None:
        # 只在发现成功（哪怕是 0 个工具）时缓存，避免把建连失败也固定下来
        _cache_tool(key, tool)
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
