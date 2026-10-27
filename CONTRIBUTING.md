# 贡献指南

感谢参与 AgentDock（仓库代号 smart-cs）。提交前请先阅读本指南与 [安全策略](SECURITY.md)。

## 开发环境

### 后端

```powershell
cd backend
uv venv --python 3.12
uv pip install -r requirements-dev.txt --python .venv\Scripts\python.exe
copy .env.example .env
```

种子数据分两种模式：

```powershell
# 普通模式：不创建演示账号；管理员密码随机生成并只打印一次
python ..\scripts\seed_data.py

# 演示模式：写入 admin/Admin@123、service01、alice 与示例文档
$env:SEED_DEMO="true"; python ..\scripts\seed_data.py
```

非演示模式也可通过 `SEED_ADMIN_USERNAME` / `SEED_ADMIN_PASSWORD` 指定初始管理员。

### 前端

npm workspaces 单仓：锁文件与 `node_modules` 都在仓库根，一次安装覆盖两个前端与 `packages/shared`。

```powershell
# 仓库根目录
npm ci

# 启动（分别在 5173 / 5174）
npm run dev:chat
npm run dev:admin
```

## 常用命令

| 目的 | 命令 |
|---|---|
| 代码检查 | `cd backend; .\.venv\Scripts\ruff.exe check app tests` |
| 后端测试 | `cd backend; .\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest_tmp` |
| 客户端构建 | `npm run build -w frontend` |
| 管理端构建 | `npm run build -w admin-frontend` |

CI 会执行以上全部检查。提交前请在本地至少跑通 lint、pytest 与两端构建。

> Windows 上请保留 `--basetemp=.pytest_tmp`：pytest 默认会在 `%TEMP%\pytest-of-<user>` 建临时目录，
> 部分环境下 `os.scandir` 会被拒绝访问（`PermissionError: [WinError 5]`），表现为大量用例
> 在 fixture 阶段报 ERROR。CI 在 Linux 上跑，不受影响。

需要真实模型与嵌入模型的全栈集成测试（`tests/test_hello_agents_integration.py`）默认跳过，
单独验证时先配好 `.env`，再设 `RUN_HELLO_AGENTS_INTEGRATION=1` 运行该文件。

## 依赖与版本

- 运行时依赖在 `backend/requirements.txt`：普通依赖使用下限，存在兼容风险的依赖加上界并注释原因
- 开发与 CI 工具在 `backend/requirements-dev.txt`，ruff 等工具钉死版本，避免规则升级造成无关告警
- 前端使用 npm workspaces，锁文件统一为仓库根 `package-lock.json`，CI 使用 `npm ci`；升级依赖单独开 PR 并附构建结果

## 分支与提交

- `main` 始终保持可发布；功能、修复、文档分别使用 `feat/`、`fix/`、`docs/`、`chore/` 前缀分支
- 提交信息使用 Conventional Commits，例如 `feat: 支持业务场景切换`
- 一个 PR 只做一件事；包含数据库结构变更时，在描述中说明升级方式与回滚方式

## Pull Request 检查清单

- [ ] `ruff check app tests` 通过
- [ ] `pytest -q --basetemp=.pytest_tmp` 通过
- [ ] `frontend` 与 `admin-frontend` 构建通过
- [ ] 未提交 `.env`、密钥、运行数据与本地临时文件
- [ ] 新增/修改的行为有测试或明确的手工验证步骤
- [ ] 用户可见变化已更新文档

## 代码约定

- Python 3.11+，行宽 120，类型注解优先使用内置泛型与 `X | None`
- 业务 Agent 继承 `BaseBusinessAgent`；工具通过 `ToolRegistry` 注册
- 不要绕过 `RAGTool` 直接操作向量库；不要绕过 `MemoryTool` 直接读写记忆存储
- 异步代码不要引入同步阻塞调用；CPU/IO 重活放 `asyncio.to_thread`
- 前端沿用现有 Vue 3 + Pinia + Element Plus 结构，字段说明与时间格式遵循现有页面

## 数据库变更

当前开发阶段仍以 `scripts/rebuild_and_seed.py` 全量重建为主（会清空数据）。涉及线上兼容的变更，需要在 PR 中说明迁移 SQL，后续版本将接入 Alembic 版本化迁移。

## 业务代码边界

新增业务能力放入 `app/agents/business/`、`app/agents/prompts/` 或 `app/tools/custom/`，
不要把客服/工单等业务词汇写进平台内核；业务隔离与对外接入通过后台「业务场景」配置。
