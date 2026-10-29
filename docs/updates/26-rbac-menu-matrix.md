# U26 后台 RBAC：角色 × 菜单读写矩阵

> 状态：已落地（`SCHEMA_VERSION` 由 `9` 升到 `10`，`scripts/migrations/0010_rbac.sql`）
> 范围：菜单模块级的「角色 → 可见菜单 → read/write」授权；**不含**场景/知识库/工单的资源级授权
> 关联：[Spec §12.2 权限矩阵](../spec.md#122-rbac-权限矩阵)、[API · Admin · RBAC](../api.md)、
> [U07 人员/客户分表](07-staff-customer-auth-split.md)、[U21 人员配额](21-scene-identity-runtime-quota.md)、[U23 License](23-license-edition.md)

## 1. 背景与问题

U07 把账号拆成 `staff_users` / `customer_users` 并引入了 `agent` / `admin` 两个角色，但后台只能用
`require_staff` / `require_admin` 两个**粗粒度**守卫：

- 坐席天然能看到全部后台菜单（含人员、授权、Agent 配置），无法只给「接管 + 咨询 + 工单」；
- 想新增一种岗位（如只读的质检、只管知识库的运营）只能改代码加角色常量，前后端两处同步；
- 前端菜单与后端守卫各写一份判断，容易不一致：菜单能看见但接口 403，或菜单隐藏但接口可调。

因此引入**菜单模块级** RBAC：授权对象是「菜单模块 + read/write」，而不是「某个接口」或「某行数据」。
这一层足够表达后台常见岗位，又不需要为每个资源写规则；资源级（这一个人只能看 A 场景的工单）留给
License 的 `rbac` 企业功能位。

## 2. 设计要点

1. **单一事实源在代码，不在数据库**：`app/core/rbac.py` 的 `MENU_MODULES` 登记 17 个模块的
   `key / name / group`。前端菜单叶子、后端 `require_perm` 用它，角色配置页的矩阵也由它渲染；
   库里只存「角色 → `{menu_key: read|write}`」的映射。
2. **零 DDL 扩展新模块**：`role_permissions.permissions` 是 JSONB，key 没有外键约束；新增菜单只需
   在 `MENU_MODULES` 加一行 + 前端加一条路由，**不需要建表 / ALTER / 迁移**（U25 的 `analytics` 即如此）。
3. **只有两级 access**：`read`（菜单可见 + 可浏览/查询）⊂ `write`（可改，含只读全部）。
   `access_satisfied(held, required)` 用秩比较，`write` 自动覆盖 `read`；`None` 视为无权限。
   不区分「能删不能改」这类动作粒度——需要时再加等级，不改表结构。
4. **admin 是超级角色，前后端都旁路**：数据库里 admin **不落 `role_permissions` 行**，
   `effective_permissions()` 直接返回全模块 write，前端 `canAccess()` 对 `role === 'admin'` 恒真。
   这样升级到 RBAC 后，管理员行为与过去 `require_admin` 完全一致（纯增量）。
5. **agent 默认授权贴合历史可见范围**：`DEFAULT_AGENT_GRANTS` = takeover / inquiries / tickets / memory
   写，traces / knowledge 读；`dashboard` / `analytics` 属管理视角，**默认不授予**。
   迁移 0010 会按这张表给内置 `agent` 角色 seed 权限行，保证坐席菜单不回退。
6. **主角色列保留，多角色走关联表**：`staff_users.role`（`agent|admin`）仍是 Token 与粗粒度守卫的依据，
   `user_roles` 存多对多。一人多角色时有效权限取**并集**（`write` 覆盖 `read`）。
7. **写入即白名单过滤**：写接口经 `_clean_permissions()` 丢弃不在 `MENU_KEYS` 里的 key，而不是报错
   ——前端多传一个已下线模块（如 `usage`）不会把请求打成 500，也不会把脏 key 写进库；
   而**等级**非 `read|write` 的会在 schema 层被 `Literal` 直接拦成 422（`_clean_permissions` 里的等级判断只做防御）。
8. **未纳入的模块保持粗粒度**：接入方管理（`/integrations`）仍按 `meta.roles=['admin']` /
   `require_admin`，因为它本身就是管理员的集成配置（入口在「业务场景」页里，不在侧边菜单）；
   将来若要授权给坐席，加一个 `integrations` key 即可。

## 3. 数据模型

`0010_rbac.sql`（幂等：`CREATE TABLE IF NOT EXISTS` + seed `ON CONFLICT DO NOTHING`）：

| 表 | 关键列 | 说明 |
|---|---|---|
| `roles` | `key` UNIQUE、`name`、`description`、`is_system` | 内置 `admin` / `agent` 的 `is_system=true`，固定 UUID（`…00a1` / `…00a2`）与 `init_db.sql` 一致 |
| `user_roles` | `(user_id, role_id)` 主键 | 人员 ↔ 角色多对多，双向外键 `ON DELETE CASCADE`；`idx_user_roles_role` 供「在册人数」统计 |
| `role_permissions` | `role_id` UNIQUE、`permissions JSONB` | 一角色一行，整体替换；不存行级/资源级规则 |

迁移同时把存量 `staff_users.role` 回填进 `user_roles`（`JOIN roles ON r.key = u.role`，`ON CONFLICT DO NOTHING`），
所以升级后老账号的权限与升级前一致。

## 4. 关键实现

| 文件 | 职责 |
|---|---|
| `backend/app/core/rbac.py` | `MENU_MODULES`（17 个模块 + 分组）、`MENU_KEYS`、`ACCESS_LEVELS`、`access_satisfied()`、`SYSTEM_ROLES`、`DEFAULT_AGENT_GRANTS`、`all_write_permissions()` |
| `backend/app/api/deps.py` | `effective_permissions(user)`（admin 全量 write / 其余取并集）、`require_perm(menu_key, access="write")`（非后台人员 403、admin 旁路、其余按矩阵判定） |
| `backend/app/models/entities.py` | `Role` / `UserRole` / `RolePermission` 三个 ORM，与 0010 对齐 |
| `backend/app/models/repositories.py` | `RoleRepo`：角色 CRUD、`get/set_permissions`（upsert）、`user_counts()`、`resolve_permissions()`（并集）、`get_user_role_keys()` / `assign_user_roles()`（整体替换） |
| `backend/app/api/v1/admin_rbac.py` | 7 个接口（见下），写操作落审计 `admin.rbac.role_create` / `role_delete` / `role_grant` |
| `backend/app/api/v1/auth.py` | 登录与 `/auth/me` 下发 `roles`（角色 key 列表）与 `permissions`（有效矩阵），前端据此渲染菜单 |
| `backend/app/api/v1/*.py` | 各后台接口把 `require_staff` / `require_admin` 换成分模块的 `require_perm(...)`；`/admin/integrations` 仍用 `require_admin` |
| `backend/app/api/v1/admin_staff.py` + `schemas/api.py` | 人员的 `roles: list[str]` 入参（整体覆盖角色集合，`role` 列同步为主角色）；不能改自己的角色/配额/启停 |
| `admin-frontend/src/{router/index.ts,layouts/AdminLayout.vue}` | 路由 `meta.perm` 与菜单 `perm` 一对应；菜单显隐、`firstAllowed()` 兜底、路由守卫三处共用同一判定 |
| `admin-frontend/src/stores/auth.ts` | `canAccess(user, key, access)`：admin 恒真，其余比较 `user.permissions` 的等级秩 |
| `admin-frontend/src/views/RoleView.vue` | 角色列表（内置标记、授权模块数、在册人数）+ 矩阵对话框（按 group 分组、只读/读写单选、全部只读/全部读写） |
| `admin-frontend/src/views/StaffQuotaView.vue` | 人员表格展示角色标签；编辑弹窗多选角色（选项来自 `/admin/rbac/roles`，失败回退内置两个 key） |

## 5. 接口契约

```
GET    /api/v1/admin/rbac/menus                         # MENU_MODULES 原样返回（分组 key/name）
GET    /api/v1/admin/rbac/roles                         # [{id,key,name,description,is_system,permissions,user_count}]
POST   /api/v1/admin/rbac/roles                         # body: {key,name,description?,permissions?}
PATCH  /api/v1/admin/rbac/roles/{role_id}               # body: {name?,description?}（key/is_system 不可改）
DELETE /api/v1/admin/rbac/roles/{role_id}               # 内置角色 400（1001）
GET    /api/v1/admin/rbac/roles/{role_id}/permissions   # {menu_key: "read"|"write"}
PUT    /api/v1/admin/rbac/roles/{role_id}/permissions   # body: {permissions:{...}}，整体替换
```

- 全部要求 `admin`（`require_admin`）；`roles` 模块的 `write` 授权不替代这里的 admin 限制——
  避免「能改角色的人」自行提权。
- `POST` 的 `key` 规则 `^[a-z0-9][a-z0-9_-]*$`、2–32 位；重复 key 返回 409（`3002`）。
- 非法 `menu_key` 在接口层过滤后**静默丢弃**（只保留 `MENU_KEYS` 之内的）；权限值不是 `read` / `write`
  的在 schema 层就返回 422；`PUT` 传空对象 = 清空该角色全部授权。
- 删除自定义角色走 CASCADE，连带删掉授权行与人员映射；删之前不存在「仍在用则拒绝」的拦截
  （角色只是权限集合，人员失去授权后菜单自然消失）。

## 6. 前端行为

1. **菜单**：`AdminLayout` 按 `MENU` 里的 `perm` 过滤；没有 `read` 的模块不渲染。分组（咨询与工单 /
   知识与记忆 / 监控与分析 / 场景与 Agent / 工具集成 / 系统管理，外加单列的「人工接管」）本身不授权，
   跟着子项显隐；「接入方管理」不在菜单树里，只能从「业务场景」页跳转（`meta.roles=['admin']`）。
2. **落地页**：访问 `/` 时 `firstAllowed()` 返回第一个可读模块（按 `MENU` 顺序），兜底 `/inquiries`。
3. **守卫**：直接改 URL 访问无权限模块会被送回 `firstAllowed()`；若兜底目标本身也无权限
   （空授权账号），**放行**由页面处理 403，避免重定向死循环。
4. **权限时效**：后端每次请求实时查库，改矩阵**立即生效**；前端菜单用的是登录/`/auth/me` 时缓存的
   `user.permissions`，所以被授权/撤销的人刷新页面后才看到菜单变化（无需重新登录）。
5. **自我保护**：人员页不允许改自己的角色集合（含「含管理员即超级权限」提示），与后端
   「不能停用/降级自己、不能改自己配额」的防自锁规则同源。

## 7. 验证

- `backend/tests/test_admin_rbac.py`（13 项）：菜单清单完整性；角色列表带矩阵与在册人数；重复 key 409；
  创建时非法等级 422、非法 key 被过滤；保存时同样过滤；内置角色不可删、自定义角色可删；`require_perm` 的
  admin 旁路、非后台人员 403、`write` 通过、`read` 不足以写、`read` 足以读、无授权模块 403。
- `backend/tests/test_admin_analytics.py`：坐席无授权访问分析接口 403（`2003`）、显式授予
  `analytics:read` 后 200，`/admin/rbac/menus` 含 `analytics`。
- `backend/tests/test_migrations.py`：`schema_meta.version` 与 `migrations/` 目录、`init_db.sql` 三者一致（`10`）。
- 全量回归：`pytest` 646 passed / 3 skipped；`ruff check app tests` 无告警；`admin-frontend` `npm run build` 通过。

## 8. 边界与后续

- **不是资源级授权**：不能表达「只给 A 场景的接管」「只读 B 知识库」。这类需求对应 License 的
  `rbac` 功能位（[U23](23-license-edition.md)），开源版未实现，`require_feature("rbac")` 也还没有端点使用。
- **不做动作级细分**：`write` 是模块内的全动作；「能改不能删」这类诉求需要再加等级或动作位。
- **角色变更不即时推送**：前端权限来自 `/auth/me`，多人协作场景下需刷新；暂不做 SSE 推送或定时刷新。
- **只覆盖后台人员**：客户侧接口的边界仍是 `current_customer`（只能看自己的会话/记忆），与菜单无关。
- **未做**：角色模板/导入导出、权限变更历史查询页（审计只落 `audit_logs`）、按角色限制数据范围、
  SSO（企业功能位 `sso`）。
