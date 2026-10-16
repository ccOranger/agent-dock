"""自定义工具注册器：按名从代码目录装配 BaseTool 到 ToolRegistry。

对称于 mcp_loader.register_mcp_tools，但简单得多：
- 自定义工具是进程内 Python 对象，无网络、无跨事件循环问题，纯同步即可，
  无需守护线程/超时（MCP 那些复杂度这里都不需要）。
- 会话上下文 user_id/session_id 由 orchestrator._execute_tool 按工具声明的参数自动
  注入，这里只负责实例化 + 注册。

调用约定：在业务 Agent 的 build_tools（工作线程内）同步调用即可。
"""
import logging

from hello_agents.tools import ToolRegistry

from app.tools.custom_catalog import build, exists

logger = logging.getLogger(__name__)


def register_custom_tools(registry: ToolRegistry, names: list[str]) -> list[str]:
    """把 names 指定的代码自定义工具注册进 registry，返回实际注册成功的工具名。"""
    registered: list[str] = []
    for name in names or []:
        if not exists(name):
            logger.warning(f"⚠️ 自定义工具不在代码目录，跳过: {name}")
            continue
        tool = build(name)
        if tool is None:  # 理论上不会发生（exists 已保证），防御性兜底
            logger.warning(f"⚠️ 自定义工具实例化失败，跳过: {name}")
            continue
        registry.register_tool(tool)
        registered.append(name)
        logger.info(f"✅ 自定义工具注册成功: {name}")
    return registered
