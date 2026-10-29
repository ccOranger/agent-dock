# U25 数据分析（成效指标，analytics）

> 状态：已落地（**无 schema 变更**，`SCHEMA_VERSION` 保持 `10`；菜单 key 仅登记在 `app/core/rbac.py`）
> 修订：原「用量报表」的请求数与日维度统计已并入本页，`/usage` 页面与 `/admin/usage/*` 接口下线，token 不再对外展示
> 关联：[API · Admin · Analytics](../api.md)、[U22 用量计量](22-usage-metering.md)、[U24 评测与回归](24-eval-regression.md)、[RBAC 设计](../productization-plan.md)

## 1. 背景与问题

仪表盘只有资源概览与 Top5，回答不了"这周效果怎么样"：转人工多不多、机器人能自己答掉多少、
工具到底调用成功没有、兜底和 RAG 空命中是否在恶化。

这些数字散在三处且口径不同：

- `chat_usage_daily` 全量记账（requests / handoffs / errors / tokens），可对账；
- `request_traces` 有工具调用/失败、兜底、RAG 片段数等扁平列，受 `TRACE_SAMPLE_RATE` 采样；
- `eval_runs` 是离线评测，不能代表线上。

原「用量报表」把第一类数据单独做成一页，但它真正有价值的内容只有"请求数 + 每日统计"：token 是估算值
（`hello_agents` 不回传供应商 usage），既不能当账单，也不该出现在对外界面。因此本期把请求数与日维度
统计并入**「数据分析」**，同时下线用量报表页与 `/admin/usage/*`；页面上仍把**全量口径**与**采样口径**
显式分组、分别标注。

## 2. 设计要点

1. **两组三率，分开呈现**
   - 全量三率：转人工率、问题解决率、错误率 ← `chat_usage_daily`（长期保留、无采样偏差）；
   - 链路三率：工具调用成功率、兜底降级率、RAG 命中率 ← `request_traces`（分母是"走完链路并落库的请求"）。
2. **分母为 0 返回 `null` 而不是 0**：没有样本和"成功率 0%"是两件相反的事。前端显示「—」，
   副文案明确「暂无样本」，避免把无数据读成效果崩盘。
3. **解决率口径写死在文案里**：`(requests - handoffs - errors) / requests` 只是"机器人自主应答率"，
   不代表用户满意度（满意度需要 👍/👎 采集，本期未做）。同时防御性取 `max(0, ...)`，脏数据不出负值。
4. **采样提示不可省**：`TRACE_SAMPLE_RATE < 1` 时接口返回 `meta.sampled=true`，页面常驻告警 + 卡片"采样"角标；
   采样率 1.0 标注「全量链路」。工具成功率在采样下会**低估**（失败可能没被采到），必须讲清楚。
5. **同一时间窗**：日聚合按进程本地日期记账，链路按 `timestamptz` 落库。接口把 `from/to` 转成
   **本地日的 aware datetime** 再过滤 `request_traces`，两边才是真正同一区间（用 UTC 边界在国内部署会偏 8 小时）。
6. **零 DDL**：`analytics` 只是 `MENU_MODULES` 里的一行；角色授权是 `role_permissions.permissions` JSONB，
   key 无数据库约束，新增模块无需建表/ALTER/迁移。坐席默认不授予（与 `dashboard` 一致），需管理员显式打开。
7. **合并用量报表，去掉 token**：日趋势与每日明细直接复用 `UsageRepo.summary(group_by="day")` 的 `rows`
   （totals 本来就与三率同源，一次查询同时拿到合计与日行）；接口对日行做**字段白名单**
   （`day / requests / handoffs / errors`），即使仓储以后多返回列也不会把 token 泄到前端。
   库里 `chat_events.total_tokens` 与 `chat_usage_daily.tokens` 照常累加，只是不再对外输出（以后接入真实 usage 可复用）。
8. **只留一个入口**：`usage` 从 `MENU_MODULES` 删除，`/usage` 路由、`UsageView.vue`、`/admin/usage/*`、
   `UsageRepo.export_rows` 一并删除；已存在角色授权里残留的 `usage` 键不会报错也不会生效，
   下次保存该角色时被 `_clean_permissions` 按白名单清掉。

## 3. 关键实现

| 文件 | 职责 |
|---|---|
| `backend/app/core/rbac.py` | `MENU_MODULES` 新增 `analytics`（数据分析 / 观测）、删除 `usage`；`DEFAULT_AGENT_GRANTS` 不含 `analytics` |
| `backend/app/models/repositories.py` | `TraceRepo.outcome_summary(...)`：一次查询返回 `traces / tool_calls / tool_errors / fallbacks / rag_hits`；删除只服务于导出接口的 `UsageRepo.export_rows` |
| `backend/app/api/v1/admin_analytics.py` | `GET /admin/analytics/outcomes`：日期校验与 `USAGE_QUERY_MAX_DAYS` 跨度上限内置（默认近 7 天），组装六个 `{value, numerator, denominator}` + `daily` + `meta` |
| `backend/app/api/v1/router.py` | 挂载 `admin_analytics.router`；移除 `admin_usage.router` |
| `admin-frontend/src/views/AnalyticsView.vue` | 数据分析页（`/analytics`）：区间 + 场景筛选、两组六卡（带图标徽标与色调：成功绿 / 风险橙红 / 中性蓝）、采样提示、空值「—」、每日请求量折线图（按区间补 0，标合计/峰值，单日明细看 tooltip，不另列表格） |
| `admin-frontend/src/components/LineTrendChart.vue` | 每日请求量折线图：ECharts 按需引入（`LineChart` + `Grid/Tooltip/DataZoom` + `CanvasRenderer`）；`> 62` 天开滚轮缩放；`ResizeObserver` 跟随卡片宽度；卸载 `dispose` 防泄漏 |
| `admin-frontend/src/{router,layouts/AdminLayout.vue}` | 路由与「监控与分析」分组菜单项（`perm: 'analytics'`）；删除 `/usage` 与「用量报表」菜单 |
| `admin-frontend/src/{api,types}` | `apiAnalyticsOutcomes` + `AnalyticsOutcomes` / `AnalyticsRate` / `AnalyticsDailyRow`；删除用量报表的请求函数与类型 |

## 4. 接口契约

```
GET /api/v1/admin/analytics/outcomes?from=YYYY-MM-DD&to=YYYY-MM-DD&scene_code=
```

- 鉴权：`require_perm("analytics", "read")`；无授权 403（业务码 2003）。
- 默认区间：最近 7 天（含今天）；日期格式非法 / `from > to` / 跨度超 `USAGE_QUERY_MAX_DAYS` → 400（1001）。
- 响应：`handover_rate` / `resolution_rate` / `error_rate` / `tool_success_rate` / `fallback_rate` / `rag_hit_rate`
  均为 `{ value: float|null, numerator: int, denominator: int }`；
  `daily = [{ day, requests, handoffs, errors }]`（按天升序，只含有请求的日期、不补零行，无 token）；
  页面折线图在 [from, to] 上补 0（日聚合无行 = 当天 0 次请求），否则缺日直连会把 3 天的量画成 1 天；
  单日请求/转人工/异常看折线 tooltip（原先折线下的明细表已删除，与图表信息重复）。
  `meta = { full_source, trace_source, trace_sample_rate, sampled, requests, traces_covered }`。
- 已下线：`GET /admin/usage/summary`、`GET /admin/usage/export.csv`（调用方改调本接口；不再提供 CSV 导出）。

> 图表：此前仪表盘/链路页的柱状条是 CSS 手绘（不引图表库），本页折线的手绘版观感不佳、
> 坐标轴与 tooltip 细节都要自己维护，故只在本页引入 ECharts，且按需注册组件（只打包折线相关模块）。
> echarts 只被 `/analytics` 这个懒加载路由引用，不会进入首屏与主应用的 chunk。

## 5. 验证

- `backend/tests/test_admin_analytics.py`（13 项）：六个比率的分子分母、解决率不为负、空数据六项全 `null`、
  默认近 7 天与本地日边界、场景透传、`meta.sampled` 在 0.2 / 1.0 下的取值、非法日期与跨度超限 400、
  坐席无授权 403 / 显式授予 `analytics:read` 后 200、`/admin/rbac/menus` 含 `analytics`、
  `usage` 已从菜单 key 移除，以及 `daily` 只输出四个字段（token 不外泄）。
- `backend/tests/test_usage.py` 只保留采集侧（token 估算、链路关闭仍计数、`chat_events` 回填与日聚合 upsert），
  接口与 CSV 用例随页面一起删除。
- 全量回归：`pytest` 646 passed / 3 skipped（用量报表接口用例随页面删除）；`ruff check app tests` 无告警；`admin-frontend` `npm run build` 通过。
- 真实库抽验（本机近 7 天）：`usage totals = requests 392 / handoffs 33 / errors 12`，
  `trace counts = traces 225 / tool_calls 141 / tool_errors 0 / fallbacks 0 / rag_hits 117`；
  换算为转人工 8.4%、解决率 88.5%、错误 3.1%、工具成功 100%、兜底 0%、RAG 命中 52.0%，查询均可执行。

## 6. 边界与后续

- **幻觉率不做**：没有 ground-truth，不能拿线上流量硬算；后续走离线评测 LLM-as-judge。
- 工具成功率要全量精确，需在 `chat_events` 增加 `tool_call_count / tool_error_count` 两列并走一次迁移（本期不做）。
- 本页预留 P2/P3 扩展（场景/接入方/渠道分布、结局分布、转人工原因 Top、延迟 p50/p95、
  人工接管时效、工单存量与积压）：多数是 `chat_events` / `request_traces` 聚合，零新增埋点。
- **未做**：CSV 导出（原用量报表的能力随页面删除；若以后要交付账目，应先有真实 usage 口径再设计）；
  token 若从估算转为供应商真实值，再决定是否恢复展示。
