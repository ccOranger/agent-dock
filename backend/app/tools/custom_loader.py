"""自定义工具注册器：按名装配 BaseTool 到 ToolRegistry（两种来源统一入口）。

对称于 mcp_loader.register_mcp_tools。自定义工具现在有两个来源：
- source=code（历史形态）：实现托管在代码目录 app/tools/custom/，登记在
  custom_catalog._TOOL_CLASSES，进程内直接实例化；
- source=dynamic（方案 A）：源码存于 tools 表 config.code，运行时由
  tool_code_loader 做安全校验 + 编译 + 指纹缓存，改代码即生效、无需重启。

解析优先级：代码目录 > DB 源码。同名时以代码实现为准，保证存量工具零回归
（后台即便误建同名动态工具也不会覆盖线上代码工具）。

会话上下文 user_id/session_id 仍由 orchestrator._execute_tool 按工具声明的参数自动
注入，这里只负责实例化 + 注册；动态工具的超时由 orchestrator 读取实例 timeout 属性。

DB 读取必须在主 event loop 预取（fetch_dynamic_configs），随后把结果传进
build_tools/register_custom_tools（工作线程内同步执行），避免跨循环访问 asyncpg。
"""
import logging
from typing import Any

from hello_agents.tools import ToolRegistry

from app.tools import custom_catalog, tool_code_loader

logger = logging.getLogger(__name__)


async def fetch_dynamic_configs(names: list[str]) -> dict[str, dict[str, Any]]:
    """在主 event loop 上预取动态自定义工具的源码配置（仅 DB 托管源码的工具）。

    返回 {工具名: {"code": 源码, "description": 显示描述}}；代码目录工具、未登记、
    已禁用或未填源码的工具一律不出现在结果里（由注册阶段按既有逻辑跳过）。
    """
    from app.models.repositories import ToolRepo

    wanted = [n for n in (names or []) if n and not custom_catalog.exists(n)]
    if not wanted:
        return {}
    configs: dict[str, dict[str, Any]] = {}
    for row in await ToolRepo.list_by_type("custom"):
        if row.name not in wanted or not row.enabled:
            continue
        cfg = row.config or {}
        code = cfg.get("code")
        if not code:
            continue  # 仅登记未填源码的旧行：不在此处打日志，交由注册阶段统一告警
        configs[row.name] = {"code": code, "description": cfg.get("description")}
    return configs


def register_custom_tools(
    registry: ToolRegistry, names: list[str], configs: dict[str, dict[str, Any]] | None = None
) -> list[str]:
    """把 names 指定的自定义工具注册进 registry，返回实际注册成功的工具名。

    configs 为 fetch_dynamic_configs 的预取结果；不传时只有代码目录工具可装配
    （动态工具会记一条「未预取配置」告警并跳过，绝不阻塞整个 Agent 构建）。
    """
    registered: list[str] = []
    for name in names or []:
        tool = None
        if custom_catalog.exists(name):
            tool = custom_catalog.build(name)
            if tool is None:  # 理论上不会发生（exists 已保证），防御性兜底
                logger.warning(f"⚠️ 代码自定义工具实例化失败，跳过: {name}")
                continue
            logger.info(f"✅ 自定义工具注册成功（代码目录）: {name}")
        else:
            cfg = (configs or {}).get(name)
            if not cfg or not cfg.get("code"):
                logger.warning(f"⚠️ 自定义工具无可用实现（不在代码目录且未预取到源码），跳过: {name}")
                continue
            tool = tool_code_loader.build(name, cfg["code"])
            if tool is None:
                continue
            logger.info(f"✅ 自定义工具注册成功（动态源码）: {name}")
        registry.register_tool(tool)
        registered.append(name)
    return registered


def resolve_spec(name: str, code: str | None) -> dict[str, Any]:
    """给出工具的可调用签名（参数 Schema 等），供后台校验/试运行与绑定检查复用。

    代码目录工具直接读类属性；动态工具需先编译（不写缓存）才能拿到真实 parameters。
    """
    if custom_catalog.exists(name):
        return custom_catalog.spec_of(name)
    if not code:
        return {"ok": False, "errors": [f"工具未登记且无源码: {name}"]}
    return tool_code_loader.preview(code)
