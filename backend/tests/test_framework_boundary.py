"""框架边界守卫：``app/`` 里只允许适配器模块 import ``hello_agents``。

这是 P1 的验收条件「把上游依赖收敛到一个文件」的可执行版本——靠 review 记住
"不要直接 import" 是不可靠的，靠测试才是。

替换完成后（P5）本测试仍然有效：届时适配器模块被删除，断言自然变成"全仓库 0 处"。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
# 唯一允许接触上游的实现模块（P5 删除它时，把常量改成 None 即可）
ADAPTER_REL = Path("core/ports/adapters/hello_agents.py")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_only_adapter_imports_hello_agents() -> None:
    offenders: dict[str, list[str]] = {}
    for py in sorted(APP_ROOT.rglob("*.py")):
        rel = py.relative_to(APP_ROOT)
        if rel == ADAPTER_REL:
            continue
        hits = sorted(m for m in _imported_modules(py) if m.split(".")[0] == "hello_agents")
        if hits:
            offenders[rel.as_posix()] = hits

    assert offenders == {}, (
        "以下模块绕过了端口层直接依赖 hello_agents，请改为 "
        "`from app.core.ports.adapters.hello_agents import ...`：\n"
        + "\n".join(f"  - {name}: {mods}" for name, mods in offenders.items())
    )


def test_protocols_module_is_dependency_free() -> None:
    """协议层只能是纯类型：任何第三方导入都会让"换实现"变成"改协议"。"""
    modules = _imported_modules(APP_ROOT / "core" / "ports" / "protocols.py")
    external = sorted(
        m for m in modules if m.split(".")[0] not in sys.stdlib_module_names and m != "app"
    )
    assert external == [], f"协议模块不应依赖第三方库，发现：{external}"
