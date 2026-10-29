# 一轮对话怎么被拆开看：链路追踪的数据模型与埋点纪律

> 面向需要给 LLM Agent 做可观测的后端工程师。以 AgentDock 的实现为例：为什么"打日志"不够、一轮一行怎么存、埋点该放在哪、采样怎么不丢关键轮次。
> 代码见 `backend/app/core/tracing.py`、`backend/app/services/chat_service.py`、`backend/app/agents/orchestrator.py`、`backend/app/api/v1/admin_traces.py`。

## 1. 日志看不了的东西

一轮客服对话可能经过：路由 → 2 个业务 Agent → MCP 工具发现 → 3 次工具调用 → 记忆召回 → RAG 检索 → 记忆归档。用户说"答错了"，日志里能查到的是零散行，回答不了真正的问题：

- 这句话判给了谁，依据什么线索？
- 工具到底调了没有？调了但失败了，还是根本没调？
- 知识库明明有内容，为什么没进 prompt？
- 到底是模型答错，还是解析把模型输出丢了？

日志是**按时间散落的字符串**，链路要的是**按决策阶段组织的结构化证据**。所以需要一张表：一轮请求一行，行里带一条 span 时间线。

## 2. 一张表，两种结构

`request_traces` 的设计是"宽表 + 明细"：

| 结构 | 存什么 | 为什么 |
|---|---|---|
| 拍平的维度列（数组 + GIN 索引） | `agent_names`、`tool_names`、`mcp_server_names`、`llm_models`、`rag_namespaces`、`intent_count` | 列表页要按"哪个 Agent / 哪个工具"过滤，数组列 + GIN 比在 JSONB 里解构快得多，也不用给列表页做 N+1 |
| 结论列 | `outcome`、`confidence`、`handover_reason`、`error_code`、`ttft_ms`、`total_latency_ms`、`response_chars` | 聚合与告警只读这几列，不需要展开 spans |
| `spans` JSONB | 完整时间线 | 详情页才用，读一次展开一次 |

关键是**不要把"要过滤的"和"要看的"混在一起**。全塞进一个 JSONB，列表页每次查询都要展开；全拍平成列，又装不下 span 的嵌套结构。

还有一个细节：`trace_id` 与 HTTP 层的 `X-Trace-Id`、structlog 的上下文对齐。这样用户报障时给出一个 trace id，能同时定位到日志行、链路行和助手消息。

## 3. 十种 span，按决策阶段排

| span | 记录点 | 回答的问题 |
|---|---|---|
| `router.llm` | 路由模型调用 | 候选有哪些、原始输出是什么、是不是兜底 |
| `route.intent` | 每个意图 | 顺序、置信度、理由 |
| `context.memory` | 记忆召回 | 命中几条、有没有报错 |
| `context.rag` | 每个知识库命名空间 | chunk 数、最高分 |
| `context.budget` | 上下文裁剪 | 哪块被整块丢弃（`status=skipped`） |
| `agent.resolve` | 意图 → Agent | 从哪注册的、声明了哪些 MCP 与命名空间、是否未注册 |
| `agent.build` | Agent 构建 | 模型是谁、实际连上了哪些 MCP、可用工具清单 |
| `agent.run` | 单意图流式应答 | 字符数、是否空应答 |
| `tool.call` | 工具调用 | 入参、返回、错误 |
| `memory.persist` | 记忆归档 | 重要性、是否低于阈值被跳过 |

每个 span 统一带 `span_id / parent_id / type / name / start_ms / duration_ms / status / attributes`。**`parent_id` 是必须的**：多意图那一轮，两个意图各有一条 `agent.run`，没有父子关系就只能靠时间猜。

### 最容易忽略的一条：`router.llm` 必须留原始输出

做链路时都想只存"解析后的意图"，因为干净。但排查时会立刻卡住：用户问"这句话为什么判给订单 Agent"，你只有 `reason` 这一个字符串，没法区分两种情况：

- 模型输出了正确的 JSON，但**解析器**把它丢了（格式兼容问题）；
- 模型本来就判错了。

留下原始输出（受 `TRACE_PAYLOAD_MAX_CHARS` 截断），这两个原因可以当场区分。代价是一点存储。

## 4. 采集器：内存累加，一次落库

`TraceCollector` 的纪律只有三条：

1. **纯内存累加，不做 IO**。所有 span 先进 list，流结束后由 `ChatService._persist_trace` 一次写库。每写一个 span 就发一条 UPDATE，在流式场景里会把延迟打穿。
2. **`enabled` 关闭时所有方法空转**。调用方到处写 `if collector:` 会让埋点代码淹掉业务代码，所以约定：传了 collector 就直接调，内部判断开关。
3. **跨事件的 span 用 `start()` / `end()` 配对**。工具调用是"call 事件到达 → 若干秒后 result 事件到达"，无法在单点算出耗时；`tool.call` 的 `duration_ms` 只能这样凑。

`to_row()` 负责把 collector 里的数据拍平成一行，包括从 spans 里反推 `tool_error_count`、`response_chars` 这类结论列——**结论列不能靠调用方各自维护**，否则漏一处就是一个永远为 0 的指标。

## 5. 埋点放在哪：跟着决策走，不跟着函数走

| 位置 | 埋什么 |
|---|---|
| `Orchestrator.dispatch(..., collector)` | 路由、意图、解析、构建、运行、工具、结局全程传递 |
| `ContextEngineer.build / persist_after_run` | 记忆召回、RAG、预算裁剪、归档 |
| `RouterAgent.route(..., collector)` | 路由 LLM 的原始输出与候选数 |
| `ChatService.stream / reply` | 创建 collector、把 `route` 事件同步到 `sessions.last_route_to`、结束时落库 |

判断标准是"这一步是否产生了影响回答的决策"：决策点埋 span，纯运算不埋。埋太多会让详情页读不动，也拖慢流式链路。

## 6. 两个能立刻定位问题的对比

**① `agent.resolve` vs `agent.build`**

- `agent.resolve` 记录 Agent **声明**要用的 MCP 与命名空间；
- `agent.build` 记录**实际**连上的 MCP 与展开出的工具。

MCP 最典型的静默失败是"连上了但没展开工具"——模型看到的是一个名为 `mcp_xxx` 的元工具，而不是真实的 `query_logistics`。对比这两条 span 就能发现：声明了 3 个 MCP，可用工具清单里却只有 1 个元工具。

**② `context.budget` 的 `status=skipped`**

上下文裁剪是整块丢弃。一旦客户记忆 + 知识库超过预算，两块里较大的那块会被整块扔掉，模型收到的是"没有参考资料"的 prompt。答非所问的第一嫌疑就是它，而这条 span 是唯一证据。

## 7. 采样：失败轮次永远全量

线上全量落库的成本主要在两处：`spans` JSONB 的体积、以及每轮一次 INSERT。

`TRACE_SAMPLE_RATE` 控制正常应答的采样，但 `should_persist()` 有一条硬规则：**handover / error / 带工具异常的轮次永远落库**。

原因很直白：采样是为了省存储，而这几类轮次正是排障对象。按 20% 采样，出问题的 5 轮里只留下 1 轮，等于把最有价值的证据随机丢掉。

配套的运维口径也要写清：链路三率（工具成功率、兜底降级率、RAG 命中率）在采样率 < 1 时**会被低估**，只能作比值参考；全量三率走日聚合表，不受采样影响。后台的数据分析页对这两组指标做了分组和角标，就是为了防止它们被混着解读。

## 8. 脱敏与体积

| 配置 | 默认 | 作用 |
|---|---|---|
| `TRACE_ENABLED` | true | 总开关 |
| `TRACE_STORE_PAYLOAD` | true | 关掉后工具入参/返回只存类型摘要（`<payload omitted:N chars>`） |
| `TRACE_PAYLOAD_MAX_CHARS` | 2000 | 入库前截断 |
| `TRACE_SAMPLE_RATE` | 1.0 | 正常轮次采样率 |
| `TRACE_RETENTION_DAYS` | 30 | 清理脚本取用 |

`TRACE_STORE_PAYLOAD=false` 是给涉敏环境准备的：能看到"调用了几次、耗时多少、成功失败"，看不到具体参数。这是一个二选一的开关，不做字段级脱敏——字段级脱敏要么漏、要么把有用的上下文一起抹掉。

## 9. 旁路：链路坏了不能影响对话

三条写死的规则：

1. `_persist_trace` 失败**只记日志**，不抛出、不改变 `done` 事件；
2. 服务端异常路径也落链路（`outcome=error`），否则"崩了"这件事反而没有记录；
3. 客户端主动断开（`GeneratorExit`）**不落**——那不是一轮完整对话，存下来只会污染成功率统计。

第三条容易被写成"反正都存"。区别在于：`error` 是系统的问题，`GeneratorExit` 是用户关页面；混在一起看错误率，永远查不到真问题。

## 10. 定位入口：三种 trace_ref

详情接口接受三种引用：

| 传入 | 场景 |
|---|---|
| 行 ID | 从链路列表点进去 |
| HTTP `trace_id` | 用户/网关给出一个 id，反查整轮 |
| 助手消息 ID | 从咨询详情看"这条回答当时怎么来的" |

`done` 事件里带 `trace_id`，前端拿到就能跳转；咨询详情页用 `assistant_message_id` 反查。**三种入口都存在**，是因为排障时手上有什么就用什么，要求对方先找到行 ID 是不现实的。

再加一个聚合视角：`GET /admin/traces/usage?field=tool_names|agent_names|mcp_server_names&days=N`，看近 N 天每个依赖被多少轮覆盖。它回答的是另一个问题——"这个 MCP 是不是根本没人用/长期失败"，这比单轮详情更能暴露配置腐化。

## 11. 保留与清理

`scripts/cleanup_traces.py` 按 `TRACE_RETENTION_DAYS` 删除过期链路，支持 `--days` 覆盖与 `--dry-run` 预演，挂 cron 即可。

清理脚本一定要先有 `--dry-run`：第一次上线时先看清楚"按这个天数会删多少行"，因为链路表是典型的"某天参数配错、一个月后才炸"的表。

## 12. 失败模式清单

| 症状 | 根因 | 处置 |
|---|---|---|
| 链路详情没有 span | collector 未开启，或 `TRACE_ENABLED=false` | 先看配置，再看采集点是否传了 collector |
| 工具耗时都是 0 | 只记了 call 事件，没做 `start/end` 配对 | 用跨事件 span |
| 列表页过滤很慢 | 在 JSONB 里做过滤 | 拍平成数组列 + GIN |
| 关键轮次没落库 | 采样率 < 1，且未把失败轮次排除在采样外 | `should_persist()` 里强制全量 |
| 错误率虚高 | 把客户端断开也算成 error | `GeneratorExit` 不落库 |
| 三率和链路对不上 | 采样口径与全量口径混读 | 两组指标分组展示，标注采样率 |
| 表体积失控 | 没有清理任务 | 挂 `cleanup_traces.py`，先 `--dry-run` |
| 排查时说不出"模型错还是解析错" | 只存解析后的结果 | 保留 `router.llm` 的原始输出 |

## 13. 小结

链路追踪的投入产出比，取决于四件事做没做：

1. **结构分层**：要过滤的拍平成列，要看的放 spans；
2. **决策点埋点**：跟着判断走，不跟着函数走；
3. **失败优先**：采样可以省存储，但不能省掉排障对象；
4. **绝对旁路**：写链路失败不能改变任何对话行为。

做到这四点，用户说"答错了"的时候，你能在 30 秒内指出是路由、检索、工具还是模型的问题，而不是去翻几百行日志。
