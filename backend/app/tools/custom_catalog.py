"""自定义工具目录：场景包声明的 BaseTool 可发现清单。

设计要点（见「自定义工具管理功能」方案）：
- 工具实现来自场景包（packs/<name>/tools），本模块只做内核侧索引——把 packs.loader
  按 manifest 装载的工具集中列出，暴露 name/description/parameters（parameters 即
  JSON Schema），供后台扫描展示、勾选登记到 tools 表（type=custom），再由动态 Agent
  按名绑定。
- 不再硬编码 import：新增工具时在所属场景包的 manifest.yaml 的 tools 段登记即可。
- 所有工具均为无参构造（会话上下文 user_id/session_id 由 orchestrator 执行时按参数
  声明自动注入），故 build() 直接 cls() 实例化。
"""
from app.packs.loader import get_custom_tools
from app.tools.custom.base import BaseTool

# name -> 工具类（键即类属性 name，全局唯一，与 tools.name / MCP 名共用唯一约束）
# 先拷贝一层，避免调用方改动影响 loader 的进程缓存
CATALOG: dict[str, type[BaseTool]] = dict(get_custom_tools())


def exists(name: str) -> bool:
    return name in CATALOG


def build(name: str) -> BaseTool | None:
    """按名实例化目录中的工具；未登记返回 None。"""
    cls = CATALOG.get(name)
    return cls() if cls is not None else None


def list_specs() -> list[dict]:
    """返回代码目录全部工具的名称/描述/参数 Schema（供后台扫描展示与登记）。"""
    return [
        {
            "name": cls.name,
            "description": cls.description or "",
            "parameters": cls.parameters or {},
        }
        for cls in CATALOG.values()
    ]
