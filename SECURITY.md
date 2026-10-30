# 安全策略

## 支持的版本

| 版本 | 安全更新 |
|---|---|
| 0.2.x | 支持 |
| 0.1.x | 仅在 0.2.x 发布前提供修复 |
| < 0.1 | 不保证 |

## 报告漏洞

请不要在公开 issue 中披露安全漏洞。优先使用 GitHub 的私密漏洞报告：

1. 打开仓库的 **Security** 标签页
2. 选择 **Report a vulnerability**
3. 描述影响范围、复现步骤和可能的修复建议

我们会在确认后尽快回复，并在修复发布后公开致谢（如你愿意）。

## 重点关注范围

- 认证、越权与租户/角色隔离
- 提示词注入导致的工具越权或数据泄露
- SSE、日志、链路追踪中的敏感数据泄露
- 文档上传、解析、RAG 检索链路的注入与拒绝服务
- 依赖链与容器镜像漏洞

## 依赖漏洞与许可证

- **扫描方式**：CI 的 `audit` job 跑 `pip-audit -r backend/requirements.txt`、`npm audit --omit=dev`，
  并用 `scripts/license_report.py --check` 校验许可证白名单。该 job 目前是**观察模式**（`continue-on-error`），
  结论看 job 日志与 annotation。
- **许可证清单**：[docs/third-party-licenses.md](docs/third-party-licenses.md)，由 `scripts/license_report.py` 生成，勿手改。
- **已知问题**（截至 2026-10-29）：
  - 前端**开发依赖**在当前 `package-lock.json` 上有 6 项 `npm audit` 告警（2 critical / 1 high / 3 moderate），
    全部落在 `vitest` 2.1.9 → 5、`vite` 5.4.x → 8 的升级链上（`tinypool` / `esbuild` / `@vitest/mocker` / `vite-node`
    随主包一起动）；修复需要跨大版本升级，属专项任务。**生产依赖 0 项告警**，镜像产物不受影响。
  - 核心依赖 `hello-agents` 以 **CC BY-NC-SA 4.0**（含非商业条款）分发。本项目以库的形式调用、未修改其源码，
    并整体采用同一许可，因此**仅限非商业使用**；商用需先解决上游授权或替换该依赖。详见 [NOTICE](NOTICE) 与 [LICENSE](LICENSE)。

## 部署安全基线

- 生产环境必须设置 `APP_ENV=prod`，并使用 ≥32 字节的随机 `JWT_SECRET`
- 不要使用演示账号与默认口令；`SEED_DEMO` 默认关闭
- 仅在反向代理可信时配置 `TRUSTED_PROXIES`，否则不要采信 `X-Forwarded-For`
- 对外部署必须启用 HTTPS；限制管理端访问来源
- 定期轮换模型 API Key、数据库密码与 JWT 密钥
- 不要将 `.env`、`memory_data/`、`backend/data/` 提交到仓库或镜像
