# 三层记忆别熬成一锅粥：对话 Agent 的记忆生命周期设计

> 面向正在做企业对话 Agent 的后端工程师。以 AgentDock 的实现为例，讲清楚三件事：记忆要分几层、什么时候晋升、为什么必须留一张能被人看懂的镜像表。
> 代码见 `backend/app/core/context_engineer.py`、`backend/app/tools/memory_factory.py`、`backend/app/models/repositories.py`（`MemoryRepo`）。

## 1. 先拆层，否则一定互相打架

"记住用户"这句话，在工程上至少对应三种寿命完全不同的东西：

| 层 | 载体 | 寿命 | 谁写 | 谁读 |
|---|---|---|---|---|
| 同轮上下文 | 拼装后的 prompt（`【客户记忆】`/`【知识库参考】`/`【客户问题】`） | 一次请求 | `ContextEngineer.build` | 本次应答的模型 |
| 同会话跨轮 | `messages` 表最近若干条 | 一次会话 | `ChatService` | 业务 Agent（`_seed_history` 注入 12 条） |
| 跨会话长期 | 向量库（Qdrant）+ `memories` 镜像表 | 天/月级 | `persist_after_run` | 后续任意会话的召回 |

把三者塞进一个"记忆"概念里，会立刻出现两类错：把短期上下文当成长期记忆去写（向量库很快被"你好"塞满），或者把长期记忆当上下文用（每次都全量拉，token 直接爆）。

所以边界要先划死：**同轮上下文不落库，跨轮上下文只从消息表取，长期记忆才进向量库**。

## 2. 每轮都写 working，阈值只决定"要不要晋升"

早期实现用一个阈值做闸门：重要性不达标就不写。结果是"你好""在吗"这类话确实不该记，但连"我下周五要去外地"也被一起丢掉——第二天用户回来，Agent 完全不认得。

现在的分法是：

- **working 层每轮必写**，进程内、带 TTL（`WORKING_MEMORY_TTL`，默认 1800 秒），参与召回时优先级最高；
- **阈值只管晋升**：达到阈值才额外写一条 `episodic` 长期记忆。

```python
# context_engineer.persist_after_run（示意）
extra_meta, signals = self._extract_signals(query, history)
importance = compute_importance(query, metadata, signals)
memory_tool.run({"action": "add", "memory_type": "working", ...})   # 无条件
if promote:
    memory_tool.run({"action": "add", "memory_type": "episodic", ...})
```

`compute_importance` 是纯规则打分，起点 0.3，命中订单号/工单号/手机号 +0.2，再叠加情绪强度等信号——**不用 LLM 判断重要性**。理由很实际：归档在后台跑，模型调用会让每轮多一次几百毫秒的开销，而且判定结果不稳定，今天记明天不记，排查起来无从下手。

## 3. 晋升的四个入口

| 入口 | 条件 | 备注 |
|---|---|---|
| 重要性 | `importance >= MEMORY_IMPORTANCE_THRESHOLD`（代码默认 `0.35`，`.env.example` 示例值 `0.6`） | 规则打分 |
| 硬实体 | 消息里出现订单号 / 工单号 | 后续一定会被追问，必须记住 |
| 情绪强度 | `signals["sentiment_intensity"]` 为真 | 投诉类会话宁可多记 |
| 使用频次 | `access_count >= MEMORY_ACCESS_THRESHOLD`（默认 3） | 召回命中即异步 `touch_access`，反复被检索的 working 行补写长期层 |

外加一条收口：**会话结束全量晋升**（`promote_on_session_close`），把该会话还没晋升的 working 行一次性转成 episodic。这样"用户聊完就走、没触发任何信号"的会话也不会白丢。

四入口 + 一条兜底，覆盖的都是可解释的显式信号，不需要"模型觉得重要"。

## 4. 镜像表：向量库负责召回，DB 负责被管理

向量库能算相似度，但不能回答运营的三个问题：这条记忆是谁说的、什么时候说的、能不能改。所以每次长期写入都在 `memories` 表 upsert 一行镜像：

| 列 | 作用 |
|---|---|
| `memory_uid` | 与向量库条目的对应键 |
| `user_id` / `visitor_id` | 主体归属（`CHECK` 约束二选一，访客会话同样有归属） |
| `session_id` / `source_message_id` | 溯源到会话与当轮用户消息 |
| `type` / `importance` / `access_count` | 后台展示与晋升判定用 |

### `memory_uid` 必须是稳定摘要

这里踩过一个隐蔽的坑：早期用 `abs(hash(query))` 生成 uid。`hash()` 对 str 带进程级随机种子（PYTHONHASHSEED），**服务重启后同一个 query 算出不同 uid**，于是同一条记忆在向量库和镜像表里越攒越多，运营页看到的"重复记忆"就是这么来的。

现在固定为 `f"{session_id}:{sha1(query)[:16]}"`：同一会话同一句话永远落到同一个 uid，upsert 天然幂等。

### 类型标签不要过度解读

镜像行的 `type` 有时会写成 `semantic`（消息里带实体时），但**向量实际写入的仍是 episodic 层**。这只是给后台看的一个语义标签，别拿它当"用了哪种记忆算法"的证据——文档里写清楚这一点，能省掉一堆误读。

## 5. 召回与预算：整块丢弃才是"答非所问"的真凶

`ContextEngineer.build` 并发做两件事，两件都做了硬保护：

- **记忆召回**：`limit=5`，专用线程池 + 硬超时（`RAG_TIMEOUT`，默认 15s），超时或异常一律降级为空；
- **知识库检索**：按 Agent 声明的命名空间检索并归并相似度。

拿到两块文本后按 token 预算裁剪：

```
budget = CONTEXT_MAX_TOKENS - CONTEXT_SYSTEM_RESERVE - CONTEXT_HISTORY_RESERVE
       = 8000 - 800 - 2000 = 5200
```

裁剪策略是**整块丢弃**而不是截断。截断会把半句话塞进 prompt，模型容易顺着半句编；整块丢至少是"这次没参考"，链路里还会留一条 `context.budget` span（`status=skipped`）。线上排查"明明有知识库却答错"时，先看这条 span 比看提示词快得多。

多意图场景还有一个细节：同一轮里几个意图共用同一个 query，`_ctx_cache`（键为 `user_id/query/命名空间`）复用增强结果，第二个意图不再重复检索；缓存在每轮 `dispatch` 开始清空，**不跨轮存活**——跨轮缓存等于把上一轮的客户记忆灌给下一轮，是最容易造成"张冠李戴"的写法。

## 6. 进程内缓存与线程安全

`MemoryTool` 的构造不便宜（要连 Qdrant、建 collection 上下文），所以按 `user_id` 做 `TTLCache`（`maxsize=2000`，`ttl=1800`）。

两个坑：

1. **TTLCache 不是线程安全的**。检索与归档跑在 `asyncio.to_thread` 的工作线程里，会和主线程并发访问同一个 cache —— 必须加 `threading.Lock`，并且用双重检查，避免两个线程同时构造、后者覆盖前者。
2. **缓存与 TTL 要对齐**。`MemoryConfig(working_memory_ttl_minutes=...)` 的默认值是 120 分钟，而配置里的 `WORKING_MEMORY_TTL` 是 1800 **秒**。单位不一致会让 working 层的实际寿命差 4 倍，构造时必须显式换算（`settings.working_memory_ttl // 60`，且下限 1）。

## 7. 归档不能阻塞响应

`persist_after_run` 里至少两次 embedding + 一次 upsert。放进主流程意味着 `done` 事件和连接释放都要等它。

现在的做法是 `schedule_after_run`：用 `asyncio.create_task` 丢到后台，并用一个模块级集合 `_BG_TASKS` 持有强引用（否则任务可能在执行前被 GC 回收，表现是"偶发不记记忆"）。

还有一个容易忽略的点：**后台归档不再往共享 `collector` 写 span**。归档与 trace 落库并发时，同一个 collector 会在 trace 写完之后再被追加 span，导致链路详情里"span 时有时无"，看起来像随机丢数据。归档的观测改为独立日志。

## 8. 过期清理：working 只有 30 分钟寿命

`memories` 表里 `type='working'` 的行按 `created_at < now() - WORKING_MEMORY_TTL` 清理（`purge_expired_working`），已晋升的行类型已被翻转，不在删除条件内。

清理不是每次归档都跑，而是**进程内 300 秒节流**（`_last_working_purge`）：单机部署时这样足够，多副本部署下每个副本各扫一次，靠 SQL 的幂等删除兜住，不需要分布式锁。

## 9. 运营侧：把记忆当数据管

记忆只要进了库，就必须能被运营看见和修正。对应接口（`api/v1/memory.py`）：

| 端点 | 用途 |
|---|---|
| `GET /memory/me` | 客户看自己的记忆 |
| `GET /memory/users/{user_id}` | 坐席按客户查（含类型过滤） |
| `POST /memory/search` | 以指定客户的记忆空间做检索，验证"为什么没召回" |
| `PATCH /memory/{id}` | 改内容或重要性（同时 upsert 镜像，保持向量与 DB 一致） |
| `DELETE /memory/{id}` | 遗忘（先删向量条目，再删镜像行） |

`PATCH` 之后必须同步镜像行，否则后台看到的仍是旧内容——这是"表和向量双写"系统里最常见的一致性缺口。

## 10. 失败模式清单

| 症状 | 根因 | 处置 |
|---|---|---|
| 重启后记忆条数翻倍 | uid 用了 `hash()`，跨进程不稳定 | 换成 `sha1(query)` 之类的稳定摘要 |
| 用户说"昨天说过"但没记住 | 只写达标的记忆，working 未落长期；或会话未正常收尾 | 打开每轮 working + 会话结束全量晋升 |
| 明明是常识也被记住 | 阈值低于规则打分的常量项（`0.35` 时带实体的消息都会达阈） | 上调阈值（示例 `.env` 给的是 `0.6`），或只保留硬实体条件 |
| 有知识库却答非所问 | 上下文预算不足，整块被丢 | 看 `context.budget` span；调 `CONTEXT_MAX_TOKENS` 或缩短历史 |
| 偶发不记记忆 | 后台任务被 GC；或归档异常被吞 | `_BG_TASKS` 持引用；归档失败记 warning |
| 后台记忆页改不动 | `PATCH` 只改了向量，没 upsert 镜像 | 两处同时写 |
| 删了会话，记忆也没了 | `source_message_id` 是 `ON DELETE CASCADE` | 需要长期留存的记忆不要强绑消息行 |
| 启动即挂 | `MEMORY_TYPES` 含 `semantic` 但没有 Neo4j | 默认只留 `working,episodic,perceptual` |

## 11. 边界与未做

- **`semantic` 层默认不启用**：依赖 Neo4j，未部署时包含它会启动失败；镜像行里的 `semantic` 只是展示标签。
- **衰减没有实现**：`MEMORY_DECAY_HALF_LIFE_DAYS` 目前是预留配置，实际只有 working 层按 TTL 过期，长期记忆不会随时间降权。
- **记忆以主体为隔离键**（`user_id` 或 `visitor_id`），不做跨主体的相似记忆合并；同一客户在不同场景下的记忆是否共享，取决于场景与 Agent 的配置，而不是记忆层的自动行为。
- **没有记忆审核流**：写入即生效，运营只能在产生之后改或删。要做到"先审后写"，需要在归档路径上加入待审状态，这属于产品决策而不只是技术改动。

## 12. 小结

记忆这件事，最后落到的都是四句工程纪律：

1. **分层**：短期上下文别落库，长期记忆别当上下文；
2. **无条件写、有条件升**：working 每轮都写，阈值只决定长期化；
3. **可溯源**：稳定 uid + `source_message_id`，否则重复与丢失去向都查不出来；
4. **旁路化**：归档、召回、清理全部有超时、有降级、不阻塞对话。

做到这四点，"记住用户"才是一个可运营的能力，而不是一段靠 prompt 祈祷的上下文。
