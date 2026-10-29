# U24 评测与回归（eval_runs + 真实链路执行器）

> 状态：已落地（**schema 升到 8**，新增表 `eval_runs`）
> 关联：[评测与回归使用手册](../evaluation.md)、产品化计划 [3.4](../productization-plan.md)、[运维手册](../runbook.md)、[开发清单](../development-checklist.md)、决策 D1
> 能力归属：用例集 / CLI 执行器 / Markdown 报告 / `eval_runs` 全在本仓库；后台看板与定时回归尚未实现。

## 1. 背景与问题

W3 之前，这个项目对“效果”的全部回答能力是零：

- 客户第一问「效果行不行」只能靠现场演示即兴回答，没有任何可复核的数字；
- 改一句提示词、换一个模型、调一次 `RAG_TOP_K`，都没有办法证明「没变差」——而交付周期里最容易出事的恰好是这三个动作；
- 实施验收单需要一个能盖章的东西，`request_traces` 里有链路事实，但没有跨时间的对照基线。

初版把评测理解成“跑一遍看看答得对不对”，落地后确认它真正的价值是**变更闸门**：
指标本身不必多精确（token 还是估算值），但必须**同一套口径可重复、可对比、可判退化**。

## 2. 设计要点

1. **走真实 HTTP 链路，不 import Agent**。直接调 Agent 快得多，但测不到鉴权、限流、场景路由、
   固定话术与链路回读——首轮评测就是靠真实链路捞出一个运维可见的产品缺陷（见 5.3）。
   执行器只做两件事：客户端采集 SSE 事件与耗时；服务端按 `trace_id` 回读 `request_traces` / `chat_events`。
2. **SSE 流里没有的事实一律回读，不自造**。RAG 片段数、命中命名空间、工具名、是否兜底、结局来自链路；
   token 来自 `chat_events`（不受 `TRACE_SAMPLE_RATE` 影响）。模型自述不算实测。
3. **分母纪律**（这份报告最容易被质疑的一格）。用例没写这类期望 → 完全不参与；
   写了但服务端没产生可回读事实（链路被采样丢弃） → 计入 `unobserved`，**不进分母**；写了且有判定 → 才进分子分母。
   把“采样丢弃”算成失败，就会把基础设施问题误报成效果退化。
4. **用例集指纹锁 diff**。`fingerprint()` 只取参与判定的字段（`case_id` + `query` + `setup` + `expect`），
   与 `note` / `tags` 无关；`--scene` / `--tag` / `--limit` 过滤之后再算，所以 3 条冒烟的子集不会去对 39 条的全量基线。
   指纹不同 → `comparable=False`，只展示差值、不判退化。
5. **退化容差分两类**。比率类绝对下降 > 2 个百分点（`REGRESSION_ABS=0.02`）；
   耗时/消耗类相对上升 > 15%（`REGRESSION_REL=0.15`）。兜底率与转人工率方向相反（越小越好）。
   命中任一退化 → 退出码 2，CI 上就是流水线红。
6. **纯逻辑与执行分离**。校验、指纹、打分、聚合、diff、报告渲染全在 `app/services/eval_runner.py`，
   不发请求、不碰 DB，因此 47 项单测全部用 mock 观测跑，不打真实 LLM（CI 可跑、结果确定）。
7. **运行结论落库**。报告是文件，在客户环境里会丢；`eval_runs` 存指标 + 指纹 + 基线指向，
   用来回答“上一次跑是多少”，也是将来后台看板的数据源。

## 3. 关键实现

| 文件 | 职责 |
|---|---|
| `backend/app/schemas/eval.py` | 数据模型：`EvalExpect`（18 个可选期望字段，按判定归为 11 组）、`EvalCase`（含 `setup[]` 多轮铺垫）、`EvalObservation`、`CaseResult`、`MetricPoint`、`EvalMetrics`、`MetricDiff`、`EvalRunSummary`；加载时的结构校验（重复 case_id、空文件、场景不一致） |
| `backend/app/services/eval_runner.py` | 纯逻辑：`load_case_files` / `load_cases` / `fingerprint` / `planned_checks` / `score_case` / `percentile` / `aggregate` / `diff_metrics` / `has_regression` / `render_report`；14 个 `CHECK_*` 检查点名；`REGRESSION_ABS` / `REGRESSION_REL` |
| `scripts/eval.py` | 执行器：探活 → 取身份 → 并发跑 SSE → 按 `trace_id` 回读链路 → 打分聚合 → 基线检索（DB 或 sidecar）→ 出 `.md`/`.json` → 写 `eval_runs`；退出码 0/1/2/3 |
| `backend/app/models/entities.py` | `EvalRun` 实体（`eval_runs`） |
| `scripts/migrations/0008_eval_runs.sql` | 建表 + 2 个索引（`(scene_code, started_at DESC)`、`(fixture_hash, started_at DESC)`），全 `IF NOT EXISTS` 可幂等补跑 |
| `scripts/init_db.sql` / `scripts/verify_schema.py` / `backend/app/core/schema.py` | 新库直建、列级校验、`SCHEMA_VERSION = "8"` |
| `backend/tests/fixtures/eval/customer_service.json` | 39 条（`demo_corpus` 23 + `manual` 16） |
| `backend/tests/fixtures/eval/default.json` | 11 条（`manual` 6 + `demo_corpus` 5） |
| `backend/tests/test_eval_runner.py` | 47 项，全部 mock，含仓库用例集的结构与来源标注约束 |
| `backend/app/api/v1/chat.py` | `trace_id` 复用中间件值（评测暴露的产品缺陷，见 5.3） |
| `scripts/seed_data.py` | 演示 Agent 配 `rag_namespaces` 多库绑定（评测暴露的配置缺陷，见 5.2） |
| `.gitignore` | `reports/` 不入库，指标与明细已落 `eval_runs` |

## 4. 数据结构与契约

`eval_runs` 一行 = 一次 `scripts/eval.py` 运行：

| 列 | 说明 |
|---|---|
| `run_tag` | 人工标注本次对应哪个版本/变更（报告标题引用） |
| `scene_code` / `base_url` / `model_name` | 换模型即换基线，指标不可混比 |
| `fixture_hash` | 用例集内容指纹（sha256 前 16 位）；`--baseline latest` 就是按它 + 场景检索最近一次 |
| `case_count` / `passed_count` | 通过率分子分母 |
| `metrics` | JSONB，全部指标含 `num` / `den` / `unobserved` 三个数 |
| `detail` | JSONB 数组，逐用例失败项、trace_id、截断后的实际应答 |
| `report_path` / `baseline_run_id` / `duration_ms` / `started_at` / `finished_at` | 报告文件指向、基线链条、耗时 |

指标口径与 18 个期望字段的完整对照见 [evaluation.md 第 3–4 节](../evaluation.md)。
P95 用**最近邻法**（排序后取第 `ceil(0.95n)` 个观测值）——几十条样本上做插值只会让数字看着更精确而不可核对。

一次运行的 sidecar `reports/eval-<timestamp>.json` 是 `{summary, cases[]}`：
`summary` 是完整快照（逐用例明细在 `summary.results[]`），`cases` 是本次纳入的用例 id 清单，可直接当离线基线喂给 `--baseline`。

## 5. 首批实测与暴露的问题

本机 `SEED_DEMO=true`、`deepseek-flash`、`TRACE_SAMPLE_RATE=1.0`，完整数据表见 [evaluation.md 第 7 节](../evaluation.md)。

### 5.1 三条验收都拿到了自造证据

| 验收 | 证据 | 结果 |
|---|---|---|
| 同一用例集跑两次指标一致 | `w4-full-3` vs `w4-full-4 --baseline latest`（39 条） | 通过率 92.3%、意图 30/30、工具 7/7、RAG 9/12、转人工 4/38 **逐项相同**；只有 P95 +988ms、平均 token +51 在容差内 → 报告结论「无指标退化」 |
| 改错提示词能显示意图准确率下降 | `w4-deg-base`（`--tag 知识` 10 条）→ 把 `knowledge_qa` 的提示词与路由线索改成「物流异常登记」→ `w4-deg-broken --baseline latest` | 意图准确率 **100% → 30%**，报告标 4 项退化（通过率 −40%、意图 −70%、RAG −40%、转人工 +70%），退出码 **2**；跑完逐项恢复并读回校验 |
| 报告可直接附验收单 | 报告五节结构：结论 → 指标与基线对比（含退化标注） → 指标口径与样本数 → 失败用例 → 全量用例表 | 采纳；`--out-dir` 默认 `reports/`，随交付单归档 |

另跑了 `default` 场景 11 条（`--customer alice`，因该场景未开匿名）：10/11 = 90.9%，
话术类首字 65–94ms、token 80–130，工具命中率显示「—」——分母纪律在真实报告里生效。

### 5.2 首轮 9 条失败的归因（四类，只有一类改了期望）

| 类别 | 现象 | 处置 |
|---|---|---|
| 环境污染（不是产品问题） | 重复 seed 让三个命名空间各剩 9 个**内容完全相同**的重复点，同一块被重复注入 5 次 | 清向量 + 按 `docs/demo/*.md` 重灌；平均 token 2521 → 1437。**修环境不叫迁就结果** |
| 配置缺陷（已回写仓库种子） | `knowledge_qa` 只绑 `cs_default`，而退货运费/发票/退款时效写在售后与订单指南里 → 检回空块只能转人工 | `seed_data.py` 配 `rag_namespaces=["cs_default","cs_order","cs_after_sales"]`、`order_service` 配 `["cs_order","cs_after_sales"]`；RAG 50% → 75%。刻意不用 `search_all_knowledge_bases`（会把 `it_*` 库混进客服场景） |
| 内容粒度（**保留失败**） | 演示文档每篇 < 512 token 整篇成一块，窄问法相似度落在 `RAG_MIN_SCORE=0.3` 之下 | `cs-order-08`、`cs-kb-04`、`cs-kb-05`、`d-kb-04` 继续判失败：不降阈值、不改期望。正确处置是实施时把客户 FAQ 按主题拆条目，这正是报告要替客户提前暴露的问题 |
| 用例口径写错（有代码依据才改） | `cs-edge-01/02/03` 原期望超纲走「兜底应答」；`cs-order-04` 原期望匿名访客直接调订单工具 | 见 5.4 |

### 5.3 评测顺手修掉的真实缺陷：`trace_id` 三处不一致

执行器按 `trace_id` 回读链路命中 0 条，追下去：`main.py` 的 `trace_middleware` 在 `call_next` **之后**覆盖响应头
`X-Trace-Id`，而 `chat.py` 端点自己又 `new_trace_id()` 一次并用于落库 —— 客户端拿到的 id 与库里的不是同一个，
**运维按响应头查链路永远查不到**。这类缺陷只有走真实 HTTP 链路才能暴露，直接 import Agent 测不出来。
修复：端点改用 `_request_trace_id(request)` 复用 `request.state.trace_id`，回归测试
`test_chat_endpoint_reuses_middleware_trace_id` 防复发。

### 5.4 两处用例口径修正的依据（不是改期望迁就结果）

- **超纲 ≠ 兜底应答**。`is_fallback` 只由服务端 `fallback_intent()`（解析失败/无有效意图）置位，
  LLM 自述「按兜底规则处理」不算；本场景池内没有 `default_assistant`，`resolve_fallback_agent` 回落生效清单首项
  （`complaint_handler`）且 confidence 0.3 低于阈值 → `orchestrator` 走 `handover {reason:"low_confidence"}` 并 return
  （**不产生 text**），前端展示「已转人工处理」。所以既定结局是**整轮转人工**，改为 `handover=true` + `fallback=false`；
  想让 `ROUTER_FALLBACK_ANSWER` 生效，前提是场景挂一个中立兜底 Agent。
- **匿名访客没有可信身份**，设计行为是先索要订单号/手机号（`docs/demo/order_guide.md` 第一条即此规则）；
  未确认身份就 `list_orders` 会拉到别人的单。改为期望索要凭证的措辞，带凭证的工具命中由 `cs-order-01/02/03/09` 覆盖。

两处都写进了用例 `note`（含代码位置/文档条目）与 [evaluation.md 7.2](../evaluation.md)。

## 6. 与计划的偏离（已回写 plan 3.4）

1. **`eval_runs` 独立升 schema v8**。计划 3.4 原文写「并入 P0-2 的 schema 升级，避免连续两次升版」，
   排期节也写过「schema 变更统一在 W2 一次性升到 7」。实际没有并：W2 已交付并验证完毕，
   为省一个版本号而回头改已交付的 0007 迁移，风险大于收益。迁移版本号本来就该一版一件事。
2. **来源分层的 60/40 没达成**。仓库交付时库里没有真实客户链路，首批 50 条实际是
   `demo_corpus` 28（56%）+ `manual` 22（44%），`trace_sample` 0 条。
   用 `source` 字段把替换进度显式标出来，现场跑两周后按 `request_traces` 抽样把 `demo_corpus` 换成 `trace_sample`，
   用例结构与判定逻辑都不用改；`test_repo_cases_source_annotation_is_replaceable_with_real_traces` 锁住这个约定。

## 7. 配置项

**不新增环境变量**。执行器全部走命令行参数（`--base-url` / `--scene` / `--tag` / `--limit` / `--concurrency` /
`--baseline` / `--customer` / `--api-key` / `--no-trace` / `--no-db` / `--dry-run`，清单见 [evaluation.md 第 5 节](../evaluation.md)）。
被测服务的行为仍由原有配置决定，其中会影响指标的是：`TRACE_SAMPLE_RATE`（验收跑建议临时设 1.0）、
`ROUTER_CONFIDENCE_THRESHOLD`、`ROUTER_MAX_INTENTS`、`RAG_MIN_SCORE`、`RAG_TOP_K`、模型名。
**改这些等于换基线**——报告解释不了配置造成的波动，必须写进变更说明。

## 8. 验证方式

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_eval_runner.py -q --basetemp=.pytest_tmp   # 47 项
.\.venv\Scripts\python.exe ..\scripts\eval.py --dry-run                                      # 只校验用例集
.\.venv\Scripts\python.exe -m app.migrate                                                    # 7 → 8
.\.venv\Scripts\python.exe ..\scripts\verify_schema.py
```

`test_eval_runner.py` 覆盖：仓库用例集可加载 / id 唯一 / 每条都有期望 / 覆盖全部维度 / 来源标注可替换；
指纹忽略 note 与 tags、随判定字段变化、与顺序无关；18 个期望字段各自的通过与不通过路径（含
`rejected` 必须 HTTP ≥ 400、流内 `error` 不算拦住、工具报错不改 passed、`unobserved` 跳过不算失败）；
聚合分母排除无关与不可观测；P95 最近邻；diff 的绝对/相对容差与方向、指纹不同不可比、无基线不对比；
报告五节与退化标注、失败摘录带 trace_id、明细行紧凑完整。

真实链路跑批（`w4-full-*` / `w4-default-1` / `w4-deg-*`）不在 CI 里跑——它要打模型；
上表 5.1 的三份报告是本轮验收证据，`reports/` 不入库，指标与明细在 `eval_runs` 里。

## 9. 已知边界

- **不做后台评测页与定时回归**：目前只有 CLI + Markdown 报告 + `eval_runs`。
- **模型输出天然带随机性**：同一用例两次措辞可能不同，措辞类只允许 `keywords_any` 这类宽松判定。
- **`RAG_MIN_SCORE` 是双刃剑**：调低能抬高命中率、同时增加注入上下文的噪音；评测报告不接受「为了指标调阈值」这种处置。
- **token 是估算值**：与供应商账单有系统性偏差，`TOKEN_ESTIMATE_FACTOR` 只能整体校准（W2）。
- **登录客户跨次共享记忆**：客户 token 对应固定主体 UUID，`default` 场景没开匿名只能用 token 跑，
  第二次跑可能吃到第一次的记忆；要严格可重复就用开了匿名的场景或专用接入方 Key。
- **首字延迟由客户端计时**：含网络往返，与后台链路的 `ttft_ms` 不同源，报告里已标注。
- **用例集是场景相关的**：换客户后要重写 `<scene>.json`；判定逻辑与执行器不用动。
