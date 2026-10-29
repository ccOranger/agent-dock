# 谁能看见、谁在说话：后台权限矩阵与会话状态机的边界设计

> 面向做客服/运营后台的后端与前端工程师。两个经常被混在一起的问题：**权限**（这个人能看到哪些模块）和**状态**（这个会话此刻该由机器人还是人来回）。
> AgentDock 把前者交给菜单模块级 RBAC，后者交给一条四态会话状态机。代码见 `backend/app/core/rbac.py`、`backend/app/api/deps.py`、`backend/app/api/v1/admin_rbac.py`、`backend/app/api/v1/admin_takeover.py`、`backend/app/services/chat_service.py`。

## 1. 先分清两个正交的问题

"这个坐席能不能回这条消息"，实际是两条独立的判定链：

| 问题 | 判定依据 | 失败表现 |
|---|---|---|
| 他能不能打开「人工接管」这个模块？ | RBAC：角色 → 菜单模块 → `read`/`write` | 菜单看不见，或接口 403 |
| 这个会话现在允许人工回复吗？ | 会话状态机：`handover` / `human_handling` | 回复接口 409，或机器人抢着回 |

把两者混在一起（"管理员就能回任何会话"）会立刻产生两个后果：机器人还在跑的时候坐席也能插话，以及不同岗位没法只给一部分菜单。所以先分层，再各自收口。

## 2. RBAC：从两个粗粒度守卫到 16 个模块

最初只有 `require_staff` / `require_admin`。它的边界很清楚，但表达力接近零：坐席要么能看全部后台（含人员、授权、Agent 配置），要么什么都看不到。想加一个"只读质检岗"只能改代码加角色常量，前后端两处同步。

现在的做法是**菜单模块级 RBAC**：授权对象是「菜单模块 + 读写等级」，而不是某个接口或某行数据。

```
roles ──< role_permissions (JSONB: {menu_key: "read"|"write"})
  └──< user_roles >── staff_users
```

### 单一事实源放在代码里

16 个模块的 `key / name / group` 硬写在 `app/core/rbac.py` 的 `MENU_MODULES` 里，数据库只存「角色 → `{menu_key: read|write}`」的映射。

这样做换来一个很实际的好处：**新增菜单模块零 DDL**。`role_permissions.permissions` 是 JSONB，key 没有外键约束，加一个模块只需要在 `MENU_MODULES` 加一行、前端加一条路由，不需要建表、`ALTER` 或迁移。（U25 加「数据分析」时就是这样。）

反过来，如果把模块清单也放数据库，就得为"模块下线后残留的授权 key"设计清理逻辑——现在靠白名单在写入时丢弃即可。

### 只有两级，但两级够用

`read`（菜单可见 + 可查询）⊂ `write`（可改，含只读全部），用秩比较：

```python
_ACCESS_RANK = {"read": 1, "write": 2}
def access_satisfied(held, required):
    return held is not None and _ACCESS_RANK.get(held, 0) >= _ACCESS_RANK.get(required, 99)
```

不区分"能改不能删"这类动作粒度。理由是后台岗位的现实分布：绝大多数需求是"能看"和"能改"的区分，动作级细分会让矩阵从 16 行膨胀到几百个复选框，运营根本配不明白。真需要时再加等级，表结构不用动。

### admin 必须旁路，否则升级即事故

内置 `admin` 角色在数据库里**不落 `role_permissions` 行**：`effective_permissions()` 直接返回全模块 `write`，前端 `canAccess()` 对 `role === 'admin'` 恒真。

这一条是纯增量改造的关键。如果让 admin 也走矩阵，那么升级当天所有管理员都会因为"没有授权行"而失去菜单——运维还没来得及配。同理，内置 `agent` 角色的默认授权是按历史 `require_staff` 的可见范围 seed 的（`takeover/inquiries/tickets/memory` 写，`traces/knowledge` 读），保证升级后坐席菜单不回退。

注意 `dashboard` 与 `analytics` **默认不给坐席**：指标属管理视角，要看就由管理员显式授予。默认收敛比默认放开更好收场。

## 3. 写入侧的三个细节

**① 未知 key 静默丢弃，不报错。**
前端多传一个已下线模块（比如 `usage`）不该把请求打成 500，也不该把脏 key 写进库。`_clean_permissions()` 只保留 `MENU_KEYS` 之内的项。

**② 非法等级在 schema 层就 422。**
`Literal["read", "write"]` 直接拦住 `"admin"`、`"delete"` 这类值，比在业务层写防御判断更早、更准。

**③ 改角色的接口仍然只给 admin。**
这点容易被忽略：既然有了 `roles` 模块的 `write` 授权，是不是有 `roles:write` 的人就能改角色？不行——`/admin/rbac/*` 全部 `require_admin`。否则等于"任何拿到 `roles:write` 的人都能给自己加满权限"，RBAC 当场失效。

## 4. 前端：三处共用同一个判定

菜单显隐、路由守卫、落地页兜底如果各写一份判断，迟早出现"菜单能看见但接口 403"或"菜单隐藏但直接改 URL 能进"。

`AdminLayout` 的菜单（`meta.perm`）、`router.beforeEach` 的守卫、`firstAllowed()` 的兜底全部调用同一个 `canAccess(user, key, access)`，数据来自 `/auth/me` 下发的 `permissions`。

守卫里有一个必须处理的分支：**兜底目标自己也无权限时直接放行**，由页面处理 403。否则空授权账号会在"重定向到第一个可看模块"和"该模块也不可看"之间死循环，表现为白屏。

### 一致性的运维含义

后端每次请求实时查库，改矩阵**立即生效**；前端菜单用的是登录时缓存的 `permissions`，被授权/撤销的人需要**刷新页面**才看到变化（不必重新登录）。

这不是 bug，但必须写进文档：客户现场"我给他加权限了怎么还没变"这一类工单，90% 是没刷新。

## 5. 接管：四态状态机

```
active ──(低置信 / escalate 工具)──> handover（排队待接入）
handover ──(坐席 claim)──> human_handling（机器人让位）
human_handling ──(release to=bot)──> active
human_handling ──(release to=close)──> closed
```

两个进入 `handover` 的入口语义不同，必须都覆盖：

- **路由置信度低于阈值**：系统判断"答不了"；
- **Agent 调用了 `escalate` 工具**：业务判断"不该答"（投诉、资金安全等）。这条路径还会把最近未关闭工单置为 `escalated`，并写一条审计。

工具升级的路径有个实现细节：工具执行完会话状态要置 `handover`，但这一步是**旁路写**——`mark_handover` 失败只记日志，不能因为状态写失败而让"已经升级成功"的工具调用报错。`closed` / `human_handling` 不覆盖，避免把人工正接手的会话打回队列。

### 让位契约：人接管期间机器人必须"闭嘴"

`chat_service` 在两条链路上都做了同一个前置判断（`stream` 与 `reply`）：

```python
if session.status in (SessionStatus.HUMAN_HANDLING, SessionStatus.HANDOVER):
    await SessionRepo.touch(session.id, db=db)      # 刷新 updated_at，队列里能上浮
    collector.record_outcome(OUTCOME_HANDOVER, reason=takeover_status)
    # 只落库，不跑编排、不产生助手消息
```

这里有两个容易漏的点：

1. **排队中（`handover`）也要让位**，不只是已接管。否则坐席还没接，机器人已经又答了一轮，客户看到的是"转人工了但机器还在说话"。
2. **必须 `touch`**。接管期间不产生助手消息，`updated_at` 不会自然更新，靠 `updated_at` 排序的队列就会把刚追问的会话摆到错误位置。

## 6. 抢单、越权与释放

**抢单**走数据库状态判定，不是内存锁：

```python
# SessionRepo.claim_takeover：handover 或「本人已接入」才置 human_handling
# 返回 Session=成功；True=他人占用；None=不存在
```

同一坐席重复 `claim` 幂等成功——断线重连、刷新页面都能回到自己的会话，这是一种很常见但常被漏掉的体验。他人已占用返回 `409`，措辞要说清是"已被其他坐席接入"，而不是笼统的"状态不允许"。

**越权写**由 `_require_handling` 统一挡：

- 会话不在 `human_handling` → `409`；
- 操作者不是 `assigned_agent` 且不是 `admin` → `403`。

`admin` 例外是为了"调岗/救火"：管理员可以替别人释放会话。这条例外只放开写操作，不放开"假装自己是接手人"的语义。

**释放**保留 `assigned_agent` 不清空——它是"谁处理过这条会话"的唯一追溯依据。清掉之后，事后复盘就只能翻 `audit_logs`。

## 7. 坐席的话不能进机器人上下文

人工回复以 `role="assistant"` + `routed_to="staff"` + `metadata.sender_type="staff"` 落库。三个字段各有用途：

| 字段 | 用在哪 |
|---|---|
| `role=assistant` | 前端气泡与历史顺序 |
| `routed_to="staff"` | 前端区分人工/机器人；`MessageRepo.list_recent` 把它排除 |
| `metadata.sender_type` | 后台筛选与审计 |

关键是第二行：如果坐席的话进了机器人上下文，一旦交还机器人（`release to=bot`），模型会**复述坐席说过的承诺**——比如坐席答应的补偿方案，机器人换个说法再确认一遍，客户会当作二次承诺。这类事故很难在测试里发现，只能靠上下文过滤从源头掐掉。

## 8. 审计：只记"会发生后果"的动作

| 动作 | 落到 `audit_logs` |
|---|---|
| 升级人工（工具） | `escalate`（含用户、会话、原因） |
| 建/删角色 | `admin.rbac.role_create` / `role_delete` |
| 保存授权 | `admin.rbac.role_grant`（记条数，不记完整矩阵） |

不记"看了一眼"这种读操作，否则审计表会被淹没。授权变更只记条数是个取舍：矩阵本身可从 `role_permissions` 反查当前值，但**历史值不可查**——需要"谁在什么时候收走了谁的权限"时，得额外做变更历史表（当前未做）。

## 9. 失败模式清单

| 症状 | 根因 | 处置 |
|---|---|---|
| 菜单能看见，点进去 403 | 前端 `meta.perm` 与后端 `require_perm` 的 key 不一致 | 两边都从 `MENU_MODULES` 取 key |
| 加了权限但不生效 | 前端缓存 `/auth/me`，未刷新 | 刷新页面；或用后端接口验证（实时查库） |
| 升级后管理员没了菜单 | admin 也走了矩阵判定 | admin 必须旁路（前后端各一处） |
| 空授权账号白屏 | 守卫兜底目标自己也无权限，重定向死循环 | 兜底目标无权限时放行，交页面处理 |
| 转人工了机器人还在答 | 只判断了 `human_handling`，漏了 `handover` | 两个状态都要让位 |
| 队列里新追问的会话排到后面 | 接管期间不产生助手消息，`updated_at` 未更新 | 让位分支里显式 `touch` |
| 两人同时接入，后到的抢走 | 用内存标记判断占用 | 状态判定放数据库，冲突返回 409 |
| 交还机器人后它复述坐席的话 | 坐席消息进了机器人上下文 | `routed_to="staff"` 并在 `list_recent` 排除 |
| 有 `roles:write` 就能给自己提权 | 角色接口用了 `require_perm("roles")` | 角色接口固定 `require_admin` |

## 10. 边界与未做

- **不是资源级授权**：表达不了"只给 A 场景的接管""只看 B 知识库"。这类需求需要行级规则，当前没有。
- **不做动作级细分**：`write` 是模块内全动作。
- **权限变更不实时推送**：依赖前端刷新，不做 SSE 或轮询。
- **删除角色不检查是否在用**：自定义角色被删后，挂在上面的人直接失去对应授权（CASCADE），没有"该角色仍有 3 人在用"的拦截。这是有意的取舍——角色只是权限集合，不是组织架构。
- **未做**：角色模板/导入导出、权限变更历史页、SSO、按角色限制数据范围。

## 11. 小结

把权限和状态分开处理之后，两边的规则都变短了：

1. **权限回答"能不能看"**：单一事实源在代码、两级等级够用、admin 与默认坐席都要保证升级不变行为；
2. **状态回答"现在谁说话"**：四态、两个入口、两条链路都要让位、状态判定放数据库；
3. **交界的两个细节**：坐席的话不能进机器人上下文；有授权不等于有特权（角色接口仍只给 admin）。

后台系统真正难的不是把功能做出来，而是让"谁在什么时候能做什么"这句话，在代码里有唯一、可验证的落点。
