"""动态自定义工具编译器：把 DB 中的源码（tools.config.code）编译为 BaseTool 子类。

方案 A（脚本插件式动态加载）的执行侧：
1. 先过 tool_security 的 AST 静态校验（import 白名单 / 危险调用黑名单）；
2. 在「受限 builtins」的隔离命名空间里 exec 源码，取出唯一的 BaseTool 子类；
3. 校验类元信息（name/description/parameters/run 签名）并做超时上限钳制；
4. 按 (工具名, 源码指纹) 缓存编译结果——与 mcp_loader 同款思路：每轮对话都会
   构建 Agent，命中缓存则零编译复用；代码变更即指纹变化，自动重新编译。

为什么不落盘再 importlib：源码来自 DB，写临时文件会引入文件权限/清理与并发覆盖
问题；进程内 exec + 指纹缓存足够，且编译失败不污染已有可用版本（保留旧缓存）。

用户代码无需（也被禁止）import 任何项目内部模块：BaseTool 直接注入执行命名空间，
写 class XxxTool(BaseTool) 即可，与 app/tools/custom/ 下的代码目录工具同一基类。

安全边界说明：受限 builtins 只是纵深防御的一层，无法等同于完整沙箱；因此
「谁能保存代码」由 RBAC（custom-tools 写权限）+ 审计日志控制，执行超时/异常
隔离由 orchestrator._execute_tool 统一兜底。
"""
import ast
import builtins
import hashlib
import logging
import threading
import time
from typing import Any

from app.tools import tool_security
from app.tools.custom.base import BaseTool  # 项目基类（async run + JSON Schema 类属性）

logger = logging.getLogger(__name__)

# 编译结果缓存有效期（秒）：正常路径靠指纹命中永不过期；TTL 仅用于兜底释放
# 长期未被引用的旧版本，避免 DB 中已删工具的对象永久驻留。
_CACHE_TTL = 3600

# name -> (source_sha256, compiled_class, cached_at_monotonic)
_cache: dict[str, tuple[str, type, float]] = {}
_cache_lock = threading.Lock()

# 动态工具允许声明的执行超时上限（秒）：防止用户把 timeout 写得比全局兜底还大
_MAX_TOOL_TIMEOUT = 120

# 用户代码中禁止直接出现的内建名（与 AST 黑名单一致，在受限 builtins 里直接缺席）：
# 文件 IO / 动态执行 / 交互输入 / 进程退出 / 控制台打印
# （print 在 Windows GBK 控制台会因 emoji 抛编码错误，历史上导致工具发现静默失败）
# 注：__import__ 不在此列，而是换成白名单守卫版（见 _guarded_import）。
_BLOCKED_BUILTINS: frozenset[str] = frozenset(
    {
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "exit",
        "globals",
        "input",
        "locals",
        "memoryview",
        "open",
        "print",
        "quit",
        "vars",
    }
)


def _guarded_import(
    name: str,
    globals: dict[str, Any] | None = None,
    locals: dict[str, Any] | None = None,
    fromlist: tuple[str, ...] = (),
    level: int = 0,
):
    """白名单导入守卫：import 语句在运行期走的其实就是这个入口。

    直接把 __import__ 从 builtins 里剔除会让所有合法写法（包括 import json）
    在 exec 时报 ImportError: __import__ not found，所以换成保留它、但先用
    tool_security 同一套白名单校验：AST 层拦住明写，这里拦住运行期拼出来的那张嘴。
    """
    if level:
        raise ImportError("动态工具禁止相对导入")
    if not tool_security.is_module_allowed(name):
        raise ImportError(f"动态工具禁止导入模块: {name}")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _safe_builtins() -> dict[str, Any]:
    """构造受限 builtins 字典（首次调用后复用）。

    getattr/setattr 保留：AST 层已禁止在源码中调用，但 dataclasses/属性协议等
    解释器内部机制需要它们，删掉会让合法写法莫名崩溃。
    __name__ 不删：删了会在部分解释器路径上抛错，影响无关代码。
    """
    table = {k: v for k, v in vars(builtins).items() if k not in _BLOCKED_BUILTINS}
    table["__import__"] = _guarded_import
    return table


def fingerprint(code: str) -> str:
    """源码指纹：任何字符变化都触发重新编译。"""
    return hashlib.sha256((code or "").encode("utf-8")).hexdigest()


def _validate_class(cls: Any, expected_name: str | None) -> list[str]:
    """编译后的类结构校验（不执行任何用户逻辑）。"""
    errors: list[str] = []
    if not (isinstance(cls, type) and issubclass(cls, BaseTool)):
        return ["编译结果不是 BaseTool 子类"]
    name = getattr(cls, "name", "")
    if not isinstance(name, str) or not name:
        errors.append("工具类必须声明字符串类属性 name")
    elif expected_name and name != expected_name:
        errors.append(f"代码中的 name({name}) 与登记名称({expected_name}) 不一致")
    if not isinstance(getattr(cls, "description", ""), str) or not getattr(cls, "description", ""):
        errors.append("工具类必须声明非空 description")
    params = getattr(cls, "parameters", None)
    if not isinstance(params, dict) or params.get("type") != "object" or "properties" not in params:
        errors.append("工具类必须声明 parameters（JSON Schema：type=object + properties）")
    run = getattr(cls, "run", None)
    if run is None or run is getattr(BaseTool, "run", None):
        errors.append("工具类必须实现 run 方法（async def run(**参数)）")
    timeout = getattr(cls, "timeout", None)
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        errors.append("timeout 必须是正数秒（或删除该属性使用系统默认值）")
    return errors


def compile_tool(code: str, expected_name: str | None = None) -> tuple[type | None, list[str]]:
    """把源码编译为工具类，返回 (类, 错误列表)。任何一步失败都不抛异常。

    expected_name 非空时校验类内 name 与之一致（防止后台改了名字忘了改代码）。
    """
    ok, errors = tool_security.check_source(code)
    if not ok:
        return None, errors
    try:
        tree = ast.parse(code, filename="<custom_tool>")
    except SyntaxError as exc:
        return None, [f"语法错误: {exc.msg}"]

    namespace: dict[str, Any] = {
        "__name__": "custom_tool_dynamic",
        "__builtins__": _safe_builtins(),
        # 注入基类，用户代码直接 class X(BaseTool) 即可，无需（也被禁止）import 内部模块
        "BaseTool": BaseTool,
    }
    try:
        exec(compile(tree, "<custom_tool>", "exec"), namespace)  # noqa: S102
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"动态工具编译失败: {type(exc).__name__}: {exc}")
        return None, [f"代码执行失败：{type(exc).__name__}: {exc}"]

    candidates = [
        obj
        for obj in namespace.values()
        if isinstance(obj, type) and issubclass(obj, BaseTool) and obj is not BaseTool
    ]
    if not candidates:
        return None, ["未找到 BaseTool 子类定义"]
    if len(candidates) > 1:
        return None, [f"一份代码只允许一个工具类，发现 {len(candidates)} 个"]
    cls = candidates[0]
    class_errors = _validate_class(cls, expected_name)
    if class_errors:
        return None, class_errors

    # 超时钳制：写在类属性上，orchestrator._execute_tool 会读取（getattr(tool, "timeout")）
    timeout = getattr(cls, "timeout", None)
    if isinstance(timeout, (int, float)) and timeout > _MAX_TOOL_TIMEOUT:
        cls.timeout = _MAX_TOOL_TIMEOUT
    return cls, []


def get_compiled_class(name: str, code: str) -> tuple[type | None, list[str]]:
    """按 (name, 代码指纹) 取编译结果：命中缓存直接复用，否则编译并写入缓存。

    编译失败不覆盖旧缓存：坏代码提交不影响已在用的工具版本。
    """
    fp = fingerprint(code)
    with _cache_lock:
        entry = _cache.get(name)
        if entry and entry[0] == fp and (time.monotonic() - entry[2]) < _CACHE_TTL:
            return entry[1], []
    cls, errors = compile_tool(code, expected_name=name)
    if cls is None:
        return None, errors
    with _cache_lock:
        _cache[name] = (fp, cls, time.monotonic())
    logger.info(f"✅ 动态工具编译加载成功: {name}（指纹 {fp[:8]}）")
    return cls, []


def build(name: str, code: str) -> BaseTool | None:
    """按名 + 源码实例化工具；编译失败返回 None（错误已由 get_compiled_class 打日志）。"""
    cls, errors = get_compiled_class(name, code)
    if cls is None:
        logger.warning(f"⚠️ 动态工具不可用: {name}, 原因: {'; '.join(errors)}")
        return None
    try:
        return cls()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"⚠️ 动态工具实例化失败: {name}: {exc}")
        return None


def invalidate(name: str) -> None:
    """显式失效（工具删除/改名时调用），释放缓存条目。"""
    with _cache_lock:
        _cache.pop(name, None)


def preview(code: str) -> dict[str, Any]:
    """编译并返回元信息（供后台「校验/试运行」回显），不写入缓存。"""
    cls, errors = compile_tool(code)
    if cls is None:
        return {"ok": False, "errors": errors}
    return {
        "ok": True,
        "errors": [],
        "name": cls.name,
        "description": cls.description,
        "parameters": cls.parameters,
        "timeout": getattr(cls, "timeout", None),
    }
