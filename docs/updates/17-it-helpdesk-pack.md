# U17 · IT Helpdesk 场景包与包启停（历史）

> 目标：用第二个场景包验证“同一内核、不同场景”——内核不新增业务概念，只补通用能力
> （`features` 声明、包资产归属、包级启停），IT 业务全部落在 `packs/it_helpdesk/`。
> 关联：[开发清单 M4](../development-checklist.md)。
>
> **后续变更**：场景包机制已移除，文中 manifest、包启停与 `app/packs` 路径均为历史记录；
> IT 能力现位于 `app/agents/business/it_*.py`、`app/agents/prompts/it_*.py`、`app/tools/custom/it_*.py`。

## 1. 背景与问题

M1–M3 把客服业务收敛到 `packs/customer_service/`，但一直没有第二个包验证：

- manifest 是否能承载不同领域的 Agent/工具/知识库；
- 运营台能否看见并整体启停一个包，导航能否按包显隐；
- 共享内核表（工单）能否被不同场景复用，而不是复制一套。

M4 的 IT Helpdesk 包用 3 个 Agent + 4 个工具 + 3 个知识库命名空间回答这三个问题。

## 2. 方案与取舍

| 决策点 | 选择 | 理由 |
|---|---|---|
| 工单模型 | 复用 `tickets` 表，`category=it_*`，`metadata.pack=it_helpdesk` | 避免为演示包新增表与迁移；后台工单页与跨轮记忆直接复用 |
| 包开关 | 复用 `agents.enabled`，PATCH 批量写包内代码 Agent | 不新增 `pack_settings` 表；与现有 Agent 停用逻辑一致，下一轮生效 |
| 默认状态 | IT 包 `enabled: true` | 开箱即可演示“同一内核两个包”，停用客服包由运营台操作 |
| 演示知识 | 种子写文档归档行；另提供 `seed_it_kb.py` 写真实 FAQ 向量 | 无 embedding 时仍可启动；有 embedding 时可演示 RAG |

## 3. 包内容

`backend/app/packs/it_helpdesk/`：

```
manifest.yaml
agents/knowledge_agent.py   # it_knowledge
agents/incident_agent.py    # it_incident
agents/asset_agent.py       # it_asset
prompts/{knowledge,incident,asset}.py
tools/ticket_tool.py        # create_it_ticket / update_it_ticket / escalate_it
tools/asset_tool.py         # query_it_asset
tools/mock_assets.py        # 6 条只读资产快照
```

路由线索（写入路由提示词，参与 LLM 意图判定）：

- `it_knowledge`：VPN、账号、密码、邮箱、登录、网络、打印机、软件、权限、安全、安装、申请；
  检索 `it_default` + `it_network` + `it_assets`。
- `it_incident`：故障、报修、坏了、蓝屏、死机、开不了机、断网、无法上网、系统崩溃、报错、卡顿、中毒；
  检索 `it_default`。
- `it_asset`：资产、设备、电脑、显示器、序列号、SN、保修、领用、IP、MAC、编号、型号；
  检索 `it_assets`。

工具行为：

- `create_it_ticket`：受理即建单，`metadata_={"pack": "it_helpdesk"}`，返回 `TK-` 工单号；
- `update_it_ticket`：按工单号补录，找不到返回 `ok=false`；
- `escalate_it`：把当前会话未关闭工单置 `escalated`，并写审计日志（`payload.pack=it_helpdesk`）；
- `query_it_asset`：按资产编号/SN/领用人/型号/IP/MAC/品类别名查询，返回资产副本。

## 4. 内核改动（通用能力）

### 4.1 manifest 增加 `features`

`PackManifest` 新增 `features: tuple[str, ...]`，由 manifest 顶层 `features` 解析；
两个内置包均声明 `tickets`，前端据此显示工单入口。

加载器新增：

- `scan_manifests()`：全部清单（含 manifest 停用包），供运营台展示；
- `get_code_agent_packs()` / `get_custom_tool_packs()`：资产 name → 包 name（含停用包）；
- `get_enabled_features()`：启用包 features 去重保序。

`load_manifests()` 仍只返回启用包，运行时资产装载不感知停用包。

### 4.2 包列表与启停接口

`app/api/v1/admin_packs.py`：

```
GET   /api/v1/admin/packs          # require_staff
PATCH /api/v1/admin/packs/{name}   # require_admin，body: {"enabled": bool}
```

返回项字段：`name`、`display_name`、`version`、`enabled`、`manifest_enabled`、`features`、
`agents[]`（name/display_name/description/enabled）、`tools[]`（name/description）、`knowledge_namespaces[]`。

规则：

- 包 `enabled = manifest.enabled && 包内至少一个代码 Agent 启用`；DB 无行视为启用。
- PATCH 开启前补齐缺失的 `agents` 行，再 `set_enabled_many` 批量设置。
- manifest 停用的包停用幂等、开启返回 `409/3002`；未知包返回 `404/3001`。
- 停用 Agent 不卸载工具、不删知识库向量；彻底移除需改 manifest 重启。

### 4.3 工单元数据

`TicketRepo.create` 增加 `metadata_` 参数（默认 `{}`），IT 工具写入 `{"pack": "it_helpdesk"}`；
客服工具行为不变。`TicketRepo.to_dict` 回显 `metadata`，后台可按来源区分工单。

## 5. 运营台

- 新增「场景包」页（admin）：卡片展示包状态、Agent 启用数、工具、知识库命名空间与 `features`；
  `el-switch` 调 PATCH，停用前二次确认；manifest 停用包显示提示且不可开启。
- 新增 `stores/packs.ts`：布局挂载时拉取 `/admin/packs`；请求失败回退为“全部功能可见”，
  避免后端异常导致后台菜单消失。
- 工单入口按 `packs.hasFeature('tickets')` 显隐。
- 工单页去除客服专属文案：标题「工单管理」、列「问题描述」/「用户 ID」、分类与补录占位改为 IT 示例。

## 6. 演示数据

- `scripts/init_db.sql`：`knowledge_bases` 增加 `it_default`、`it_network`、`it_assets`。
- `scripts/seed_data.py`：
  - `SEED_AGENTS` 增加 `it_knowledge` / `it_incident` / `it_asset`（sort 4–6，始终写入）；
  - 演示模式增加 3 条 IT 文档归档行；
  - 演示模式增加 4 张工单 `TK-IT0001..0004`，覆盖 open / in_progress / escalated / resolved。
- `scripts/rebuild_and_seed.py`：同步生成 tickets 插入与计数校验。
- `scripts/seed_it_kb.py`（可选）：3 篇 FAQ 经 `DocumentService.create_entry` 写入 Qdrant；
  已存在同名文档则跳过，单条失败继续，最后以退出码报告是否有失败。

## 7. 验证

```bash
cd backend
.venv/Scripts/python.exe -m ruff check app tests       # All checks passed
.venv/Scripts/python.exe -m pytest tests -q            # 324 passed / 3 skipped
cd ..
docker compose up -d --build backend web               # 两端镜像构建通过
```

新增/更新测试：

- `tests/test_packs_loader.py`：双包发现、6 Agent、13 工具、6 命名空间、features、停用包扫描；
- `tests/test_admin_packs.py`：聚合结构、部分/全部停用语义、开启补行、manifest 停用 409、未知 404、RBAC；
- `tests/test_it_helpdesk_pack.py`：路由线索、资产别名/大小写/副本、4 个工具的契约、`metadata` 标记与回显；
- `tests/test_custom_tools.py`：工具目录期望集合加入 4 个 IT 工具。

M4-4 手工验收（Docker，2026-10-20）：`PATCH /admin/packs/customer_service {"enabled":false}` 后包内 3 个 Agent 全部停用，
客户问“笔记本开不了机，一直黑屏，资产编号 IT-LT-1002”仍正常应答：路由 `it_incident` + `it_asset`，
工具 `create_it_ticket` / `query_it_asset` 执行成功，回复含工单号 `TK-2E2A151A`，
链路 `outcome=answered`，库中 `tickets.metadata = {"pack": "it_helpdesk"}`。

## 8. 已知边界

- 包开关只作用于代码 Agent；动态 Agent 需在「Agent 管理」单独启停。
- 工具与 Agent 名全局唯一，跨包重名时先登记者生效。
- 工单仍是内核表；包级工单表/路由前缀留到后续版本。
- `it_asset` 只查进程内假数据，不接真实 CMDB；生产替换 `mock_assets` 即可。
- 包装载缓存按进程生效，改 manifest 后必须重启。
