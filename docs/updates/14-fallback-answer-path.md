# U14 · 兜底意图应答通道（修「你是谁」直接转人工）

> 状态：已落地 ｜ 影响面：路由解析契约、Orchestrator 低置信判定、knowledge_qa 路由线索与提示词、链路 span、后台只读页

## 1. 现象与根因

客户在顾客端问「你是谁」，页面直接显示「已转人工处理（low_confidence）」，没有任何回答。

实跑 `RouterAgent.route("你是谁")` 拿到的是：

```json
{ "intents": [ { "agent_name": "knowledge_qa", "confidence": 0.3,
  "reason": "用户询问身份，属于通用性问题，未贴合其他具体业务场景" } ] }
```

链路三步，每一步单看都"正确"，合起来是死路：

1. **没有 Agent 认领元问题**：`knowledge_qa` 当时的关键词是「是什么 / 怎么用 / 政策 / FAQ…」，典型问法全是业务向，
   「你是谁 / 你好 / 你能做什么」无人认领 → 模型按 [U12](12-agent-routing-clues.md) 的规则判「不贴合任何 Agent」；
2. **提示词命令它填 0.3**：`prompts/router.py` 最后一条规则——不贴合时输出 `{fallback_agent}` 且 **confidence 填 0.3**；
3. **调度层按阈值卡死**：`orchestrator.dispatch` 的 `qualified = [i for i in intents if i["confidence"] >= CONFIDENCE_THRESHOLD]`
   为空 → `handover(reason="low_confidence")` 并 `return`，业务 Agent 一次都没被调用。

结论：**只要兜底置信度低于阈值，兜底 Agent 就永远拿不到应答机会**——「兜底」形同虚设，
任何被判"不贴合"的提问（寒暄、自我介绍、能力咨询）都会直接转人工。U12 §9 当时写的
「兜底 Agent 会先应答（优于整轮报错）」是错误结论，已在该文档中更正。

## 2. 修法

### A. 兜底与阈值解耦（治本）

关键是把「模型拿不准」和「模型确定没人接」这两种低分区分开，靠一个显式标记而不是去调魔数：

- `router_agent.fallback_intent()` 返回的意图带 **`is_fallback: True`**，并新增常量
  `FALLBACK_CONFIDENCE = 0.3`（与提示词里"confidence 填 0.3"的字面约定对应，注释互相指认）；
- `orchestrator.dispatch` 在 `qualified` 为空时**不再直接转人工**，先看有没有兜底意图：

```python
fallback_intents = [i for i in intents if i.get("is_fallback")]
if settings.router_fallback_answer and fallback_intents:
    qualified = fallback_intents          # 交给兜底 Agent 应答
else:
    ... handover(low_confidence) ...      # 旧行为
```

- 新增开关 **`ROUTER_FALLBACK_ANSWER`**（默认 `true`）：置 `false` 即完全恢复旧行为，
  适合"宁转人工也不让通用问答兜着"的租户；
- **模型自己给出的低分意图不带该标记**，仍然照常触发转人工——低置信保护能力没有被削弱，
  只是不再误伤兜底通道；
- 链路侧 `route.intent` span 增加 `is_fallback` 字段，后台时间线能直接看出这一轮是"兜底应答"还是"正常命中"。

### B. knowledge_qa 认领元问题（治标，立刻见效）

让「你是谁」这类提问走**正常高置信意图**，而不是依赖兜底通道：

- `routing_keywords` 前置元问题词：`你是谁 / 你好 / 能做什么`，并与"政策/规则"语义重叠的
  「条件 / 说明」、低频的「运费 / 营业时间」一起腾位，总数压回 **12 个以内**——
  因为注入上限 `ROUTING_KEYWORDS_LIMIT=12` 是按顺序截断的，堆在后面根本进不了提示词
  （改前该 Agent 有 13 个关键词，最后一个「营业时间」一直是被静默丢弃的）；
- `routing_examples` 首条换成「你是谁，都能帮我做什么」；
- `KNOWLEDGE_SYSTEM_PROMPT` 增加一段：客户问"你是谁/你能做什么/你好"时直接自我介绍并说明可提供的服务，
  **不检索知识库、也不因此建议转人工**（原规则第 3 条"知识库没答案就建议转人工"会误伤这类提问）。

## 3. 效果对比

| 输入 | 改前 | 改后 |
|------|------|------|
| 你是谁 | `handover(low_confidence)`，无应答 | `knowledge_qa` 正常意图命中（B），自我介绍 + 引导 |
| 一句确实无人认领的话 | `handover(low_confidence)` | 兜底意图 → `knowledge_qa` 应答（`is_fallback=true` 可在链路中辨识） |
| 模型给了 0.4 分的正常意图 | `handover(low_confidence)` | 不变，仍转人工 |
| `ROUTER_FALLBACK_ANSWER=false` | — | 全部回到改前行为 |

## 4. 涉及文件

| 文件 | 改动 |
|------|------|
| [core/config.py](../../backend/app/core/config.py) | `router_fallback_answer: bool = True` |
| [agents/router_agent.py](../../backend/app/agents/router_agent.py) | `FALLBACK_CONFIDENCE` 常量；`fallback_intent()` 带 `is_fallback`；`route()` 的兜底判定改看标记；`describe_router_agent()` 输出 `fallback_confidence` / `fallback_answer` / `fallback_answer_effect` |
| [agents/orchestrator.py](../../backend/app/agents/orchestrator.py) | 低置信分支放行兜底意图（受开关控制） |
| [core/tracing.py](../../backend/app/core/tracing.py) | `record_intents` 的 `route.intent` span 带 `is_fallback` |
| ~~`agents/business/knowledge_agent.py`~~ / ~~`agents/prompts/knowledge.py`~~ | 当时由代码托管的 knowledge Agent 重排关键词并认领元问题（**已移除**：业务 Agent 全部改为后台动态配置，线索与提示词在 `agents` 表，见 [U03](03-dynamic-agents.md)） |
| `backend/.env` / `.env.example` | `ROUTER_FALLBACK_ANSWER=true` |
| [views/RouterInfoView.vue](../../admin-frontend/src/views/RouterInfoView.vue) | 「兜底策略」行显示兜底置信度与应答模式（[U13](13-router-agent-readonly.md)） |

## 5. 验证方式

- `tests/test_router.py`：`test_fallback_intent_is_marked_so_it_survives_threshold`（兜底带标记、
  正常低分意图不带）、`test_knowledge_qa_claims_meta_questions`；两处兜底意图全等断言已含 `is_fallback`；
- `tests/test_orchestrator_multi_intent.py`：`test_fallback_intent_is_answered_not_handed_over`（兜底被应答、
  `route` 事件带出标记）、`test_fallback_answer_disabled_falls_back_to_handover`（开关关闭恢复转人工）；
- 手工：顾客端问「你是谁」应有自我介绍应答；后台「路由分发器」页看兜底模式；链路页 `route.intent` span 的
  `is_fallback` 字段辨识兜底轮次。

## 6. 已知边界

- 兜底应答的轮次在链路里 `outcome=answered`，若运营希望"兜底也要算作需要人工跟进"，
  需另加维度（可按 `route.intent.is_fallback` 过滤，暂不做统计页）；
- `FALLBACK_CONFIDENCE=0.3` 与提示词里的字面 0.3 仍是**两处约定**，改一处必须同步另一处
  （未做成占位符注入，是为了不改动已发布的模板契约）；
- 兜底 Agent 的应答质量取决于 `knowledge_qa` 的提示词；若希望它一律引导转人工，
  可把 `ROUTER_FALLBACK_ANSWER` 置为 `false`，或在 `KNOWLEDGE_SYSTEM_PROMPT` 中要求兜底场景输出转人工话术。
