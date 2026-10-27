# 开源发布检查清单（v0.1）

> 目标：把当前仓库整理成"陌生人 30 分钟内能跑起来"的开源项目。
> 关联文档：[产品定位](./positioning.md)、[开发清单](./development-checklist.md)。
> 现状基线：无 LICENSE / Dockerfile / docker-compose；`.env`、运行数据已被 gitignore；`seed_data.py` 含演示默认口令；README 仍是客服系统叙事。

## 0. 发布目标

| 项 | 目标 |
|---|---|
| 受众 | 能自己跑 Docker 的技术型团队 |
| 首次体验 | 克隆 → `cp .env.example .env` → `docker compose up` → 打开管理台 → 用演示数据对话 |
| 版本 | v0.1.0，明确标注"早期版本，接口可能变化" |
| 渠道 | GitHub（主）+ Gitee（镜像）+ 2–3 篇技术文章 |
| 成功标准 | 10 个真实部署、3 个外部 issue、1 篇社区转载 |

## 1. 法务与品牌

- [x] 许可证：核心 Apache-2.0（已添加 LICENSE/NOTICE）。
- [x] 增加 `LICENSE`、`NOTICE`（如有第三方声明）。
- [ ] 确认依赖许可证兼容（Python/Node 依赖清单扫描）。
- [x] 确定产品名：对外使用 **AgentDock**；仓库名与代码代号保留 `smart-cs`（Compose 项目名、包名不变，避免已有数据卷失联）。
- [ ] README 与页面中不出现真实客户名、内部系统名、真实域名。
- [x] 增加 `SECURITY.md`：漏洞报告方式、支持版本、响应时限（尽力而为）。

## 2. 仓库卫生

- [x] 删除临时文件 `.review_diff.txt`。
- [ ] 检查 git 历史是否提交过密钥：`git log -p -- backend/.env`、gitleaks/git-secrets 扫描。
- [ ] 确认 `.env`、`memory_data/`、`backend/data/doc_archives/*`、`backend/data/knowledge_bases/*` 均未入库（当前已 gitignore）。
- [x] `seed_data.py` 默认口令（`Admin@123` 等）仅用于 demo：
  - [x] 种子脚本显式要求 `SEED_DEMO=true`；
  - [x] 首次登录强制改密，或在初始化向导中生成随机密码并打印一次；
  - [x] README 显著提示"不要在生产使用演示口令"。
- [ ] `backend/app/tools/custom/mock_data.py` 的示例数据去标识化（姓名、手机号、订单号均为虚构）。
- [ ] 确认没有大文件/构建产物入库：`git count-objects -vH`。
- [x] 增加 `.gitattributes`（换行符）与统一编码说明（项目含中文，建议 UTF-8）。

## 3. 文档

- [x] 重写 `README.md`：
  - [ ] 一句话定位与截图/GIF（定位已完成，截图待 M3-4）
  - [x] 能力清单（路由、记忆、知识、工具、追踪）
  - [x] 架构图（后端、两个前端、Postgres/Qdrant/Redis/MCP）
  - [x] 3 分钟快速开始
  - [x] 业务扩展说明（代码 Agent / 自定义工具目录）
  - [x] 路线图与“明确不做什么”
  - [x] 许可证与商业支持入口
- [x] 新增 `docs/quickstart.md`（从零到对话成功，含常见报错）。
- [x] 新增 `docs/configuration.md`（`.env` 全量说明与默认值）。
- [x] 新增 `docs/deployment.md`（Docker、反向代理、HTTPS、SSE 缓冲关闭）。
- [x] 新增 `docs/faq.md`（模型接入、Qdrant、MCP stdio、Windows 编码）。
- [x] 新增 `CONTRIBUTING.md`（开发环境、测试命令、提交规范、业务 Agent/工具贡献方式）。
- [x] 新增 `docs/demo-script.md`（GIF 分镜台词、8 张截图取景清单与录制/压缩参数，台词取自已验证评测用例）。
- [ ] 新增 `docs/architecture.md`（路由、上下文工程、记忆生命周期、追踪模型；待 M4 前后补）。
- [ ] 保留 `docs/updates/*` 作为开发历史，不要求外部读者通读。

## 4. 一键部署

当前只有 Windows 启动脚本（`scripts/start_all.ps1` / `.bat`），不满足跨平台要求。

### 4.1 镜像

- [ ] 后端 `Dockerfile`：Python 3.12-slim，非 root 用户，先装依赖再拷代码，带 `/health` HEALTHCHECK。
- [ ] 前端 `Dockerfile`：多阶段构建管理端与聊天端两个产物，一个 Nginx 托管；`/` 聊天、`/admin/` 管理端，`/api` 反代后端。
- [ ] `.dockerignore`：排除 `node_modules`、`.venv`、`data/`、`memory_data/`、日志。

### 4.2 Compose

- [ ] `docker-compose.yml` 服务：`postgres`（pgvector 镜像）、`qdrant`、`redis`、`backend`、`web`（一个 Nginx 托管两个前端产物）。
- [ ] 数据卷：Postgres、Qdrant、上传归档、记忆文件；命名卷 + 备份说明。
- [ ] `depends_on` + healthcheck，避免后端先于数据库启动。
- [ ] `.env.example` 覆盖全部必填项（模型 Key、数据库、JWT、CORS、TRUSTED_PROXIES、上传上限、追踪开关）。
- [ ] 首次启动自动执行 `init_db.sql` 与可选种子（`SEED_DEMO=true`）。
- [ ] MCP 示例：stdio 服务需要 Python/Node 运行时，确认镜像内可用；`npx` 示例默认关闭或单独 profile，避免离线环境启动失败。
- [ ] 反向代理示例（Nginx）：`/` 聊天、`/admin/` 管理端、`/api` 后端；关闭 SSE 缓冲，透传 `X-Forwarded-For`，配置 `TRUSTED_PROXIES`。

### 4.3 升级

- [ ] 引入版本化迁移（Alembic 或 `scripts/migrations/NNN_*.sql` + `schema_version` 表）。
- [ ] 提供 `docker compose run backend python -m app.migrate` 或入口自动迁移开关。
- [ ] `CHANGELOG.md` 使用 Keep a Changelog 格式，标注破坏性变更。

## 5. 初始化向导

当前依赖 `scripts/seed_data.py` 手工建管理员与演示数据，开源用户容易卡住。

- [ ] 新增"首次运行"检测：不存在任何后台账号时进入设置流程。
- [ ] 提供两种方式（二选一即可）：
  - [ ] CLI：`python -m app.setup`（交互式创建管理员、写入 `.env`、测试模型连通性）。
  - [ ] Web：`/setup` 页面 + `POST /api/v1/setup`，仅在无管理员时可调用，成功后自动关闭。
- [ ] 模型配置测试：填写 Base URL / Key / Model 后发一次最小请求，失败给出明确错误。
- [ ] 演示数据开关：一键导入示例知识库、客服包、IT Helpdesk 包。
- [ ] 系统自检页：数据库、Qdrant、Redis、模型、MCP 连接状态。

## 6. 安全基线

- [x] 生产环境 JWT 密钥 ≥32 字节校验（已实现，确认文档同步）。
- [ ] 初始化向导自动生成随机 JWT 密钥，禁止示例值进入生产。
- [x] 默认管理员强制改密；演示账号默认禁用或随机密码。
- [ ] CORS 默认仅放行本地地址；文档说明生产配置。
- [ ] 上传大小限制、登录限流、SSE 错误脱敏已在代码中生效，补充文档说明。
- [ ] `TRUSTED_PROXIES` 说明：反向代理后必须配置，否则限流按直连 IP。
- [ ] 关闭或保护 `/docs` 的可选开关（生产环境）。
- [ ] 追踪 payload 开关（`TRACE_STORE_PAYLOAD`）默认值复核，避免无意存客户数据。
- [ ] 依赖漏洞扫描（`pip-audit`、`npm audit`），记录已知问题。

## 7. 质量与 CI

- [x] 后端：`python -m compileall`、`pytest`（本地 277 passed / 2 skipped；CI 275 passed / 4 skipped）。
- [x] 增加 ruff（0.17.1，已纳入 `requirements-dev` 与 CI）：lint + import 排序。
- [x] 前端：`npm run build` 两端均通过（当前通过，仅有 chunk 体积告警）。
- [x] GitHub Actions：
  - [x] backend job（Python 3.12，依赖缓存，pytest）
  - [x] frontend job（Node 20，两端 build）
  - [ ] docker job（构建镜像，不推送）
- [x] 固定版本与锁文件：`requirements.txt` 版本策略、仓库根 `package-lock.json`（npm workspaces）入库。
- [ ] 发布流程：打 tag、生成 Release Notes、附镜像构建说明。

## 8. 发布物料

- [x] 演示 GIF：四帧 13 秒（发问 + 工具回填 → 多意图 + 自动建单 → 跨轮补单 → 转人工），分镜台词见 [demo-script.md](demo-script.md)。
- [x] 8 张截图：链路详情、业务场景、知识库、Agent 编辑、用量报表、嵌入挂件、授权与版本，取景清单见 [demo-script.md](demo-script.md)。
- [ ] 技术文章三篇（差异化证明）：
  1. 多意图路由与提示词去具名化
  2. 记忆生命周期与跨轮上下文
  3. 一轮对话的全链路追踪模型
- [ ] Release Notes：v0.1 能力、已知限制、升级方式。
- [ ] 路线图 issue（置顶）：第二个业务场景、Docker 优化、评测、RBAC。

> 2026-10-20：首篇技术文章初稿已完成（[docs/articles/01-multi-intent-routing.md](articles/01-multi-intent-routing.md)），待外部发布；截图与 GIF 待演示数据就绪后补。
> 2026-10-27：演示脚本与取景清单已就位（[demo-script.md](demo-script.md)），台词取自 `customer_service` 已验证用例；待本机录屏后一次性入库。

## 9. 发布后运营

- [x] Issue 模板：bug / feature / deployment help；PR 模板含测试说明。
- [ ] Discussions 或群组入口，明确"尽力而为"的支持边界。
- [ ] 版本节奏：前三个月每 2–4 周一个 patch/minor。
- [ ] 收集部署反馈：至少记录部署失败原因 Top 5，作为下一版优先项。
- [ ] 不默认采集遥测；如需统计，必须显式 opt-in 并在文档说明。

## 10. v0.1 验收清单

全部勾选才可发布：

- [ ] 全新机器按 README 步骤 30 分钟内跑通对话。
- [ ] `docker compose up` 一条命令启动，无需手工建库。
- [ ] 初始化向导可创建管理员并完成模型连通性测试。
- [ ] 演示数据可一键导入，也可完全不导入。
- [ ] 客服包可停用，IT Helpdesk 包可启用（若第二个包未就绪，则明确标注"预览"）。
- [x] 无默认生产密钥、无演示口令直接暴露。
- [x] 两端构建通过，后端测试通过，CI 绿。
- [x] LICENSE、SECURITY、CONTRIBUTING 齐备。
- [x] README 与产品定位文档一致，不再以“智能客服系统”作为唯一身份。

## 11. v0.1 明确不做

- 多租户与租户隔离（只预留 `tenant_id`）。
- SSO、RBAC、计费、用量账单。
- 可视化 DAG 编排、插件市场、移动端。
- 托管 SaaS 与在线试用环境。
- 自动化升级（先提供手动迁移脚本）。
