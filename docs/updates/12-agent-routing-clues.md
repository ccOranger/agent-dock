# U12 · Agent 路由线索与兜底动态化

> 状态：已落地 ｜ 影响面：路由提示词、路由解析与兜底、agents 表结构、Orchestrator 分发、管理后台 Agent 表单

## 1. 背景与问题

路由分发器的**候选清单**早已动态化（每轮 `list_effective_agents()` 实时拼入 `{agent_list}`），
但**规则段**仍是硬编码文本，造成三处失衡：

1. `prompts/router.py` 写死「若涉及订单号/物流/退换，优先 order_service」「若包含投诉…优先 complaint_handler」
   「若是一般知识问题，knowledge_qa」——三个代码 Agent 有具名先验，后台新建的动态 Agent 只有一行
   `description` 赤膊参赛，天然抢不到路由；
2. 每个 Agent 只有一行描述，**没有「何时选我」的结构化线索**，模型只能凭名称与描述猜适用范围；
3. 兜底 Agent 具名且硬编码（提示词 + `FALLBACK_INTENT` 都写死 `knowledge_qa`）。一旦后台禁用 `knowledge_qa`
   （`list_effective_agents` 会剔除它），提示词仍命令模型输出该名字 → 被 `_parse_intents` 当未注册丢弃
   → 兜底又返回它 → `resolve_business_agent` 返回 `None` → 整轮报 **5006「Agent 未注册」**。

## 2. 路由提示词去具名化（[prompts/router.py](../../backend/app/agents/prompts/router.py)）

- 删除全部具名优先条款，改为通用判据：「选择依据只看各 Agent 声明的适用范围/关键词/典型问法与用户消息的贴合度，
  不要凭名称猜测职责，也不要偏向某个特定 Agent」；
- 追加「逐字照抄清单中冒号前的名称，不得自行翻译或改写」，抑制模型改写/臆造名称；
- 不贴合任何 Agent 时输出的兜底名改为占位符 `{fallback_agent}`（运行时由生效清单算出）。

## 3. 路由线索字段（方案 B）

`agents` 表新增两列（代码 Agent 用同名 Python 类属性声明，二者同构）：

| 字段 | 类型 | 语义 |
|------|------|------|
| `routing_keywords` | JSONB `[]` | 触发关键词，管**字面**命中（客户口语说法，建议 5~12 个） |
| `routing_examples` | JSONB `[]` | 典型用户问法 2~3 条，管**语义**贴合 |

`_format_agent_list` 由「一行 `- name: description`」升级为信息块（仅前 12 个关键词、前 3 条问法注入，控制提示词体积）：

```
- invoice_service：开票服务：受理开票申请、修改抬头
  关键词：发票、开票、抬头、税号
  典型问法：帮我开张发票 / 发票抬头写错了能改吗
```

线索缺失时**降级为一行描述**（不渲染空行、不报错），因此老数据无需强制回填即可继续工作。

代码 Agent 声明见 `OrderAgent` / `KnowledgeAgent` / `ComplaintAgent` 类属性（原先写在提示词里的三条规则被搬回数据）。

## 4. 兜底 Agent 动态派生

- `RouterAgent.resolve_fallback_agent(agents)`：按 `FALLBACK_CANDIDATES = ("knowledge_qa",)` 优先命中生效清单，
  被禁用则降级到**首个生效 Agent**，清单为空回退旧契约名；
- 硬编码常量 `FALLBACK_INTENT` 改为 `fallback_intent(agent_name)`；`_parse_intents(raw, valid_names, fallback_agent=...)`
  第三参默认值保持旧行为，不破坏既有调用；
- `route()` 返回值新增 `fallback_agent`，供调度层回落使用。

## 5. 意图 Agent 不可用时回落一次（Orchestrator）

`dispatch` 中 `resolve_business_agent` 返回 `None`（后台禁用/已删除/模型臆造名称）时：

- 若路由给出了 `fallback_agent` 且尚未回落过 → **回落一次**到兜底 Agent 并继续正常应答（`agent_start/agent_end`、SSE 契约不变）；
- 每轮**最多回落一次**（`fallback_attempted`），避免多意图重复灌兜底回答；
  注：本回落通道只处理“已达标意图指向不可用 Agent”；“路由判不贴合”产生的兜底意图另走 U14 的通道；
- 记忆归档的 `agent_name` 改记 `handled[0]`（实际承接者），不再记不可用的原意图名；
- 无 `fallback_agent`（如单测 FakeRouter）或兜底也不可用时，维持原 **5006** 语义。

## 6. 管理后台（admin-frontend · AgentList）

- 新增/编辑弹窗加「路由关键词」「典型问法」两项：`el-input-tag`（纯输入型标签框，输入后回车成标签，关键词还支持逗号分隔；
  `:max` 分别限 20 / 5 条与后端 schema 一致），配 `.muted hint` 灰字说明（建议条数、不填的后果）与问号 tooltip；
  早期版本用的是 `el-select multiple allow-create`，因为它会先弹出一个空下拉面板，看着像「从固定选项里选」，已换掉；
- 详情弹窗加「路由关键词 / 典型问法」展示，未配置时提示「路由仅依描述判定」；
- 编辑回填来自列表接口的 `routing_keywords` / `routing_examples`。

## 7. 数据结构与接口

- `DynamicAgentCreate` / `DynamicAgentUpdate` 增 `routing_keywords`（≤20）/ `routing_examples`（≤5），
  写入前统一 `strip()` 并剔除空串（防空关键词进提示词）；
- `GET /admin/agents`、`GET /admin/agents/{name}` 对 code/dynamic/stale 三种来源均回显该字段；
- 迁移（[scripts/init_db.sql](../../scripts/init_db.sql) 末尾「存量库增量迁移」段，幂等）：
  `ALTER TABLE agents ADD COLUMN IF NOT EXISTS routing_keywords/routing_examples JSONB NOT NULL DEFAULT '[]'::jsonb;`

## 8. 验证方式

- `tests/test_router.py`：线索渲染、规则无具名硬编码、降级渲染、`resolve_fallback_agent` 三级降级、
  `_parse_intents` 使用传入兜底名；
- `tests/test_dynamic_agents.py`：代码 Agent 均已声明线索、`list_dynamic_agents` 输出线索、创建时空串剔除、列表回显；
- `tests/test_orchestrator_multi_intent.py`：不可用意图回落应答、每轮只回落一次；
- 手工：后台新建动态 Agent 填关键词 → 顾客端问对应话术 → 链路追踪 `router.llm` span 看
  `candidate_agents` 增长与 `raw` 是否命中新 Agent（未填线索时应只有一行描述出现在提示词里）。

## 9. 已知边界

- 关键词是**提示词层面的线索**，不是硬匹配规则；仍需人工维护质量。方案 C1（LLM 输出非法时用关键词做二次定向）、
  C2（用 embedding 预筛 top-k 候选，Agent 数量上去后防提示词膨胀）尚未实现；
- 线索靠运营填写，未填的动态 Agent 仍可能被更“能说会道”的代码 Agent 抢走意图（信息块渲染天然给了已配置者更高权重）；
- 兜底 Agent 应答能力在本篇落地时**实际是失效的**：兜底意图的 confidence 固定为 0.3，必然低于
  `ROUTER_CONFIDENCE_THRESHOLD=0.5`，会在进入业务 Agent 之前就被“低置信转人工”拦掉（典型现象：客户问
  “你是谁”直接转人工）。该缺陷与修法见 [U14](14-fallback-answer-path.md)。
