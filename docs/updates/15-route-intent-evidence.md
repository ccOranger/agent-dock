# U15 · 链路详情显示路由意图依据

> 状态：已落地 ｜ 影响面：agents 路由结果标注、链路 span 属性、管理后台链路详情页

## 1. 背景与问题

链路详情此前只能看出「判给了哪个 Agent、模型自己说的理由、置信度、是否兜底」，但运营反复问的一类问题是：

> 这句话凭什么就被划给这个 Agent 了？到底是哪个词把它抢过去的？

路由是 **LLM 的语义判断**，中间没有分词、也不走关键词匹配，所以"分词结果"这个东西在链路里本来不存在。
要回答上面的问题，能落的不是"分词"，而是**意图依据**：把该 Agent 配置的路由线索（`routing_keywords`）
拿回来在原句里比一遍，回显命中的词，并标出"没命中"与"压根没配线索"这两种截然不同的状况
（配合 [U12](12-agent-routing-clues.md) 的线索管理和 [U13](13-router-agent-readonly.md) 的完整度视图，
才能定位到"线索没配好"还是"模型判错"）。

## 2. 设计取向：本地回显，不动路由契约

被否决的两种做法：

- **让路由模型额外输出触发词**：要改已发布的提示词模板契约 + 解析逻辑，而且模型不保证守格式，
  为了一个展示字段去动全局入口的风险不划算；
- **引入 jieba 真做分词**：分词结果与路由的真实判据无关，展示出来反而误导（看着像依据，其实不是）。

因此采用**零 LLM 成本的本地子串匹配**，并在前端明写"仅供参考、路由实际由语义判定"。
数据只进 `request_traces.spans`（JSONB），**不加表、不加列、不需要迁移**。

## 3. 后端（[agents/router_agent.py](../../backend/app/agents/router_agent.py)）

```python
EVIDENCE_KEYWORDS_LIMIT = 8   # 命中词回显上限，防关键词多的 Agent 撑大 span

def match_routing_clues(query, agent) -> list[str]:
    # 逐个关键词做子串匹配（大小写不敏感），命中词保留配置里的原写法；典型问法是整句，不参与匹配

def annotate_intent_evidence(intents, agents, query) -> list[dict]:
    # 就地给每个意图补 matched_keywords + clue_basis，返回同一列表
```

调用点在 `RouterAgent.route()`：`_parse_intents()` 之后、写链路之前标注一次，因此
SSE 的 `route` 事件 `data.intents[]` 与链路 `route.intent` span 用的是同一份数据。
标注发生在候选清单 `agents`（`list_effective_agents()` 的结果）上，动态 Agent 与代码 Agent 一视同仁；
意图指向清单里不存在的 Agent 时按"没配线索"处理，不抛异常。

链路侧（[core/tracing.py](../../backend/app/core/tracing.py) `record_intents`）新增两个 span 属性：
`matched_keywords`（空则不写，保持 span 精简）与 `clue_basis`。

## 4. `clue_basis` 三态

| 值 | 含义 | 运维读法 |
|----|------|----------|
| `keyword_hit` | 句中出现该 Agent 已配置的关键词 | 依据明确，配的词在起作用 |
| `keyword_miss` | 配了关键词，但这句里一个都没出现 | 判给它是靠语义/描述 → 说明真实说法和配置的线索脱节，值得补词 |
| `no_keywords` | 该 Agent 没配任何路由关键词 | 提示词里只有一行适用范围，抢注意愿天然弱（[U12](12-agent-routing-clues.md)） |

## 5. 前端（[components/TraceTimeline.vue](../../admin-frontend/src/components/TraceTimeline.vue)）

`route.intent` 类型的 span 不再走通用属性平铺，改为独立的「意图依据」块（浅橙底）：
主/次意图标签 + 置信度 + 兜底标记 + `clue_basis` 文案标签 + 命中词 chips + 模型理由全文，
底部一行说明"命中词为本地子串匹配，路由实际由 LLM 语义判定"。其他类型 span 的展示不变。

## 6. 验证

- `tests/test_router.py`：`test_match_routing_clues_hits_substrings`（子串匹配、大小写、空输入）、
  `test_match_routing_clues_is_capped`、`test_annotate_intent_evidence_three_states`、
  `test_annotate_intent_evidence_tolerates_unknown_agent`；
- `tests/test_tracing.py`：`test_record_intents_carries_evidence_fields`（命中词进 span、空数组不写、`clue_basis` 保留）；
- 实测（顾客端 `/chat/message` + 后台 `/admin/traces/{trace_id}`）三态全覆盖：

| 请求 | 意图 | 依据 |
|------|------|------|
| 我的订单 ORD123456 到哪了，顺便问下退款多久到账 | `order_service` 0.95 | `keyword_hit`，命中 `订单`、`ORD` |
| 同上第二意图 | `refund_service` 0.9 | `no_keywords`（该动态 Agent 仍未配线索） |
| 你们退货运费谁承担 | `knowledge_qa` 0.85 | `keyword_miss`（配置的词一个没出现，靠语义判的） |

## 7. 已知边界

- **命中词不等于路由判据**：它是事后字符串比对，模型可能因为完全别的理由选中该 Agent；
  英文/短词（如 `ORD`）会命中订单号里的字母片段，看着"多余"，属预期；
- 典型问法（整句）不参与匹配，所以 `keyword_miss` 不代表线索完全没用；
- 注入路由提示词的关键词上限是 12（`ROUTING_KEYWORDS_LIMIT`，超出即被截断），
  而链路回显的命中词上限是 8 —— 一个词没被回显可能是因为它在 [U13](13-router-agent-readonly.md)
  里就被截断没进提示词；
- 兜底应答轮次（[U14](14-fallback-answer-path.md)）通常表现为 `keyword_miss`/`no_keywords`，
  要确认是否兜底看同一条 span 的 `is_fallback`。
