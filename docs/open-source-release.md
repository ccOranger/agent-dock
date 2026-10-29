# 开源发布检查清单（v0.2）

> 目标：把当前仓库整理成"陌生人 30 分钟内能跑起来"的开源项目。
> 关联文档：[产品定位](./positioning.md)、[开发清单](./development-checklist.md)。
> 最近一次实检：**2026-10-29**（版本号已统一为 **0.2.0**；本地 `release.py check` 0 失败；`ruff check app tests` 通过；
> `pytest -q --basetemp=.pytest_tmp` → 629 passed / 3 skipped；两端 `npm run build` 通过；CI 已加 `docker` / `audit` job；
> 依赖许可证已扫描并入库：[third-party-licenses.md](third-party-licenses.md)；66 个 Markdown 内链 0 坏链）。
> 勾选口径：`[x]` = 已在代码/仓库/CI 中验证；`[ ]` = 未做或不满足。

## 0. 发布目标

| 项 | 目标 |
|---|---|
| 受众 | 能自己跑 Docker 的技术型团队 |
| 首次体验 | 克隆 → `cp backend/.env.example .env` → `docker compose up` → 打开管理台 → 用演示数据对话 |
| 版本 | v0.2.0，明确标注"早期版本，接口可能变化" |
| 渠道 | GitHub（主）+ Gitee（镜像）+ 2–3 篇技术文章 |
| 成功标准 | 10 个真实部署、3 个外部 issue、1 篇社区转载 |

## 1. 当前阻塞项（发布前必须清零）

| # | 阻塞项 | 现状 |
|---|---|---|
| B1 | ~~默认分支与发布版本一致~~ **已清零** | 2026-10-29 已将 `main` 快进到发布提交（含 RBAC、数据分析、人工接管、截图与 GIF）；之前 `main` 落后 9 个提交 / 103 个文件 |
| B2 | **仓库仍为私有** | `https://github.com/ccOranger/smart-cs` 返回 404；转公开前需确认最终归属（个人号 / 组织）与仓库名，并同步 README 徽章、克隆地址与 issue 模板里的链接；**另需先定下 B4 的 `hello-agents` 许可处置**（CC BY-NC-SA 4.0 决定项目能不能商用） |
| B3 | ~~CI 缺镜像构建~~ **已补** | `ci.yml` 新增 `docker` job：backend / web 两个镜像 `push: false` 构建，分层缓存走 gha 且各用一个 scope；PR 上就能暴露 Dockerfile 与前端产物问题（待首次 Actions 运行确认） |
| B4 | **依赖许可证与漏洞扫描** | **已扫描；`hello-agents` 的 CC BY-NC-SA 4.0 经维护者决定暂不处理（按现状发布，风险由 NOTICE 声明）** | 2026-10-29 新增 `scripts/license_report.py` 与 [第三方许可证清单](third-party-licenses.md)：Python 188 个发行版 + Node 274 个包（生产 87 个），除 `hello-agents 0.2.9` 外全部为宽松许可，处置见 §2。CI 新增 `audit` job（`pip-audit` + `npm audit --omit=dev` + 许可证白名单，目前观察模式）。`npm audit --omit=dev` **0 告警**；全量 6 项均在 devDependencies（`vitest` 2→5 / `vite` 5→8 大版本），已记入 `SECURITY.md` |
| B5 | ~~`docs/architecture.md` 缺失~~ **已补** | 新增 [架构说明](architecture.md)：组件拓扑、启动关闭、一轮对话链路、场景路由、上下文工程、记忆生命周期、RAG、MCP、鉴权限流、追踪模型、数据层、前端、扩展点、明确边界；README 文档表已挂链，`release.py` 必备文件清单已纳入 |
| B6 | **Release 实体未发布** | tag 需重打为 `v0.2.0`（原 `v0.1.0` 指向旧提交，与当前内容不符）；CHANGELOG 已写，但 GitHub Release 正文、置顶路线图 issue、Discussions / 支持入口都还没有 |

> 建议顺序：B1 → B4 → 更新本文件 → B2（转公开）→ B6（发 Release）。转公开之后再做 B6 才有效。

## 2. 法务与品牌

- [x] 许可证：核心 Apache-2.0（`LICENSE`）。
- [x] `NOTICE`（第三方声明位）：列出许可证清单的生成方式与唯一需单独说明的依赖 `hello-agents`（CC BY-NC-SA 4.0）。
- [ ] 确认依赖许可证兼容（Python/Node 依赖清单扫描）：2026-10-29 已用 `scripts/license_report.py` 扫描（Python 188 个发行版、Node 274 个包 / 生产 87 个），清单见 [third-party-licenses.md](third-party-licenses.md)。**发现 1 项不兼容**：核心框架 `hello-agents 0.2.9` 为 **CC BY-NC-SA 4.0**（非商业 + 相同方式共享），与 Apache-2.0 项目直接冲突，已在 `NOTICE` 与 README 许可证段声明。
  - [ ] 待决（三选一）：① 向上游 [jjyaoao/HelloAgents](https://github.com/jjyaoao/HelloAgents) 确认能否改用宽松许可或取得商用授权；② 把该依赖换成自研编排层（涉及 40 处引用：`SimpleAgent` / `HelloAgentsLLM` / `ToolRegistry` / `MemoryTool` / `RAGTool` / `MCPTool`）；③ 保持依赖但把产品定位明确收窄为非商业用途。
    **2026-10-29 维护者决定：暂不处理** —— 按现行许可发布 v0.2.0，风险由 `NOTICE` 与 README 许可证段声明；转公开后若收到许可质疑，再回到本条按 ①/②/③ 处置。
- [x] 产品名：对外 **AgentDock**；仓库名与代码代号保留 `smart-cs`（Compose 项目名、包名不变，避免已有数据卷失联）。
- [x] 对外文案无真实客户名、内部系统名、真实域名：`docs/api.md`、`docs/configuration.md`、`docs/updates/23-license-edition.md` 里的客户名均为"某某科技有限公司"占位；截图抽查（嵌入挂件、数据分析页）无客户信息、无合同号、无真实域名。
  - [x] 截图复核：`06-admin-usage.png`、`08-admin-license.png` 对应页面（用量报表、授权与版本）已下线，旧图已删除；
        重拍为 `06-admin-analytics.png`（数据分析：7 天 789 次请求 + 趋势折线）与 `08-admin-roles.png`（角色与权限：3 个角色 + 质检专员权限矩阵），均为 1280×800。
- [x] `SECURITY.md`：漏洞报告方式、支持版本、响应时限（尽力而为）。
- [x] API 文档标题统一为 AgentDock（`main.py` 的 FastAPI `title`、`pyproject.toml` 的 `description` 不再出现旧名）。

## 3. 仓库卫生

- [x] 无临时文件入库（`.review_diff.txt` 已删除）。
- [x] 历史未提交过密钥：`git log --all` 中从未新增 `.env`（只有 `.env.example`）；全仓 `sk-*` / `AKIA*` / `ghp_*` 模式 0 命中；历史仅 27 个 commit。
- [x] `.env`、`memory_data/`、`backend/data/doc_archives/*`、`backend/data/knowledge_bases/*`、`reports/` 均在 `.gitignore` 且未被跟踪。
- [x] 演示口令受控：`seed_data.py` 要求 `SEED_DEMO=true`；非演示模式只建管理员且**随机密码只打印一次**；README 显著提示"切勿用于生产"。
- [x] `backend/app/tools/custom/mock_data.py` / `mock_assets.py` 为虚构数据（订单号 `SO2024xxxx`、地址为公共路段、客户 ID 为 `alice` / `u_1002`）。
- [x] 无大文件/构建产物入库：322 个跟踪文件 / 5.67 MiB，`node_modules`、`dist`、`.venv`、`reports` 均未入库。
- [x] `.gitattributes`（`* text=auto`，Windows 脚本 CRLF / Shell LF）+ UTF-8 编码约定。

## 4. 文档

- [x] `README.md` 重写：一句话定位、能力清单、Mermaid 架构、Compose 快速开始、业务扩展方式、生产部署、路线图、"明确不做什么"、许可证与社区入口。
- [x] 首屏物料：四帧 13 秒演示 GIF + 8 张运营台截图（取景与台词见 [demo-script.md](demo-script.md)）。
- [x] `docs/quickstart.md`（从零到对话成功，含常见报错）。
- [x] `docs/configuration.md`（`.env` 全量说明与默认值）。
- [x] `docs/deployment.md`（Docker、反向代理、HTTPS、SSE 缓冲关闭、离线部署）。
- [x] `docs/faq.md`（模型接入、Qdrant、MCP stdio、Windows 编码）。
- [x] `CONTRIBUTING.md`（开发环境、测试命令、提交规范、业务 Agent/工具贡献方式；Windows 已注明 `--basetemp=.pytest_tmp`）。
- [x] `docs/api.md`、`docs/runbook.md`、`docs/evaluation.md`、`docs/multi-scene-development.md`。
- [x] `docs/architecture.md`（组件拓扑、对话链路、上下文工程、记忆生命周期、追踪模型、扩展点）。
- [x] `docs/updates/*` 保留为开发历史，不要求外部读者通读。

## 5. 一键部署

### 5.1 镜像

- [x] 后端 `docker/backend.Dockerfile`：Python 3.12-slim，非 root（appuser 1000），依赖层缓存，`/health` HEALTHCHECK，镜像内含 MCP stdio 所需运行时。
- [x] 前端 `docker/web.Dockerfile`：多阶段构建管理端与聊天端两个产物，一个 Nginx 托管；`/` 聊天、`/admin/` 管理端，`/api` 反代后端。
- [x] `.dockerignore`：排除 `node_modules`、`.venv`、`data/`、`memory_data/`、日志。

### 5.2 Compose

- [x] `docker-compose.yml`：`postgres`（pgvector）、`qdrant`、`redis`、`backend`、`web`。
- [x] 命名卷 + healthcheck + `depends_on`，五服务均达 healthy。
- [x] `.env.example` 覆盖必填项（模型 Key、Embedding、数据库、JWT、CORS、`TRUSTED_PROXIES`、上传上限、追踪开关、`API_DOCS_ENABLED`）。
- [x] 首启自动执行 `init_db.sql`；`SEED_DEMO=true` 时导入演示数据，均为幂等。
- [x] `npx` 型 MCP 示例只在 `SEED_DEMO=true` 时写入，离线环境不会因此启动失败。
- [x] 反向代理样例 `docker/nginx.conf`：SSE 关闭缓冲、`X-Forwarded-For`、`TRUSTED_PROXIES` 说明、25m 上传上限；`/metrics` 不对外暴露。

### 5.3 升级

- [x] 版本化迁移：`scripts/migrations/NNNN_*.sql` + `schema_migrations`，`python -m app.migrate`（`--check` / `--dry-run` / `--backup`）。
- [x] 入口自动迁移开关 `MIGRATE_ON_START`（默认 false）+ `scripts/verify_schema.py` 校验。
- [x] `CHANGELOG.md` 遵循 Keep a Changelog，破坏性变更用 ⚠️ 标注。

## 6. 初始化向导

- [x] CLI：`python -m app.setup`（建表、建管理员、生成随机 JWT 并写回 `.env`、测试模型连通性）。
- [x] 演示数据开关：`SEED_DEMO=true` 一键导入示例知识库、场景与动态 Agent。
- [x] 启动日志自检：数据库 / Qdrant / Redis / 模型状态单行输出，失败给可操作提示。
- [ ] **系统自检页**：管理台里看不到实时依赖状态（当前只有启动日志 + `/health`）。
- [x] 不做 Web `/setup`：计划里是"CLI / Web 二选一"，已由 CLI 满足。

## 7. 安全基线

- [x] 生产环境 JWT 密钥 ≥32 字节校验（`validate_runtime_config`）。
- [x] 初始化向导自动生成随机 JWT，示例值无法进入生产（弱密钥直接拒绝启动）。
- [x] 演示账号默认关闭（`SEED_DEMO=false`），非演示模式管理员密码随机且只打印一次。
- [x] CORS 默认仅放行本地开发地址，生产按 `ALLOWED_ORIGINS` + 场景 `allowed_origins` 收紧。
- [x] 上传大小限制、登录限流、SSE 错误脱敏已在代码生效，且 `docs/configuration.md` / `docs/deployment.md` / `SECURITY.md` 有说明。
- [x] `TRUSTED_PROXIES` 说明：反向代理后必须配置，否则限流按直连 IP。
- [x] `/docs`、`/redoc`、`/openapi.json` 可用 `API_DOCS_ENABLED` 关闭；生产叠加 `docker-compose.prod.yml` 默认 `false`。
- [x] `TRACE_STORE_PAYLOAD` 默认值改为 `false`（开源默认不把客户工具入参落库，排障时显式打开）。
- [x] 依赖漏洞扫描并入 CI：`.github/workflows/ci.yml` 的 `audit` job 跑 `pip-audit -r backend/requirements.txt`、`npm audit --omit=dev` 与许可证白名单校验（目前 `continue-on-error` 观察模式）；已知问题记入 `SECURITY.md`「依赖漏洞与许可证」。

## 8. 质量与 CI

- [x] 后端：`ruff check app tests` + `pytest`（本地 629 passed / 3 skipped；CI Linux 上跳过 Windows 专用用例）。
- [x] 前端：两端 `npm run build` 通过（仅有 chunk 体积告警）。
- [x] GitHub Actions：backend job（Python 3.12，ruff + pytest）、frontend job（Node 20，矩阵构建两端）、docker job（backend / web 两个镜像 `push: false`，待首次运行确认）。
- [x] 版本与锁文件：`requirements.txt` 版本策略、仓库根 `package-lock.json`（npm workspaces）入库；`python scripts/release.py check` 校验六处版本一致。
- [x] 发布流程脚本：`scripts/release.py {check,bundle,tag}`（离线 bundle + 校验和 + 打 tag）。
- [ ] Release Notes 正式发布：正文已备（[release-notes/v0.2.0.md](release-notes/v0.2.0.md)，含定位、本版内容、镜像构建、升级与已知限制，可直接粘贴），GitHub 侧尚未发布。

## 9. 发布物料

- [x] 演示 GIF：四帧 13 秒（发问 + 工具回填 → 多意图 + 自动建单 → 跨轮补单 → 转人工），分镜见 [demo-script.md](demo-script.md)。
- [x] 8 张截图：链路详情、业务场景、知识库、Agent 编辑、数据分析、嵌入挂件、角色与权限（均 1280×800 PNG，单张 < 300KB）。
- [x] 技术文章四篇（4/4）：
  1. [x] 多意图路由与提示词去具名化 —— [articles/01-multi-intent-routing.md](articles/01-multi-intent-routing.md)
  2. [x] 记忆生命周期与跨轮上下文 —— [articles/02-memory-lifecycle.md](articles/02-memory-lifecycle.md)
  3. [x] 权限矩阵与会话状态机（RBAC + 人工接管）—— [articles/03-rbac-and-takeover.md](articles/03-rbac-and-takeover.md)
  4. [x] 一轮对话的全链路追踪模型 —— [articles/04-request-tracing.md](articles/04-request-tracing.md)
- [ ] 路线图 issue（置顶）：第二个业务场景、Docker 优化、评测回归、资源级授权与 SSO。
- [ ] 发布公告：技术社区 1–2 处，附 GIF 与部署入口。

## 10. 发布后运营

- [x] Issue 模板：bug / feature / deployment help；PR 模板含测试说明。
- [ ] Discussions 或群组入口，明确"尽力而为"的支持边界。
- [ ] 版本节奏：前三个月每 2–4 周一个 patch/minor。
- [ ] 收集部署反馈：至少记录部署失败原因 Top 5，作为下一版优先项。
- [ ] 不默认采集遥测；如需统计，必须显式 opt-in 并在文档说明。

## 11. v0.2 验收清单

全部勾选才可发布：

- [ ] **默认分支可克隆即用**（B1 清零）。
- [x] 全新机器按 README 步骤 30 分钟内跑通对话（M2-8 演练记录：冷构建 + 启动 + 首条回复合计 <10 分钟）。
- [x] `docker compose up` 一条命令启动，无需手工建库。
- [x] 初始化可创建管理员并完成模型连通性测试（`python -m app.setup`）。
- [x] 演示数据可一键导入，也可完全不导入（`SEED_DEMO`）。
- [x] 默认场景与业务场景可切换：停用客服场景后系统仍可用，IT Helpdesk 场景可独立启用（场景包机制已移除，改为后台「业务场景」配置）。
- [x] 无默认生产密钥、无演示口令直接暴露。
- [x] 两端构建通过，后端测试通过，CI 绿。
- [x] LICENSE、SECURITY、CONTRIBUTING 齐备。
- [x] README 与产品定位文档一致，不再以"智能客服系统"作为唯一身份。

## 12. v0.2 明确不做

- 多租户与租户隔离（只预留 `tenant_id`）。
- SSO、计费、用量账单（RBAC 已落地：人员-角色-菜单矩阵，见 `app/core/rbac.py`）。
- 可视化 DAG 编排、插件市场、移动端。
- 托管 SaaS 与在线试用环境。
- 自动化升级（只提供手动迁移脚本）。
- 内置 mock 模型模式（对话与向量必须连 OpenAI 兼容端点）。

## 13. 维护者发布动作（按顺序执行）

```bash
# 1. 默认分支已对齐（B1 已清零，2026-10-29）：main == release 发布提交，日常提交直接进 main
git checkout main && git pull --ff-only

# 2. 发布前全量校验（B3/B4 补完后再跑一次）
cd backend && ruff check app tests && pytest -q --basetemp=.pytest_tmp
cd .. && python scripts/release.py check
backend/.venv/bin/python scripts/license_report.py --check   # 依赖变动后先重跑清单，再校验白名单

# 3. 打 tag 并推送（已打过则跳过；改版本时用 tag --push）
python scripts/release.py tag --push

# 4. 生成离线交付包（可选，内网客户用）
python scripts/release.py bundle
```

GitHub 网页端（本地无法代替）：Settings → 转公开 / 转移归属；Actions 勾选 docker job 首次运行；
Releases 用 [release-notes/v0.2.0.md](release-notes/v0.2.0.md) 作正文（依据 CHANGELOG 的 `[0.2.0]` 段落编写）；新建并置顶路线图 issue；开启 Discussions。
