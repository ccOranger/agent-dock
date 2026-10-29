"""RBAC 单一事实源：角色 ↔ 可授权菜单模块 ↔ 读/写 授权。

设计取舍（对应"粗粒度守卫 + 可配置模块"方案）：
- **菜单结构本身仍在前端**（与 Vue 路由强绑定），这里只登记"可授权模块"的规范
  key/name/group，用于三处：① 后端接口按 key+access 校验；② 角色配置页渲染读写矩阵；
  ③ 迁移 seed 内置角色与坐席默认授权；
- **access 两级**：`read`（只读：可见菜单 + 可浏览/查询）⊂ `write`（可改：含只读全部）；
- 内置角色 `admin` 为超级角色：前后端一律旁路放行、不可删除；`agent` 为默认坐席角色，
  其默认授权贴合历史上 `require_staff` 的可见范围，保证升级到 RBAC 后坐席菜单不回退。
"""
from __future__ import annotations

ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_LEVELS = (ACCESS_READ, ACCESS_WRITE)

# access 序：write 覆盖 read。用于"要求 X，持有 Y 是否满足"的比较。
_ACCESS_RANK = {ACCESS_READ: 1, ACCESS_WRITE: 2}


def access_satisfied(held: str | None, required: str) -> bool:
    """持有权限 held 是否满足要求 required（write ⊇ read；None 视为无权限）。"""
    if held is None:
        return False
    return _ACCESS_RANK.get(held, 0) >= _ACCESS_RANK.get(required, 99)


# 可授权模块：key 与前端菜单叶子、后端路由 path slug 严格对齐
MENU_MODULES: list[dict[str, str]] = [
    {"key": "takeover", "name": "人工接管", "group": "运营"},
    {"key": "inquiries", "name": "咨询管理", "group": "运营"},
    {"key": "tickets", "name": "工单管理", "group": "运营"},
    {"key": "traces", "name": "链路追踪", "group": "观测"},
    {"key": "dashboard", "name": "仪表盘", "group": "观测"},
    {"key": "analytics", "name": "数据分析", "group": "观测"},
    {"key": "knowledge", "name": "知识库维护", "group": "内容"},
    {"key": "canned-scripts", "name": "固定话术", "group": "内容"},
    {"key": "memory", "name": "记忆管理", "group": "内容"},
    {"key": "scenes", "name": "业务场景", "group": "配置"},
    {"key": "agents", "name": "Agent 管理", "group": "配置"},
    {"key": "router", "name": "路由分发器", "group": "配置"},
    {"key": "mcp-servers", "name": "MCP 服务管理", "group": "集成"},
    {"key": "custom-tools", "name": "自定义工具", "group": "集成"},
    {"key": "staff", "name": "人员与配额", "group": "系统"},
    {"key": "roles", "name": "角色与权限", "group": "系统"},
]
MENU_KEYS: set[str] = {m["key"] for m in MENU_MODULES}


def is_valid_menu_key(key: str) -> bool:
    return key in MENU_KEYS


def all_write_permissions() -> dict[str, str]:
    """超级角色（admin）的有效权限：全模块 write。"""
    return {key: ACCESS_WRITE for key in MENU_KEYS}


# 内置角色（is_system=True，不可删除、key 不可改）
SYSTEM_ROLES: list[dict[str, str]] = [
    {"key": "admin", "name": "管理员", "description": "超级管理员，默认拥有全部模块的读写权限"},
    {"key": "agent", "name": "客服坐席", "description": "一线客服：接管/咨询/工单读写，知识与链路按需查看"},
]

# 默认坐席授权：贴合历史 require_staff 可见范围，保证 RBAC 升级后不回退
DEFAULT_AGENT_GRANTS: dict[str, str] = {
    "takeover": ACCESS_WRITE,
    "inquiries": ACCESS_WRITE,
    "tickets": ACCESS_WRITE,
    "traces": ACCESS_READ,
    # analytics（成效指标）与 dashboard 一致：属管理视角，坐席默认不授权，需管理员显式授予
    "knowledge": ACCESS_READ,
    "memory": ACCESS_WRITE,
}
