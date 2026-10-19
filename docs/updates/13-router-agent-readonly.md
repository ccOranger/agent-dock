# U13 · 路由分发器后台只读视图

> 状态：已落地 ｜ 影响面：agents 路由自省、admin 接口（只读）、管理后台菜单/页面

## 1. 背景与问题

业务 Agent 已能在后台增删改查（[U03](03-dynamic-agents.md)、[U11](11-code-agent-transparency-state.md)、[U12](12-agent-routing-clues.md)），
但**路由分发器仍然是纯代码对象**，运营侧有两个盲区：

1. 想知道「现在路由到底怎么判的」——模型、单条消息最多拆几个意图、置信度阈值多少、兜底给谁、
   提示词长什么样，只能翻代码与 `.env`；
2. 新建的动态 Agent 路由不到时，无法判断是**没进候选清单**（被禁用/未启用），还是**进了但没配线索**
   （提示词里只有一行描述，抢注意愿天然弱），还是**线索配了但被条数上限截断**没进提示词。

## 2. 设计取向：只读，不做在线修改

路由 Agent 与业务 Agent 的性质不同：业务 Agent 的提示词是「领域话术」，改坏了只影响一个分支；
路由提示词是**全局唯一入口**，模板写坏（例如删掉 `{agent_list}`）会让所有意图识别一起失效，
而它本身由代码 + `.env` 定义、不在 `agents` 表中。因此本期**只提供查看**：

- 接口只有两个 GET，任何写方法返回 **405**（有测试守住，防止后来误开修改入口）；
- 返回值带 `editable: false`，前端页面顶部 `el-alert` 明确「改参数请改 backend/.env 并重启」。

被否决的两种做法：① 往 `agents` 表塞一行 `name='router'` 复用现成 CRUD——`list_effective_agents`
会把它当成候选，出现「路由把自己也当业务 Agent」的自指；② 建 `router_config` 表 + 在线编辑模板——
风险高于收益，等真有线上调参需求再单独评估。

## 3. 后端：自省函数（[agents/router_agent.py](../../backend/app/agents/router_agent.py)）

路由相关的常量此前只被内部使用，现补一层只读自省，**不访问 LLM、不产生副作用**：

| 函数 | 作用 |
|------|------|
| `describe_router_agent()` | 静态档案：`name` / `agent_type=router` / `source=code` / `editable=false` / 职责、`model`、`max_intents`、`confidence_threshold`（含生效说明）、`fallback_candidates`、`history_turns`/`history_chars`、`keywords_limit`/`examples_limit`、`system_prompt_template` 原文、`placeholders`（占位符 → 含义）、`definition_files`、`env_keys` |
| `render_router_prompt()` | 按 `list_effective_agents()` 实时渲染出「这一轮会真正发给模型的提示词」，并返回 `prompt_chars`、`candidate_count`、`fallback_agent`、`fallback_from_candidates`、`candidates` |
| `_candidate_brief()` | 单个候选 Agent 的路由视角：完整线索 + **实际注入的部分**（受上限截断）+ `clue_level` |

`clue_level` 四值 `full / keywords_only / examples_only / description_only`，用于直接指出
「这个 Agent 在提示词里只有一行描述」——这正是动态 Agent 路由不到的最常见原因。

顺带把 `_render_prompt` 收敛为 `@staticmethod`（自省与实例无关），`SimpleAgent` 名称改用常量
`ROUTER_AGENT_NAME`，避免档案名与实际运行名漂移。

## 4. 接口（[api/v1/admin_router.py](../../backend/app/api/v1/admin_router.py)，`require_admin`）

```
GET /api/v1/admin/router            # 基本档案
GET /api/v1/admin/router/prompt     # 生效提示词预览 + 候选清单
```

无 POST/PUT/PATCH/DELETE。`/prompt` 会读 `agents` 表（复用 `list_effective_agents`），
与每轮对话的实时渲染口径一致，因此预览所见即线上所得（仅对话上下文与上一轮 Agent 为空）。

## 5. 前端（[views/RouterInfoView.vue](../../admin-frontend/src/views/RouterInfoView.vue)）

菜单「Agent 管理」下新增**「路由分发器」**（`/router`，仅 admin 可见），顶部一条只读提示 alert，页面如下：

1. **基本信息** —— `el-descriptions` 逐项展示，每项都带出对应环境变量名与灰字说明
   （`ROUTER_MAX_INTENTS=1` 即关闭多意图；置信阈值「主意图不达标整轮转人工，仅次要意图不达标只丢该分支」；
   兜底 Agent「由生效清单实时派生」，若优先候选被禁用会显示 warning 标签）；
2. **三个 Tab**（生效提示词 / 候选 Agent 清单 / 提示词模板与占位符）
   - *当前生效提示词*：完整渲染结果 + 候选数/字数 + 复制按钮；
   - *候选 Agent 清单*：表格列出 名称/来源(代码|动态)/适用范围/关键词/典型问法/线索完整度；
     关键词超出注入上限时显示 `+N` 标签，tooltip 说明「仅前 12 个注入，其余未进入提示词」；
   - *提示词模板与占位符*：占位符含义表 + 模板原文，并说明规则段不硬编码具名 Agent；
3. Agent 管理页的关键词 hint 补一句「实际注入了哪几条，可在『路由分发器』页查看」，形成排查动线。

## 6. 验证方式

`tests/test_admin_router.py`（Repo 全 mock，不依赖真实 DB/LLM）：

- 档案字段与 `.env`/默认值一致，`editable is False`，占位符集合完整；
- 提示词随库中动态 Agent 变化（含 `关键词：` / `典型问法：` 行）；
- `clue_level` 分级正确；15 个关键词只注入前 12（`k12` 不出现在提示词里）；
- 禁用 `knowledge_qa` 后兜底降级到生效清单首项且 `fallback_from_candidates=false`；
- 四个写方法在 `/admin/router`、两个在 `/admin/router/prompt` 全部 405；
- `agent` / `customer` 角色访问 403（`2003`）。

## 7. 已知边界

- 预览不含真实对话上下文：追问续接（`{conversation}` / `{last_agent}`）只有在实际请求中才填入，
  要看某轮到底送了啥，仍去链路追踪的 `router.llm` span；
- 不提供「试路由」（输入一句话看命中哪个 Agent），需要时可在该页追加 dry-run 端点（复用 `RouterAgent.route`，不落库）；
- 若未来要把路由参数搬进 DB，需先解决 `orchestrator.CONFIDENCE_THRESHOLD` 与 `RouterAgent.__init__` 里
  模型名在 **import/构造期固化** 的问题，改成调用期读取，否则改了库里的值也不生效。
