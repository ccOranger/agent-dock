# U22 用量计量与报表（chat_events / chat_usage_daily）

> 状态：已落地（`SCHEMA_VERSION` 6 → 7，增量 `scripts/migrations/0007_usage_daily.sql`）
> 关联：产品化计划 [P0-2](../productization-plan.md)、[配置说明 3.14](../configuration.md)、[API · Admin · Usage](../api.md)

## 1. 背景与问题

`chat_events` 从 U20 起已全量记录每次聊天请求，但 `total_tokens` 恒为 NULL；`quota_service` 统计的是
"当前登录人创建了几个资源"，`ratelimit` 统计的是"每分钟请求数"，两者都不是"用了多少"。
后台仪表盘只有工具/Agent 调用次数，私有化客户无法回答最基本的商务问题：**这个月这个场景、这个接入方用了多少 token。**

## 2. 设计要点

1. **两处落点**：明细写 `chat_events.total_tokens`（可按会话/访客深挖，随保留期清理），
   聚合写 `chat_usage_daily`（账单依据，长期保留）。
2. **与链路解耦**：`request_traces` 受 `TRACE_ENABLED` / `TRACE_SAMPLE_RATE` 控制，
   用量若跟着采样走就会漏计。因此用量计数直接挂在 `TraceCollector` 上但**不判断 `enabled`**，
   也不经过 `should_persist()`。
3. **必须估算**：`hello_agents` 的 `HelloAgentsLLM.think()` 用 `stream=True` 却未传
   `stream_options={"include_usage": True}`，既不回传 usage 也没有可回读的 `last_usage`。
   所以当前全部是估算值，用 `tokens_estimated` / `estimated_requests` 显式标记，
   将来接入真实 usage 时置 False 即可在报表里区分两套数据。
4. **零 UUID 归属**：`chat_usage_daily` 的主键含 `integration_id`，而 PostgreSQL 唯一约束中 NULL 互不相等，
   匿名请求必须用 `00000000-0000-0000-0000-000000000000` 表示"未归属"，否则每次都会插入新行、聚合失效。

## 3. 关键实现

| 文件 | 职责 |
|---|---|
| `backend/app/utils/tokens.py` | `estimate_tokens` / `estimate_round_tokens` / `using_tiktoken`：tiktoken 惰性加载（离线失败永久降级）、CJK 字符加权、`TOKEN_ESTIMATE_FACTOR` 校准 |
| `backend/app/core/tracing.py` | `TraceCollector.record_prompt` / `record_completion` / `total_tokens`（不受 `enabled` 与采样影响） |
| `backend/app/agents/router_agent.py` | 路由是一次真实 LLM 调用：`system_prompt + query` 计输入、JSON 意图计输出 |
| `backend/app/agents/orchestrator.py` | 上下文工程拼装后的 `enriched_query` 计输入；`_seed_history` 按**实际注入**的历史逐条计数 |
| `backend/app/models/repositories.py` | `ChatEventRepo.record_usage`（UPDATE 明细 + `INSERT ... ON CONFLICT DO UPDATE` 日聚合）、`UsageRepo.summary` / `export_rows` |
| `backend/app/services/chat_service.py` | `_persist_usage`：成功路径在 `done` 事件之前、异常路径在 `raise` 之前各记一次 |
| `backend/app/api/v1/admin_usage.py` | `GET /admin/usage/summary`、`GET /admin/usage/export.csv`（require_admin） |
| `admin-frontend/src/views/UsageView.vue` | 用量报表页（`/usage`）：区间 + 维度切换 + 合计卡 + 柱状趋势 + 导出 |

口径：**一轮 = 1 request**，与 `request_traces` 同口径（走完整服务链路的轮次）；
被鉴权/限流拒绝的请求只留在 `chat_events`，不计入用量。`day` 用进程本地日期，
与 `ChatEventRepo.count_by_scene_today` 和 Redis 日额度桶一致。

## 4. 数据结构

```sql
ALTER TABLE chat_events ADD COLUMN IF NOT EXISTS tokens_estimated BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS chat_usage_daily (
    day                 DATE        NOT NULL,
    scene_code          VARCHAR(32) NOT NULL DEFAULT 'default',
    integration_id      UUID        NOT NULL DEFAULT '00000000-0000-0000-0000-000000000000',
    requests            BIGINT      NOT NULL DEFAULT 0,
    tokens              BIGINT      NOT NULL DEFAULT 0,
    estimated_requests  BIGINT      NOT NULL DEFAULT 0,
    handoffs            BIGINT      NOT NULL DEFAULT 0,
    errors              BIGINT      NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, scene_code, integration_id)
);
```

接口响应（`/admin/usage/summary`）：

```json
{"from":"2026-10-01","to":"2026-10-30","group_by":"day","scene_code":null,
 "rows":[{"day":"2026-10-01","requests":42,"tokens":53120,"estimated_requests":42,"handoffs":3,"errors":1}],
 "totals":{"requests":42,"tokens":53120,"estimated_requests":42,"handoffs":3,"errors":1},
 "tokens_estimated":true}
```

`group_by=scene` 行键为 `scene_code`；`group_by=integration` 行键为 `integration_id` + `integration_name` + `unassigned`。

## 5. 配置项

| 变量 | 默认 | 说明 |
|---|---|---|
| `TOKEN_ESTIMATE_FACTOR` | `1.0` | 估算整体校准系数（下限 0.01），按供应商账单微调 |
| `USAGE_QUERY_MAX_DAYS` | `366` | 报表单次查询跨度上限，超限 400 |

可选依赖：`backend/requirements-optional.txt` 里的 `tiktoken>=0.8.0`。
镜像默认不装（首次使用需下载 BPE 词表，离线/内网会失败；估算只影响精度不影响功能），
装了自动切换为 `cl100k_base` 分词。

## 6. 验证方式

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_usage.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_migrations.py -q   # G1 演练（存量库升级零丢失）
python -m app.migrate --check                                     # 存量库待执行项
python scripts\verify_schema.py                                   # 结构不漂移
```

`test_usage.py` 覆盖：估算口径（CJK / ASCII / 混排 / 空文本）、校准系数、`TRACE_ENABLED=false` 仍计量且不产 span、
`record_usage` 的 UPDATE + `ON CONFLICT DO UPDATE` 语句形态与零 UUID 归属、summary 默认区间/维度透传/越界 400、
CSV 的 BOM 与表头、`agent` 角色访问 403。

本节落地时在**本地真实 PostgreSQL** 上跑过一次端到端（数据用 `2000-01-01` 隔离，跑完删除）：

| 验证点 | 做法 | 结果 |
|---|---|---|
| upsert 累加 | 同一 `(2000-01-01, usage_smoke, 零 UUID)` 连写 3 次（100 / 50 / 0 token） | `requests=3, tokens=150, estimated_requests=2, handoffs=1, errors=1`，是累加不是覆盖 |
| 零 UUID 归属 | `group_by=integration` 查同一区间 | 单行 `integration_id=0000…0000`、`unassigned=true` |
| 导出明细 | `export_rows` 同区间同场景 | 1 行、`tokens=150` |
| 明细回填 | 读 `chat_events` 对应 trace | 最后一次写入 `total_tokens=0`、`tokens_estimated=false` |

同批验证还包括：全量 `pytest`（529 passed / 3 skipped）、`ruff check app tests`、存量库 `python -m app.migrate`
6 → 7 升级后 `--check` 无待执行项、`verify_schema.py` 28 项通过（含“迁移记录一致”“22 张表无漂移”）、
`admin-frontend` `npm run build` 成功。

## 7. 已知边界

- **token 是估算值**：与供应商账单存在系统性偏差（随模型分词器而异），`TOKEN_ESTIMATE_FACTOR` 只能整体校准；
  真实 usage 需要替换 `hello_agents` 的流式调用或改用自带 usage 的客户端。
- 路由提示词包含完整候选 Agent 清单与场景约束，占比不低；估算按实际下发内容计（`_format_conversation` 已把
  query 嵌进 system_prompt，`run(query)` 又发一次），属"宁多算不漏算"，与供应商账单的偏差方向需在首次对账时确认。
- 本轮**不做**套餐、账单、支付：本项只产出"用量事实"，计费规则留给 License 与企业合同。
