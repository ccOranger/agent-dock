# U02 · 多意图路由与追问续接

> 状态：已落地 ｜ 影响面：RouterAgent 契约、Orchestrator 调度、SSE 事件、路由提示词

## 1. 背景与问题

初版路由输出单意图 `{agent_name, confidence, reason}`，一条包含多个诉求的消息（如「我要查订单，顺便退款」）只会被分发给一个 Agent。同时，纯追问（「那什么时候到？」）脱离上下文无法判断该续接哪个业务 Agent。

## 2. 意图契约（intents 数组）

RouterAgent 输出升级为 **按主次排序的意图数组**，同时 **向后兼容** 单意图旧格式：

```json
{ "intents": [ { "agent_name": "...", "confidence": 0.0~1.0, "reason": "..." } ] }
```

`route()` 返回结构：

```python
{
  "intents": [...],            # 归一化后的完整意图列表（保持模型给出的主次顺序，不按置信度重排）
  "agent_name": <主意图名>,    # 兼容旧调用方
  "confidence": <主意图置信度>,
  "reason": <主意图理由>,
}
```

归一化规则（`RouterAgent._parse_intents`）：
- 容忍 ```json 代码块与前后杂字（`_extract_json` 取首个 `{...}`）；
- 未注册 / 重复 / 空的 `agent_name` 丢弃；
- 全部无效或解析失败 → 回落 `[{"agent_name": "knowledge_qa", "confidence": 0.3, "reason": "解析失败"}]`；
- 单意图旧格式（顶层含 `agent_name`）同样接受。

## 3. 上下文感知的路由提示词

`ROUTER_SYSTEM_PROMPT`（prompts/router.py）新增占位符并动态渲染：

| 占位符 | 来源 |
|--------|------|
| `{agent_list}` | `list_effective_agents()`（代码 + 启用中的动态 Agent），见 [U03](03-dynamic-agents.md) |
| `{conversation}` | `_format_conversation(history, query)`：最近 `HISTORY_TURNS=6` 轮，每条截断 `HISTORY_CHARS=200`；剔除与当前 query 重复的末尾用户消息 |
| `{last_agent}` | 会话的 `last_route_to`（上一轮承接 Agent） |
| `{max_intents}` | `settings.router_max_intents` |

关键规则：**当前消息只是对上一轮追问/补充时，只输出上一轮承接 Agent 一个意图**，实现续接。

`route()` 每次调用都用 `list_effective_agents()` + history + last_route_to **实时重建 prompt 并新建 SimpleAgent**，因此后台新增 Agent 与被禁用 Agent 立即反映到路由，无需重启。`SimpleAgent.run` 为同步阻塞，用 `asyncio.to_thread` 包裹避免冻结事件循环。

## 4. Orchestrator 串行分块下发

`Orchestrator.dispatch()` 核心流程：

1. 路由得到 `intents`，下发 `route` 事件（`intents` 截断到 `max_intents`，附 `intent_total`）。
2. **低置信度处理**：`qualified = [i for i in intents if confidence >= CONFIDENCE_THRESHOLD]`
   - 主意图不达标（`qualified` 为空）→ 整轮 `handover`（`reason=low_confidence`）；
   - 仅次要意图不达标 → 丢弃该分支，主意图照常应答。
3. `targets = qualified[:max_intents]`，**逐意图串行**：解析 Agent → 上下文工程 → 流式执行。
   - 第二个及以后意图前插入分隔 `text` 事件 `\n\n---\n\n`（markdown 水平线），避免两段回答粘连；
   - 每个分支前后下发 `agent_start` / `agent_end`（带 `intent_index / intent_total`）；
   - 某意图解析失败（未注册）→ 跳过该分支，其余照常；全部不可用才在循环后 `error(code=5006)`。
4. 记忆归档一轮只做一次，记在主意图名下。

## 5. SSE 事件（多意图相关）

| event | data | 时机 |
|-------|------|------|
| `route` | `{agent_name, confidence, reason, intents, intent_total}` | 路由完成 |
| `agent_start` | `{agent_name, intent_index, intent_total, confidence, reason}` | 一个意图分支开始应答 |
| `agent_end` | `{agent_name, intent_index, intent_total}` | 该分支结束 |

## 6. 配置项

| 变量 | 默认 | 说明 |
|------|------|------|
| `ROUTER_MODEL` | `deepseek-flash` | 路由专用轻量模型（需与 LLM provider 匹配） |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.5` | 主意图低于此值整轮转人工 |
| `ROUTER_MAX_INTENTS` | `2` | 单条消息最多串行执行的意图数；设为 `1` 即关闭多意图 |

## 7. 验证

- `tests/test_router.py`：意图解析（新/旧格式、非法名丢弃、兜底）。
- `tests/test_orchestrator_multi_intent.py`：多意图串行、分块分隔、次要意图低于阈值被丢弃、主意图低置信转人工。

## 8. 排障

- 「多意图只应答一个诉求」：看日志 `[route] ... intents=N`。`intents=1` 是模型没拆意图（调提示词/换路由模型）；`intents≥2` 但只走一个分支则受 `ROUTER_MAX_INTENTS` 或阈值限制。
