# 开发清单（v0.1）

> 用途：把定位与解耦方案落成可执行任务，按里程碑推进。
> 关联文档：[产品定位](./positioning.md)、[领域解耦清单](./domain-decoupling.md)、[开源发布检查清单](./open-source-release.md)。
> 估算假设：单人开发，每周投入 20–30 小时；以"可发布"为目标，不为完美主义加需求。

## 0. 当前基线

| 状态 | 内容 |
|---|---|
| 已完成 | P0/P1 性能与安全修复；278 passed / 1 skipped；两端构建通过 |
| 已完成 | 产品定位、领域解耦、开源发布三份文档 |
| 未开始 | LICENSE、Dockerfile、docker-compose、初始化向导、CI |
| 未开始 | 领域解耦 P0（文件仍在 `agents/`、`tools/custom/` 原位） |
| 待处理 | 演示默认口令、`.review_diff.txt`、README 仍是客服叙事 |
| 待决策 | 产品名、许可证、第二个场景包排期 |

## 1. 里程碑总览

| 里程碑 | 目标 | 预估 | 退出标准 |
|---|---|---|---|
| M0 工程基线 | 仓库具备开源项目的基本形态 | 2–3 天 | CI 绿、LICENSE/SECURITY 齐备、演示口令受控 |
| M1 解耦 P0 | 客服资产归位 `packs/` | 1–2 天 | 文件迁移完成，278 测试全绿，行为不变 |
| M1.5 前端单仓化 | 两个前端共享代码 | 2–3 天 | workspaces 生效，http/auth/SSE/types 只有一份，两端构建通过 |
| M2 一键部署 | 陌生人 30 分钟跑通 | 5–8 天 | `docker compose up` 后完成首次对话 |
| M3 文档物料 | 能对外讲清楚并演示 | 3–5 天 | README/Quickstart 可用，截图与 GIF 就绪 |
| M4 第二场景包 | IT Helpdesk 可切换 | 5–10 天 | 同一内核切换包，端到端可用 |
| M5 v0.1 发布 | 正式对外 | 2–3 天 | 发布检查清单全部勾选 |
| M6 发布后 | 按反馈迭代与企业版能力 | 持续 | 按 issue 优先级排期 |

建议顺序：**M0 → M1 → M1.5 → M2 → M3 → M4 → M5**。M3 可与 M2 并行（等部署稳定后再截图）。

总预估约 28–33 人日：按每周 20–30 小时投入约 8–10 周；若全职投入可压缩到 6 周左右。建议预留 30% 缓冲。

## 2. 任务清单

### M0 工程基线（发布阻塞）

- [x] **M0-1 确认许可证并添加 LICENSE**（0.5d）
  - 默认 Apache-2.0；企业版功能后续独立目录/仓库
  - 验收：根目录有 `LICENSE`，README 有许可证段落
- [x] **M0-2 仓库清理**（0.5h）
  - 删除 `.review_diff.txt`；补 `.gitattributes`（`* text=auto`）
  - 验收：`git status` 无临时文件
- [x] **M0-3 接入 ruff**（0.5–1d）
  - 加入开发依赖与 `pyproject.toml` 配置（line-length、isort）
  - 先修 `--fix` 能自动修的，剩余逐条处理或加 `noqa`
  - 验收：`ruff check app tests` 通过，并入 CI
- [x] **M0-4 CI 工作流**（0.5–1d）
  - 后端 job：Python 3.12 + 依赖缓存 + pytest
  - 前端 job：Node 20+，两端 `npm ci && npm run build`
  - 验收：push 后 Actions 绿；徽章可放 README
- [x] **M0-5 社区文件**（0.5d）
  - `SECURITY.md`、`CONTRIBUTING.md`、issue 模板（bug/feature/deploy help）
  - 验收：模板可在 GitHub 选择
- [x] **M0-6 演示口令受控**（0.5d）
  - `seed_data.py` 增加 `SEED_DEMO=true` 开关；未开启不建演示账号
  - 首次登录强制改密，或初始化时生成随机密码并只打印一次
  - 验收：默认启动无固定口令账号
- [x] **M0-7 版本与锁文件**（0.5d）
  - 确认 `package-lock.json` 入库；`requirements.txt` 版本策略写清楚
  - 验收：干净环境可复现安装

> M0 验收（2026-10-20）：本地 ruff、pytest（277 passed / 2 skipped）、两端构建均通过；GitHub Actions 首次运行已全绿（backend lint + pytest、frontend 两端 build）。CI 环境 pytest 275 passed / 4 skipped（Linux 跳过 2 个 Windows 专用用例）。

### M1 领域解耦 P0（低风险归位）

- [ ] **M1-1 建立场景包目录**（0.5d）
  - `backend/app/packs/customer_service/{agents,prompts,tools,seeds,ui}/`
  - 写 `manifest.yaml` 最小版本（name/version/agents/tools）
- [ ] **M1-2 迁移提示词**（0.5d）
  - `agents/prompts/{knowledge,order,complaint}.py` → 包内；`router.py` 暂留内核
  - 验收：所有 import 更新，测试通过
- [ ] **M1-3 迁移领域工具**（0.5d）
  - `tools/custom/{order_tool,refund_tool,logistics_tool,ticket_tool,mock_data}.py` → 包内
  - 保留 `base.py`、`custom_catalog.py`、`custom_loader.py` 在内核
  - 验收：`custom_catalog` 仍能发现包内工具
- [ ] **M1-4 迁移业务 Agent**（0.5d）
  - `order_agent.py`、`complaint_agent.py` → 包内
  - `knowledge_agent.py` 先留内核（作为通用模板），M6 再参数化
  - 验收：路由结果与迁移前一致
- [ ] **M1-5 最小包加载器**（1d）
  - 新增 `packs/loader.py`：导入包模块并返回 Agent 列表
  - `registry.py` 改为从 loader 获取，删除对客服模块的硬 import
  - 验收：内核不再直接 import 客服 Agent；测试全绿
- [ ] **M1-6 回归验证**（0.5d）
  - `pytest -q`、两端 build；手工跑一轮多意图 + 工具 + 转人工
  - 验收：278 passed / 1 skipped，无行为变化

### M1.5 前端单仓化（v0.1）

目标：保留管理端 SPA + 聊天端两个产物，但共享代码只维护一份；为 v0.2 挂件化铺路。

- [ ] **M1.5-1 建立 workspaces**（0.5d）
  - 根目录 `package.json`：`workspaces: ["frontend", "admin-frontend", "packages/*"]`
  - 依赖提升，`npm install` 后两端可独立启动
- [ ] **M1.5-2 抽取共享 HTTP 客户端**（0.5–1d）
  - `packages/shared/src/http.ts`：`createHttpClient({ tokenKey, refreshKey, loginPath })`
  - 401 单飞刷新只保留一份；两套 token key 继续隔离
  - 验收：两个 `http.ts` 退化为薄封装或删除
- [ ] **M1.5-3 抽取共享 auth store**（0.5d）
  - `createAuthStore(tokenKey, refreshKey)`；两个应用各自实例化
  - 验收：登录、登出、刷新行为不变
- [ ] **M1.5-4 抽取 SSE 与类型**（0.5d）
  - `packages/shared/src/sse.ts`、`types.ts`；聊天端改为引用
- [ ] **M1.5-5 统一脚本与 CI**（0.5d）
  - 根脚本：`dev:admin`、`dev:chat`、`build`；CI 仍分别构建两个产物
  - Vite alias `@shared`，两端配置一致
- [ ] **M1.5-6 回归验证**（0.5d）
  - 两端 `npm run build`、登录 + 刷新 token、聊天流式渲染
  - 验收：无功能变化，重复文件清零

### M2 一键部署与初始化（发布关键路径）

- [ ] **M2-1 后端镜像**（1d）
  - Python 3.12-slim；非 root；依赖层缓存；`/health` HEALTHCHECK
  - 镜像内含 MCP stdio 所需的 Python 运行时
  - 验收：镜像可启动并通过健康检查
- [ ] **M2-2 前端镜像**（1d）
  - 一个 web 镜像：多阶段构建管理端与聊天端两个产物，Nginx 托管
  - 路由：`/` 聊天端、`/admin/` 管理端（管理端 `base: '/admin/'`）
  - `/api` 反代后端；SSE 关闭缓冲
  - 验收：单容器可访问两个入口并登录
- [ ] **M2-3 Compose 编排**（1–2d）
  - `postgres`（pgvector）、`qdrant`、`redis`、`backend`、`web`（一个 Nginx 托管两个前端产物）
  - 命名卷、healthcheck、`depends_on`；`.env.example` 全量配置
  - 验收：一条命令全部 healthy
- [ ] **M2-4 初始化数据**（0.5d）
  - 首次启动执行 `init_db.sql`；`SEED_DEMO=true` 时导入演示数据
  - 验收：空库启动后可直接登录
- [ ] **M2-5 初始化向导**（2–3d）
  - 二选一：CLI `python -m app.setup` 或 Web `POST /api/v1/setup`
  - 创建管理员、生成随机 JWT、写入配置、测试模型连通性
  - 验收：全新环境按向导完成配置，无需手工 SQL
- [ ] **M2-6 自检与错误提示**（1d）
  - 启动日志明确输出数据库/Qdrant/Redis/模型状态；失败给可操作提示
  - 验收：断掉 Qdrant 时错误可读
- [ ] **M2-7 反向代理样例**（0.5d）
  - Nginx 配置：SSE、`X-Forwarded-For`、`TRUSTED_PROXIES` 说明
- [ ] **M2-8 全新机器验收**（0.5d）
  - 清空本地环境，从 README 走一遍，记录耗时与卡点
  - 验收：30 分钟内完成首次对话

### M3 文档与演示物料

- [ ] **M3-1 产品命名**（决策，0.5d）
  - 中性平台名；README、页面标题、Docker 项目名统一
- [ ] **M3-2 README 重写**（1d）
  - 定位、能力、架构图、快速开始、场景包、路线图、许可证
- [ ] **M3-3 运维文档**（1–2d）
  - `quickstart.md`、`configuration.md`、`deployment.md`、`faq.md`
- [ ] **M3-4 截图与 GIF**（1d）
  - 管理台、链路详情、记忆页、Agent 配置、场景包切换
- [ ] **M3-5 技术文章**（2–3d，可延后）
  - 多意图路由、记忆生命周期、全链路追踪；至少发布 1 篇配合 v0.1

### M4 IT Helpdesk 场景包

- [ ] **M4-1 包骨架与提示词**（2d）
  - `packs/it_helpdesk/`：manifest、3 个 Agent（知识/故障/资产）、提示词
- [ ] **M4-2 工具复用**（1–2d）
  - 复用工单工具；新增"资产查询"示例工具；数据全部假数据
- [ ] **M4-3 种子与演示**（1d）
  - IT 知识库文档、常见问题、示例工单
- [ ] **M4-4 包切换开关**（1–2d）
  - 后台可见 `packs` 列表与启用/停用；导航按包显隐
  - 验收：停用客服包后系统仍可用
- [ ] **M4-5 场景包文档**（1d）
  - 如何新增一个包（manifest 字段、目录约定、注册方式）

### M5 v0.1 发布

- [ ] **M5-1 全量验收**（0.5d）
  - 跑 [开源发布检查清单](./open-source-release.md) 第 10 节
- [ ] **M5-2 Release Notes 与 CHANGELOG**（0.5d）
- [ ] **M5-3 打 tag 与构建镜像**（0.5d）
- [ ] **M5-4 GitHub/Gitee 发布**（0.5d）
- [ ] **M5-5 发布公告**（0.5d）
  - 技术社区 1–2 处；附 demo GIF 与部署入口

### M6 发布后（按反馈排期）

按优先级从高到低：

1. 部署问题修复（Top 5 卡点）
2. 包加载器 manifest 化、`router.py` 提示词参数化
3. 聊天挂件 iframe 化（v0.2）、按需 Web Component
4. `context_engineer` 信号规则下沉到包
5. `canned_scripts` → `response_templates`，后台 IA 去客服化
6. Alembic/版本化迁移
7. 评测与回归（用 `request_traces` 建 golden set）
8. RBAC、SSO、审计增强（企业版）
9. `app_users` 合并、Ticket 迁包
10. `tenant_id` 预留与多租户（仅 SaaS 时）
11. 技术债：MCP 发现单飞锁、RAG 信号量模块级化、后台列表 N+1

## 3. 优先级与取舍

| 级别 | 任务 | 说明 |
|---|---|---|
| 必须（v0.1 阻塞） | M0、M1、M1.5、M2、M5 | 不完成无法发布 |
| 应该（发布体验） | M3、M4 | 影响传播与第二个场景验证 |
| 可以（发布后） | M6 | 按真实反馈排期 |

冻结规则：M2 开始后不再新增功能；发现新想法记入 issue，不插队。

## 4. 第一周建议日程

| 天 | 任务 | 产出 |
|---|---|---|
| Day 1 | M0-1、M0-2、M0-6 | LICENSE、仓库清理、演示口令开关 |
| Day 2 | M0-3、M0-5 | ruff 配置与社区文件 |
| Day 3 | M0-4、M0-7 | CI 绿、锁文件策略 |
| Day 4 | M1-1 ~ M1-3 | 包目录、提示词与工具迁移 |
| Day 5 | M1-4 ~ M1-6 | 最小加载器、回归验证 |

第二周前 2–3 天：M1.5 前端单仓化（若想先看到发布形态，可与 M2 并行）。

## 5. 风险与缓冲

| 风险 | 影响 | 应对 |
|---|---|---|
| Docker 内 MCP stdio/编码问题 | M2 延期 | 先用本地 Python MCP 服务验证；npx 示例单独 profile |
| 模型供应商网络不通 | 首次体验失败 | 初始化向导支持自定义 Base URL；提供 mock 模式 |
| 前端双应用构建慢 | M2/M3 延期 | 合并为一个 Nginx 多站点；后续客户端收敛为挂件 |
| 解耦迁移打断 import | M1 延期 | 只移动路径不改逻辑，每步跑全量测试 |
| 范围膨胀 | 发布无限延后 | 严格冻结 M2 后功能；M6 清单只记录不插队 |

建议在总预估上留 30% 缓冲；若某里程碑超期两天，砍范围而不是砍验收标准。

## 6. 待决策项（不阻塞 M0/M1）

- [ ] 产品名
- [ ] 许可证最终确认（默认 Apache-2.0）
- [ ] 客户端是否收敛为可嵌入挂件
- [ ] 是否提供在线 Demo 环境
- [ ] 第二个包是否在 v0.1 就标注"预览"

## 7. 进度记录约定

- GitHub 标签：`m0`–`m5`、`blocker`、`good-first-issue`、`pack`、`deploy`
- 每个任务一个 issue，完成定义写进 issue 描述
- 每周复盘一次：完成项、卡点、是否需要砍范围
