# U06 · 工单独立表（tickets）与跨轮记忆

> 状态：已落地 ｜ 影响面：新增 tickets 表、投诉 Agent 工具、业务 Agent 历史注入、后台工单 API

## 1. 背景与问题

**工单**：初版把工单当作 `audit_logs` 的 `action=create_ticket/update_ticket` 记录，字段埋在 `payload` JSONB 里 —— 无法按状态流转、按工单号回查、在后台增改查。

**跨轮失忆**：业务 Agent 每轮 **新建实例**，若不注入历史，模型看不到上一轮，既接不住追问，也不知道刚创建的工单号，无法 `update_ticket` 补录。

## 2. tickets 表（models/entities.py::Ticket、init_db.sql）

| 列 | 说明 |
|----|------|
| `ticket_no` | 对外可读工单号 `TK-XXXXXXXX`（`TK-` + uuid4 hex 大写前 8 位），业务与后台都以它回查 |
| `user_id` / `session_id` | UUID 但 **不建外键**（示例客户名 `alice` 等非 UUID 会降级为 NULL），与 audit_logs 一致，避免脏数据阻塞写入 |
| `category` | 分类（general/logistics/product/service…） |
| `urgency` | `low` \| `medium` \| `high` |
| `status` | `open` \| `in_progress` \| `escalated` \| `resolved` \| `closed` |
| `title` / `content` | 概述，`content` 保留最新 |
| `updates` | JSONB 数组，**追加式**记录每次补录（订单号/快递单号等） |

索引：`status+created`、`user+created`、`session`。

## 3. 工单工具（tools/custom/ticket_tool.py，均 async run）

| 工具 | 作用 | 落库 |
|------|------|------|
| `create_ticket` | 识别到投诉立即建单（信息不全也先建「…（待补充）」），返回工单号 | `TicketRepo.create` |
| `update_ticket` | 客户补充订单号/快递/地址等细节时按 `ticket_id` 追加一条更新 | `TicketRepo.append_update` |
| `escalate` | 升级人工：把该客户/会话最近未关闭工单置 `escalated`，并写一条会话级 `audit_logs` | `TicketRepo.set_status` + `AuditRepo.write` |

`user_id/session_id` 由系统从会话上下文自动注入（见 [U09](09-async-tool-execution.md)），模型无需填写。ComplaintAgent 挂载 `CreateTicketTool / UpdateTicketTool / EscalateTool`。

## 4. TicketRepo 关键行为（models/repositories.py）

- `new_ticket_no()`：`TK-{uuid4.hex[:8].upper()}`；
- `create / append_update / set_status / get_by_ticket_no / latest_open_for / list_filtered / to_dict`；
- `_ticket_uuid()`：非 UUID 值降级为 None，不阻断；
- `updates` 每项带时间戳（UTC ISO），`set_status(note=...)` 会追加一条备注到历史。

## 5. 后台工单 API（api/v1/admin_tickets.py，require_staff）

| 端点 | 说明 |
|------|------|
| `GET /admin/tickets` | 列表：按 `user_id/status/category/urgency/keyword/date_from/date_to` 过滤 + 分页 |
| `GET /admin/tickets/{ticket_no}` | 详情（含 updates 历史） |
| `PATCH /admin/tickets/{ticket_no}/status` | 流转状态，可附一条备注（写入 updates） |
| `POST /admin/tickets/{ticket_no}/notes` | 补录一条信息（与业务 `update_ticket` 同结构） |

请求体：`TicketStatusUpdate{status, note?}`、`TicketNoteCreate{content}`。前端 `admin-frontend/views/TicketsView.vue`。

## 6. 跨轮记忆注入（orchestrator._seed_history）

`Orchestrator` 每轮为每个意图新建 SimpleAgent 后，用最近若干轮历史填充 `SimpleAgent._history`（`_stream_with_tools` 会读取它）：

```python
_HISTORY_WINDOW = 12   # 最多注入 12 条
def _seed_history(simple_agent, history):
    prior = list(history)
    if prior and prior[-1].role == "user":   # 当前这轮输入已在库尾，剔除避免重复
        prior = prior[:-1]
    for h in prior[-_HISTORY_WINDOW:]:
        if h.role in ("user","assistant") and h.content:
            simple_agent._history.append(Message(h.content, h.role))
```

配合 [U02](02-multi-intent-routing.md) 的路由历史注入，模型既能续接追问，又能在下一轮 `update_ticket` 时引用刚创建的工单号。会话 `last_route_to` 由 ChatService 在 `route` 事件时持久化到 `sessions`。

## 7. 记忆归档（与跨轮记忆区分）

`ContextEngineer.persist_after_run` 在轮末按重要性阈值把关键信息写入 `MemoryTool`（episodic）并镜像到 `memories` 表 —— 这是 **跨会话的长期记忆**；而 `_seed_history` 是 **同会话内跨轮** 的即时上下文。二者互补。

## 8. 验证

`tests/test_tickets.py`（增改查/状态流转/补录）、`tests/test_custom_tools.py`、`tests/test_orchestrator_multi_intent.py`（追问续接 + 工单号跨轮引用）。

## 9. 迁移提示

工单从 `audit_logs` 迁到 `tickets` 后，历史 `audit_logs` 中的 `create_ticket` 记录不回灌；开发阶段直接 `scripts/rebuild_and_seed.py` 全量重建即可。
