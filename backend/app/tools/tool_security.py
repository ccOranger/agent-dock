"""动态自定义工具的安全校验：源码入库/执行前的 AST 静态检查。

背景（方案 A：脚本插件式动态加载）：管理员可在后台直接编写 Python 工具代码存入
tools 表（config.code），运行时编译加载为 BaseTool 子类。这相当于把服务器上的
代码执行权开放给了后台用户，必须在「保存」和「加载」两个关口做静态护栏：

- import 白名单：只允许纯计算/常规数据处理所需的标准库与显式放行的第三方库，
  杜绝 os/subprocess/socket/shutil/pathlib 等具备进程、文件、网络破坏面的模块；
  另有硬黑名单先于白名单生效，堵住从已放行包递进的路子（如 asyncio.subprocess）；
- 危险内建调用黑名单：open/eval/exec/compile/__import__/globals/getattr 等
  （getattr 可绕过属性白名单，一并禁止）；
- 双下划线属性访问黑名单：__globals__/__class__/__subclasses__ 等可逃逸沙箱，
  __dict__/__init__/__name__ 等类定义常规用法放行；
- 语法合法 + 行数上限：防超大源码拖慢编译。

注意：这是纵深防御的一层而非完整沙箱——进程内 exec 无法做到绝对隔离，因此
保存/修改代码必须走 RBAC custom-tools 写权限并落审计日志，仅信任后台操作人员；
执行侧的超时与异常隔离由 orchestrator._execute_tool 统一兜底。

校验结果用 (ok, errors) 返回而非抛异常：调用方（admin API）据此返回 400 明细。
"""
import ast
from typing import Any

# 源码行数上限（含空行/注释）：正常工具几十行，给足冗余仍防超大文件
MAX_SOURCE_LINES = 400

# import 白名单：标准库（纯计算/文本/数据）+ 明确放行的第三方。
# 未列入即拒绝；新增条目需确认该库不暴露进程/文件/网络底层能力。
ALLOWED_STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "abc",
        "argparse",
        "asyncio",
        "base64",
        "binascii",
        "calendar",
        "collections",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "html",
        "inspect",
        "itertools",
        "json",
        "logging",
        "math",
        "numbers",
        "operator",
        "re",
        "random",
        "secrets",
        "statistics",
        "string",
        "struct",
        "textwrap",
        "time",
        "typing",
        "unicodedata",
        "urllib.parse",
        "uuid",
        "weakref",
        "zoneinfo",
    }
)

# 放行的第三方顶层包（当前项目依赖内、无系统调用面的纯功能库）
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset({"httpx", "numpy", "pandas"})

# 硬黑名单（先于白名单判定）：即使拼上已放行的包名也不能递进到这些子模块。
# 例：asyncio 已放行（多点并发拉取需要它），但 asyncio.subprocess / 反射导入必须堵死。
DENIED_MODULES: frozenset[str] = frozenset(
    {
        "asyncio.subprocess",
        "builtins",
        "ctypes",
        "fcntl",
        "importlib",
        "multiprocessing",
        "os",
        "pathlib",
        "pickle",
        "posix",
        "pty",
        "shelve",
        "shutil",
        "signal",
        "socket",
        "socketserver",
        "subprocess",
        "sys",
        "tempfile",
        "webbrowser",
    }
)

# 禁止调用的属性名（不限 dunder）：已放行包里的“递进到其他能力”的入口
# （getattr 在调用黑名单里，但 asyncio.create_subprocess_* 这类具名工厂需要逐个堵）
FORBIDDEN_ATTR_NAMES: frozenset[str] = frozenset(
    {"create_subprocess_exec", "create_subprocess_shell", "run_coroutine_threadsafe"}
)

# 禁止调用的内建名：文件 IO、动态执行、反射逃逸入口
FORBIDDEN_BUILTINS: frozenset[str] = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "input",
        "locals",
        "memoryview",
        "open",
        "quit",
        "vars",
    }
)

# 禁止访问的双下划线属性（逃逸链常用）；其余 dunder 如 __init__/__name__/__doc__ 放行
FORBIDDEN_DUNDERS: frozenset[str] = frozenset(
    {
        "__bases__",
        "__class__",
        "__code__",
        "__dict__",
        "__globals__",
        "__import__",
        "__mro__",
        "__reduce__",
        "__reduce_ex__",
        "__subclasses__",
        "__subclasshook__",
        "__builtins__",
        "__loader__",
        "__spec__",
        "__closure__",
        "__func__",
        "__self__",
    }
)


def is_module_allowed(name: str) -> bool:
    """对外入口：供运行期导入守卫复用同一套判定（AST 与 exec 不会两套口径）。"""
    return _module_allowed(name)


def _module_allowed(name: str) -> bool:
    """判断 import 的模块名（含 from X import 的 X）是否在白名单内（按包前缀匹配）。"""
    if name in DENIED_MODULES or any(name.startswith(f"{d}.") for d in DENIED_MODULES):
        return False
    top = name.split(".")[0]
    if top in ALLOWED_THIRD_PARTY:
        return True
    if name in ALLOWED_STDLIB_MODULES:
        return True
    # 标准库包允许子模块（collections.abc → collections）；urllib 仅放行 parse
    if top in ALLOWED_STDLIB_MODULES and not name.startswith("urllib."):
        return True
    return name == "urllib.parse"


class _SafetyVisitor(ast.NodeVisitor):
    """遍历全部节点收集违规项；只收集不抛异常，一次性给出完整清单。"""

    def __init__(self) -> None:
        self.errors: list[str] = []

    # ---- import 面 ----
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if not _module_allowed(alias.name):
                self.errors.append(f"禁止导入模块: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # from . import x / from .pkg import x：相对导入可触及项目内部模块，一律禁止
        if node.level:
            self.errors.append("禁止相对导入")
            return
        mod = node.module or ""
        if not _module_allowed(mod):
            self.errors.append(f"禁止导入模块: {mod}")
            return
        # 从放行模块 import * 或 dunder 也拦（如 from json import __loader__）
        for alias in node.names:
            if alias.name == "*":
                self.errors.append(f"禁止 from {mod} import *")
            elif alias.name.startswith("__"):
                self.errors.append(f"禁止从 {mod} 导入 {alias.name}")
        self.generic_visit(node)

    # ---- 调用面 ----
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name) and func.id in FORBIDDEN_BUILTINS:
            self.errors.append(f"禁止调用 {func.id}()")
        self.generic_visit(node)

    # ---- 属性反射面 ----
    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_DUNDERS:
            self.errors.append(f"禁止访问属性 {node.attr}")
        elif node.attr in FORBIDDEN_ATTR_NAMES:
            self.errors.append(f"禁止使用 {node.attr}")
        self.generic_visit(node)

    # 说明：getattr/setattr 已在调用黑名单里，无需再拦它的第二参数字符串。


# 未找到可用工具类时的统一提示（多处复用，行长受 ruff E501 约束）
_NO_CLASS_ERROR = (
    "未找到有效的工具类：需定义 BaseTool 子类，并以字符串类属性声明 name"
    "（写法参见 app/tools/custom/base.py）"
)


def check_source(code: str) -> tuple[bool, list[str]]:
    """对工具源码做静态安全校验，返回 (是否通过, 错误列表)。"""
    if not code or not code.strip():
        return False, ["代码不能为空"]
    n_lines = code.count("\n") + 1
    if n_lines > MAX_SOURCE_LINES:
        return False, [f"代码行数 {n_lines} 超过上限 {MAX_SOURCE_LINES}"]
    try:
        tree = ast.parse(code, filename="<custom_tool>")
    except SyntaxError as exc:
        return False, [f"语法错误: 第{exc.lineno or '?'}行 {exc.msg}"]
    visitor = _SafetyVisitor()
    visitor.visit(tree)
    return (not visitor.errors), visitor.errors


def extract_tool_meta(code: str) -> tuple[bool, dict[str, Any], list[str]]:
    """静态提取工具元信息（name/description/parameters），供保存时先校验再入库。

    不执行任何用户代码：直接解析 AST，找到同时满足以下条件的类定义——
    - 基类中含 BaseTool（名字匹配即可，真正的继承校验在加载编译后做）；
    - 类体内有 name = 字符串常量 的赋值。
    返回 (ok, meta, errors)；meta 含 name/description/parameters（parameters
    仅当能静态解析为 dict 字面量时给出，否则给空 dict 由加载阶段回填）。
    """
    ok, errors = check_source(code)
    if not ok:
        return False, {}, errors
    try:
        tree = ast.parse(code, filename="<custom_tool>")
    except SyntaxError as exc:  # check_source 已拦截，防御性兜底
        return False, {}, [f"语法错误: {exc.msg}"]

    candidates: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {_class_name(b) for b in node.bases}
        if "BaseTool" not in bases:
            continue
        meta: dict[str, Any] = {"name": "", "description": "", "parameters": {}}
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                continue
            target = stmt.targets[0]
            if not isinstance(target, ast.Name):
                continue
            if target.id in ("name", "description"):
                if isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                    meta[target.id] = stmt.value.value
            elif target.id == "parameters":
                try:
                    val = ast.literal_eval(stmt.value)
                except (ValueError, TypeError):
                    val = None
                if isinstance(val, dict):
                    meta["parameters"] = val
        if meta["name"]:
            candidates.append(meta)

    if not candidates:
        return False, {}, [_NO_CLASS_ERROR]
    if len(candidates) > 1:
        names = [c["name"] for c in candidates]
        return False, {}, [f"一份代码只允许一个工具类，发现多个: {names}"]
    meta = candidates[0]
    if not meta["description"]:
        return False, {}, [f"工具类 {meta['name']} 必须声明 description（模型据此决定是否调用）"]
    if meta["parameters"] and meta["parameters"].get("type") != "object":
        return False, {}, ["parameters 必须是 JSON Schema 对象（type=object + properties）"]
    return True, meta, []


def _class_name(node: ast.expr) -> str:
    """取基类表达式的可读名：Foo / a.Foo / a.b.Foo → 末级名（BaseTool 直接按末级匹配）。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""
