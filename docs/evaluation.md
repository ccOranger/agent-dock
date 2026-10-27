# 评测与回归（Evaluation & Regression）

> 回答客户第一问「效果行不行」与交付最痛的一问「改了提示词/换了模型，有没有变差」。
> 关联：[产品化计划 3.4](productization-plan.md)、[U24](updates/24-eval-regression.md)、[配置说明](configuration.md)、[运维手册](runbook.md)
> 社区版功能：用例集、CLI 执行器、Markdown 报告、`eval_runs` 运行记录全部在社区版；
> 后台「评测与回归」看板属企业版功能位 `eval_pro`（只管管理与定时回归，不限制评测能力）。

## 1. 它由四样东西组成

| 部件 | 位置 | 职责 |
|---|---|---|
| 用例集 | `backend/tests/fixtures/eval/<scene_code>.json` | 进 Git、评审后才可改的回归用例（含期望与来源标注） |
| 数据模型与纯逻辑 | `backend/app/schemas/eval.py`、`backend/app/services/eval_runner.py` | 用例校验、指纹、打分、聚合、基线 diff、报告渲染（不发请求、不碰 DB，可用 mock 单测） |
| 执行器 | `scripts/eval.py` | 走**真实 HTTP 链路**采集 + 服务端链路回读 + 落库 + 出报告 |
| 运行记录 | 表 `eval_runs`（`SCHEMA_VERSION=8`） | 「上一次跑是多少」的持久对照，也是 `eval_pro` 看板唯一数据源 |

## 2. 为什么必须走真实 HTTP 链路

直接 import Agent 跑一遍更快，但那样**测不到**鉴权、限流、场景路由、固定话术、埋点与链路回读——
而这些恰恰是客户现场最容易出问题的地方（本项目首轮评测就是靠真实链路发现了一个运维可见的缺陷：
`/chat/stream` 响应头 `X-Trace-Id` 与落库的 `trace_id` 不是同一个，见 [7.3](#73-评测顺手修掉的真实缺陷trace_id-三处不一致)）。

执行器只做两件事：

1. **采集**（客户端视角）：`POST /api/v1/chat/stream` 逐条读 SSE，记录 `session / route / tool_call / tool_result / handover / text / done / error`、
   HTTP 状态、首字延迟与总耗时；
2. **回读**（服务端事实）：按 `done` 事件里的 `trace_id` 查 `request_traces`（RAG 片段数、命中的命名空间、工具名、是否兜底、结局）
   与 `chat_events`（token）。流里没有这些信息，只有服务端有。

RAG 与 token 指标因此是**服务端实测**，不是模型自述。

## 3. 用例集

### 3.1 结构

```json
{
  "scene": "customer_service",
  "description": "这个文件在测什么、为什么这么测",
  "cases": [
    {
      "case_id": "cs-kb-01",
      "scene": "customer_service",
      "query": "忘记密码了怎么重置",
      "setup": [],
      "expect": { "agent": "knowledge_qa", "keywords_any": ["重置", "手机号"], "expect_rag": true, "rag_namespace": "cs_default" },
      "tags": ["知识", "账号", "RAG"],
      "source": "demo_corpus",
      "note": "为什么这样期望；口径来自哪份文档/哪条政策"
    }
  ]
}
```

- `scene` 留空表示与文件名同场景；显式写别的场景就是跨场景回归用例（多数不一致会被加载器拒绝）。
- `setup[]` 是正式提问前的铺垫轮次，同一会话内依次发送，用于追问续接与跨轮记忆类用例；**只判定最后一轮**。
- `expect` 全部可选：**没写的维度不产生判定，也不进指标分母**。

### 3.2 期望维度（18 个字段，按判定归为 11 组）

| 维度键 | 判定 | 对应指标 |
|---|---|---|
| `rejected` | 必须 HTTP ≥ 400 才算「入口拦下」；流内 `error`（HTTP 200 + SSE）不算 | 通过率 |
| `agent` / `agents_any` | `route` 事件的主意图（或候选意图集合）匹配 | 意图准确率 |
| `min_intents` / `intents_include` | 多意图：识别出的意图数与清单 | 意图准确率 |
| `min_confidence` | `route` 置信度下限 | — |
| `tools_all` / `tools_any` | SSE `tool_call` ∪ 链路 `tool_names`（后者更全） | 工具命中率 |
| `max_tools` | 工具调用次数上限（政策边界：不该动手就别动） | — |
| `expect_rag` / `rag_namespace` / `rag_empty` | 链路回读的 RAG 片段数与命中命名空间 | RAG 命中率 |
| `handover` | `handover` 事件 | 转人工率 |
| `fallback` | 意图带 `is_fallback`，或 reason 为 `scene_fallback` / `解析失败`，或链路 `router_fallback` | 兜底率 |
| `keywords_any` / `keywords_all` / `must_not` | 应答文本包含 / 禁词 | 通过率（措辞类不单独成指标） |
| `max_answer_chars` | 应答长度上限 | — |

`must_not` 是给合规与安全边界用的：「不许承诺超出政策的赔偿」「不许回显 `sk-` / 连接串字样」。

18 个字段全部可选，所以上表把语义同族的合并在一行（如 `agent` / `agents_any`）。`planned_checks()` 决定一条用例
进了哪些指标的分母，单测 `test_planned_checks_covers_every_expectation_field` 保证新增字段不会被默默忽略。

### 3.3 来源分层与首批的有记录偏离

计划要求「60% 真实 `request_traces` 抽样 + 40% 人工边界用例」。本仓库交付时库里没有真实客户链路，
因此首批 50 条（`customer_service` 39 + `default` 11）实际按两类标注：

| `source` | 含义 | 首批 |
|---|---|---|
| `demo_corpus` | 由仓库演示知识库 `docs/demo/*.md` 派生的正常问法 | 计划里的「真实抽样」占位（**偏离已记录**） |
| `manual` | 人工构造的边界用例：低置信、超纲、多意图、追问、注入、政策边界 | 40% 那部分 |
| `trace_sample` | 真实线上链路抽样 | 0 条，客户现场积累后回填 |

`source` 字段就是替换进度表：现场跑两周后按 `request_traces` 抽样把 `demo_corpus` 换成 `trace_sample`，
用例集结构与判定逻辑都不需要改。`test_repo_cases_source_annotation_is_replaceable_with_real_traces` 锁住这个约定。

### 3.4 修改纪律

- 用例进 Git，**评审后才可改**。改期望值来迁就结果会让报告失去意义。
- 指纹 `fingerprint(cases)` = 参与判定的字段（`case_id` + `query` + `setup` + `expect`）的 sha256 前 16 位，
  与 `note` / `tags` 无关。指纹变了说明用例变了，与旧基线的 diff 直接标为「不可比」，不判退化。
- `--limit` / `--scene` / `--tag` 之后的子集有**自己的指纹**，不会拿 3 条冒烟的分数去对 39 条的全量基线。
- 如果确实要改判定口径（而不是改期望迁就结果），在用例 `note` 里写清依据（代码位置或政策条目），
  并在 [U24](updates/24-eval-regression.md) 记录。首批做过两处：`cs-edge-01/02/03`、`cs-order-04`，见 [7.2](#72-首轮评测暴露的问题与处置)。

## 4. 指标口径

| 指标 | 口径 |
|---|---|
| 通过率 | 通过用例 / 总用例 |
| 意图准确率 | 意图类检查点通过数 / **写了意图期望且产生了判定的用例数** |
| 工具命中率 | 同上，工具类检查点 |
| RAG 命中率 | `expect_rag` 与 `rag_empty` 两类检查点合并计入分母（后者实测到片段就算不通过） |
| 兜底率 / 转人工率 | 分母 = 真正跑完链路的用例（入口被拒、传输失败的轮次不算结局） |
| P95 首字延迟 | **最近邻法**：排序后取第 `ceil(0.95n)` 个观测值，样本少时不插值，便于人工核对 |
| 平均总耗时 / 平均 token | 跑完链路的用例均值；token 来自 `chat_events`，是估算值（口径同[用量报表](configuration.md)） |

**观测不到 ≠ 不达标**。三条纪律：

1. 用例没写这类期望 → 完全不参与该指标；
2. 写了但服务端没产生可回读事实（链路被采样丢弃、未落库） → 计入该指标的 `unobserved`，**不进分母**；
3. 写了且有判定 → 进分子/分母。

把「采样丢弃」算成失败，报表就会把基础设施问题误报成效果退化——这是回归报告最容易被质疑的一格，
因此报告第 3 节把分子/分母/不可观测三个数都列出来。

### 4.1 退化判定与容差

| 类型 | 容差 | 触发条件 |
|---|---|---|
| 比率类（通过率、意图/工具/RAG 命中率、兜底率、转人工率） | `REGRESSION_ABS = 0.02` | 比率类指标绝对下降 > 2 个百分点；兜底率、转人工率方向相反（越小越好） |
| 耗时/消耗类（P95 首字、平均总耗时、平均 token） | `REGRESSION_REL = 15%` | 相对上升 > 15% |
| 用例集指纹不同 | — | 全部标 `comparable=False`，只展示差值不判退化 |

`--baseline` 命中任一退化 → 退出码 2（CI 上就是流水线红）。

## 5. 执行器用法

```powershell
cd backend

# 只校验并列出用例，不发请求（改了用例集先跑这个）
.\.venv\Scripts\python.exe ..\scripts\eval.py --dry-run

# 全量跑一个场景，并标注本次对应的变更
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service --run-tag v0.2.0-rc1

# 与上一次同用例集的运行对比（eval_runs 里按指纹+场景取最近一次）
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service --baseline latest

# 用 sidecar 文件做基线（不连库也能对比）
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service --baseline ..\reports\eval-20261027-092826.json

# 默认场景未开放匿名，需要客户身份；并发压到 2 以免撞模型限流
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene default --customer alice --password Alice@123 --concurrency 2

# 子集冒烟：标签过滤 + 只跑前 5 条 + 不写库
.\.venv\Scripts\python.exe ..\scripts\eval.py --scene customer_service --tag 知识 --limit 5 --no-db
```

| 参数 | 说明 |
|---|---|
| `--base-url` | 被测地址，默认 `http://127.0.0.1:8000`；先探 `/health`（注意它挂在根路径，不带 `/api/v1`） |
| `--fixture-dir` | 用例集目录，默认 `backend/tests/fixtures/eval` |
| `--scene` / `--tag` / `--limit` | 过滤，只影响选哪些用例；请求仍按每条用例的 `scene` 发送 |
| `--run-tag` | 本次运行标注（报告标题、`eval_runs.run_tag`、基线检索） |
| `--baseline` | `latest`（同指纹同场景最近一次） / `eval_runs` 的 run_id / sidecar json 路径 |
| `--concurrency` | 并发数，默认 2。模型有速率限制，别无脑拉高 |
| `--timeout` | 单轮读超时（秒），默认 120 |
| `--customer` / `--password` | 客户登录取 token（未开匿名的场景需要） |
| `--api-key` / `--publish-key` / `--integration-code` | 服务端 Key / 场景公开标识 / 接入方公开标识 |
| `--model-name` | 报告里标注的模型名，默认取服务端配置 |
| `--no-trace` | 不回读链路（RAG 与 token 指标全记为不可观测） |
| `--no-db` | 不写 `eval_runs`（临时试跑） |
| `--out-dir` | 报告目录，默认 `reports/`（已在 `.gitignore` 内，报告不入库） |

### 5.1 退出码

| 码 | 含义 | 典型处置 |
|---|---|---|
| 0 | 全部通过 | 可附验收单 |
| 1 | 有失败（或 `--dry-run` 用例集不合法） | 按报告第 4 节逐条归因：用例口径、配置、还是模型真的退步 |
| 2 | 相对基线**退化** | 回滚或解释；这是发布闸门口径 |
| 3 | 全部用例都没跑通 | 服务没起来 / 地址错 / 鉴权不通 |

### 5.2 产出物

- `reports/eval-<timestamp>.md`：五节结构 — 结论 → 指标与基线对比（含退化标注） → 指标口径与样本数 → 失败用例（失败项、trace_id、应答摘录） → 全量用例表。可直接附在验收单后。
- `reports/eval-<timestamp>.json`：完整快照（`summary` 含逐用例观测与判定，`cases` 是本次纳入的用例 id 清单），可当离线基线。
- `eval_runs` 一行：`run_tag / scene_code / base_url / model_name / fixture_hash / case_count / passed_count / metrics / detail / report_path / baseline_run_id / duration_ms / started_at / finished_at`。

## 6. 跑评测之前要确认的环境事实

1. **链路采样率**：`TRACE_SAMPLE_RATE < 1` 时部分轮次没有 `request_traces`，RAG 指标会落到 `unobserved`；
   做验收跑建议临时设 1.0。token 来自 `chat_events`，不受采样影响。
2. **访客键每轮唯一**：执行器用 `ev-<时间戳>-<case_id>` 作 `visitor_id`，避免历史会话与长期记忆串味，
   这是「两次跑出同样指标」的前提。
3. **登录客户会跨次共享记忆**：客户 token 对应固定主体 UUID，记忆跨运行累积。`default` 场景没开匿名，
   只能用客户 token 或已授权场景的 `X-API-Key`；用它跑评测时要清楚第二次跑可能吃到第一次的记忆。
   要严格可重复 → 用开了匿名的场景（`customer_service`）或专用接入方 Key。
4. **知识库索引健康**：先确认后台知识库页的文档 `status=ready` 且分块数与正文规模相称；
   重复 seed 而不清 Qdrant 会留下同一块的多个重复点，把 RAG 指标算虚高（本项目首轮就踩到了，见 7.2）。
5. **鉴权与场景**：`customer_service` 有 publish key → 允许匿名；`default` 未开匿名 → 必须带身份。
6. **配置改动等于换基线**：改 `ROUTER_CONFIDENCE_THRESHOLD` / `ROUTER_MAX_INTENTS` / `RAG_MIN_SCORE` /
   `RAG_TOP_K` / 模型之后，指标波动是配置造成的；评测报告解释不了配置，请写进变更说明。

## 7. 首批实测结果与发现

### 7.1 三轮实测（本机 `SEED_DEMO=true`、`deepseek-flash`、`TRACE_SAMPLE_RATE=1.0`）

| 运行 | 范围 | 通过率 | 意图 | 工具 | RAG | 转人工 | 平均 token | 说明 |
|---|---|---|---|---|---|---|---|---|
| `w4-full-3` | customer_service 39 条 | 36/39 = 92.3% | 30/30 = 100% | 7/7 = 100% | 9/12 = 75% | 4/38 = 10.5% | 1437 | 建基线 |
| `w4-full-4` | 同一用例集再跑一次 | 36/39 = 92.3% | 100% | 100% | 75% | 10.5% | 1488 | `--baseline latest` → **无指标退化**：质量类指标逐项与上次相同，只有 P95 首字 +988ms、平均 token +51（容差内） |
| `w4-default-1` | default 11 条（alice） | 10/11 = 90.9% | 7/7 = 100% | — | 4/5 = 80% | 0% | 367 | 唯一失败 `d-kb-04` 与客服场景同因（见 7.2-3） |

「两次一致」这条验收就是 `w4-full-3` vs `w4-full-4`：比率类指标一格不差，耗时类在容差内漂移。

### 7.2 首轮评测暴露的问题与处置

首轮（修环境前）30/39 = 76.9%，9 条失败逐条归因后分三类：

1. **本地索引被重复 seed 污染**（环境）：`cs_default`/`cs_order`/`cs_after_sales` 各只剩 9 个**内容相同**的重复点，
   每篇演示文档只有 1 个块。清理命名空间向量后按 `docs/demo/*.md` 重灌，平均 token 由 2521 降到 1437
   —— 此前每轮把同一块重复注入 5 次，RAG 命中率被虚高的分数掩盖。
2. **业务 Agent 只绑一个知识库**（配置，已改仓库种子）：政策类问法（退货运费、发票、退款时效）写在售后与订单指南里，
   `knowledge_qa` 只查 `cs_default` 就检回空块只能转人工。`scripts/seed_data.py` 已给它配
   `rag_namespaces=["cs_default","cs_order","cs_after_sales"]`、给 `order_service` 配 `["cs_order","cs_after_sales"]`；
   RAG 命中率 50% → 75%。刻意不用 `search_all_knowledge_bases`：那会把 `it_*` 演示库混进客服场景。
3. **演示文档粒度过粗**（内容，**保留失败**）：每篇文档不到 512 token，整篇成一块，
   窄问法（支持哪些终端、关闭通知、开发票）的相似度落在 `RAG_MIN_SCORE=0.3` 之下 → 检回为空。
   `cs-order-08`、`cs-kb-04`、`cs-kb-05`、`d-kb-04` 四条继续判失败，**不降阈值、不改期望**：
   正确的处置是实施时把客户 FAQ 按主题拆成条目（一条一题），这正是这份报告要替客户提前暴露的问题。
4. **两处用例口径修正**（有代码依据，非迁就结果）：
   - `cs-edge-01/02/03` 原期望超纲问题走「兜底应答」。实测与代码一致：`is_fallback` 只在服务端解析失败/无有效意图时置位，
     本场景池内没有 `default_assistant`，路由给出的兜底 Agent 落为生效清单首项（`complaint_handler`）且 confidence 0.3 低于阈值 →
     既定结局是**整轮转人工**（前端展示「已转人工处理」）。改为期望 `handover=true` + `fallback=false`，并在 `note` 写明依据；
     `ROUTER_FALLBACK_ANSWER` 要让位给兜底应答，前提是场景挂一个中立兜底 Agent。
   - `cs-order-04`（「我最近都下了哪些订单」）原期望调用订单工具。匿名访客没有可信身份，
     设计行为是先索要订单号/手机号（`docs/demo/order_guide.md` 第一条即此规则），未确认身份就 `list_orders` 会拉到别人的单。
     改为期望索要凭证的措辞，带凭证的工具命中由 `cs-order-01/02/03/09` 覆盖。

### 7.3 评测顺手修掉的真实缺陷：`trace_id` 三处不一致

执行器按 `trace_id` 回读链路时命中 0 条，追下去发现是产品缺陷：`main.py` 的 `trace_middleware` 在 `call_next` **之后**
覆盖响应头 `X-Trace-Id`，而 `chat.py` 端点自己又 `new_trace_id()` 一次并用于 `chat_events` / `request_traces` 落库 ——
客户端拿到的 id 与库里的 id 不是同一个，运维按响应头查链路永远查不到。修复：端点改用
`_request_trace_id(request)` 复用 `request.state.trace_id`，回归测试 `test_chat_endpoint_reuses_middleware_trace_id` 防复发。

### 7.4 退化检出演示（验收第二条）

子集 `--scene customer_service --tag 知识`（10 条）：

| 运行 | 变更 | 通过率 | 意图准确率 | RAG 命中率 | 转人工率 | 退出码 |
|---|---|---|---|---|---|---|
| `w4-deg-base` | 正常配置 | 70.0% | 100.0% | 70.0% | 0% | 1（3 条已知失败） |
| `w4-deg-broken` | 把 `knowledge_qa` 的提示词与路由线索改成「物流异常登记」 | 30.0% | **30.0%** | 30.0% | 70.0% | **2** |

报告第 2 节把四项标成「退化」并给出超出容差的幅度：通过率 −40.0%、意图准确率 −70.0%、RAG 命中率 −40.0%、转人工率 +70.0%；
`w4-deg-broken` 跑完后配置已逐项恢复原值（读回校验通过）。

## 8. 已知边界

- **不做后台评测页与定时回归**：`eval_pro` 属企业版（私有仓库），社区版只有 CLI + Markdown 报告 + `eval_runs`。
- **模型输出天然带随机性**：同一用例两次措辞可能不同，所以措辞类只允许 `keywords_any` 这类宽松判定，不要用 `keywords_all` 卡死一句话。
- **`RAG_MIN_SCORE` 是双刃剑**：调低会抬高命中率、同时增加注入上下文的噪音；评测报告不接受「为了指标调阈值」这种处置。
- **token 是估算值**：与供应商账单有系统性偏差，`TOKEN_ESTIMATE_FACTOR` 只能整体校准（见 W2）。
- **P95 用最近邻法**：几十条样本上做插值只会让数字看着更精确而不可核对。
- **用例集是场景相关的**：换客户后要重写 `<scene>.json`；判定逻辑与执行器不用动。
