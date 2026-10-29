# U23 License 与版本分层（community / enterprise，历史）

> 状态：**已移除**（该能力上线后又整体下线，本文仅保留为历史记录；`SCHEMA_VERSION` 当时无变更）
> 关联：产品化计划 [3.3](../productization-plan.md)、决策 D2 / D5 / D6
>
> **后续变更（2026-10-29）：** 「授权与版本」功能已移除——后台 `/license` 页面与 `GET|POST /admin/license*` 接口、
> `app/core/license.py`（Ed25519 校验 / 功能位 / 启动日志）、`require_feature()`、
> `LICENSE_FILE` / `LICENSE_PUBLIC_KEY` / `DEPLOYMENT_ID` 三个环境变量、`rbac` 里的 `license` 菜单 key
> （模块数 17 → 16）与 `tests/test_license.py` 全部删除，产品不再区分社区版/企业版、不再有功能位概念。
> 下文的设计、状态矩阵、授权文件格式与配置项均为当时实现的历史快照，**当前代码中已无对应物**。

## 1. 背景与问题

Apache-2.0 单仓交付没有任何 edition 概念：既不能区分社区版与付费版，也无法防“云厂商把镜像转售成自己的产品”；
更没有到期驱动续费的手段。客户侧真正需要的不是“拿不到源码”，而是“数据不出内网 + 有明确的功能边界与合同期限”。

## 2. 设计要点

1. **离线授权文件**：`JSON 负载 + Ed25519 signature`，客户机房内校验，不联网、不回调。
   公钥由部署方写入 `LICENSE_PUBLIC_KEY`（Base64 裸 32 字节或 PEM）；私钥与签发脚本只在私有仓库。
2. **只降级不阻断**：任何校验失败（缺失 / 解析失败 / 签名不符 / 指纹不符 / 过期）都退回 `community`，
   对话与社区版功能完全不受影响。付费客户生产停摆的代价远大于防破解收益。
3. **指纹不用机器码**：容器 hostname 每次重启都会变，改用 `.env` 的 `DEPLOYMENT_ID`（初始化时生成一次，
   随数据卷长期存在），避免“重启即掉授权”。留空表示未绑定，跳过指纹校验（仅开发/演示）。
4. **开源仓库只有挂载点**：`has_feature()` / `require_feature()` 是开关，企业功能代码不在本仓库（D6）。
   校验逻辑公开不影响防护——删掉 `has_feature()` 也拿不到功能实现。
5. **不设数量上限**：授权不限制场景/接入方数量（决策 D2），上限统一由 `staff_users.quota` 管理，
   避免两套机制互相冲突；`contract_ref` 只做对账备注，`mode`（`on_prem` / `hosted`）当前只记录不做分支。

## 3. 关键实现（历史：以下文件与路由均已删除）

| 文件 | 职责 |
|---|---|
| `backend/app/core/license.py` | `verify_document`（纯函数校验）、`load_license` / `reload_license`（进程内缓存 + 手动重载）、`has_feature`、`license_status`、`log_startup_license`；`FEATURE_CATALOG` / `COMMUNITY_SCOPE` |
| `backend/app/api/deps.py` | `require_feature(feature)`：功能位依赖工厂，社区版 403/2003，错误文案带功能中文名 |
| `backend/app/api/v1/admin_license.py` | `GET /admin/license`、`POST /admin/license/reload`（均 require_admin） |
| `backend/app/main.py` | 启动时 `log_startup_license()`，在任何依赖探测之前输出一行版本与到期天数 |
| `backend/app/core/config.py` | `LICENSE_FILE` / `LICENSE_PUBLIC_KEY` / `DEPLOYMENT_ID` |
| `admin-frontend/src/stores/license.ts` | 授权状态单例：`can(feature)`、`reload()`、顶部横幅判定 |
| `admin-frontend/src/views/LicenseView.vue` | 授权页（`/license`）：版本/客户/到期/剩余天数、功能位表、社区版范围、文件与指纹、重新加载 |

状态矩阵（`LicenseStatus`）：

| 场景 | edition | valid | expired | configured | active_features |
|---|---|---|---|---|---|
| 没放授权文件（社区版默认） | community | false | false | false | 空 |
| 签名/指纹/版本/公钥有问题 | community | false | false | true | 空 |
| 已过期 | community | **true** | true | true | 空（`features` 仍保留“曾授权”） |
| 有效企业授权 | enterprise | true | false | true | 授权声明的功能位（过滤未知项） |
| 有效社区版授权（`edition=community`） | community | true | false | true | 空 |

## 4. 授权文件

```json
{
  "v": 1,
  "customer": "某某科技有限公司",
  "edition": "enterprise",
  "features": ["rbac", "audit", "white_label", "eval_pro", "sso"],
  "issued_at": "2026-11-01T00:00:00Z",
  "expires_at": "2027-11-01T00:00:00Z",
  "deployment_id": "dep_7f3a...",
  "contract_ref": "HT-2026-0117",
  "mode": "on_prem",
  "signature": "base64(ed25519(...))"
}
```

签名口径：去掉 `signature` 后 `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
的 UTF-8 字节，用 Ed25519 私钥签名后 Base64 编码。签发端与校验端必须逐字节一致，否则表现为“文件看着没问题但签名不符”。

## 5. 配置项（历史：三个变量已从 `core/config.py` 与 `.env.example` 移除）

| 变量 | 默认 | 说明 |
|---|---|---|
| `LICENSE_FILE` | `./license.json` | 授权文件路径；不存在属正常状态（社区版） |
| `LICENSE_PUBLIC_KEY` | 空 | 签发方公钥；为空则一律社区版并在原因里提示 |
| `DEPLOYMENT_ID` | 空 | 部署指纹；与授权里的 `deployment_id` 不一致即降级 |

`license.json` 已加入 `.gitignore`（含客户名与合同号，禁止入库）。到期前 30 天启动日志 WARN + 后台黄色横幅。

## 6. 验证方式（历史：`tests/test_license.py` 已随功能删除）

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_license.py -q --basetemp=.pytest_tmp
```

`test_license.py`（17 项）覆盖：无文件、有效授权、过期保留 `features`、篡改 `features` 破坏签名、缺 `signature`、
指纹不符、本机未配 `DEPLOYMENT_ID` 时跳过指纹、未知功能位过滤、未配公钥、`v=2` 不支持、换文件后 `reload` 生效、
`require_feature` 社区版 403 与授权后放行、接口形状与功能位目录、`agent` 越权 403。

本轮另做过一次**真实签发端到端**（临时 Ed25519 密钥对 + 临时目录，跑完即删）：

| 场景 | 实测结果 |
|---|---|
| 有效企业授权（3 个功能位） | `edition=enterprise valid=True days_left=364 active=['audit','rbac','white_label']` |
| 接口返回 | `edition_label=企业版`、`customer=示例科技有限公司`、`features` 排序后与授权一致 |
| 篡改 `features` 不重签 | `edition=community valid=False reason=签名校验失败：文件被修改或不是本产品签发的授权` |
| 过期 2 天 | `edition=community expired=True days_left=-3 reason=授权已于 … 到期` |
| 换成别的 `deployment_id` | `edition=community reason=授权绑定的是另一个 DEPLOYMENT_ID` |
| 删除授权文件 | `edition=community configured=False reason=未找到授权文件` |
| 只放文件不配公钥 | `edition=community reason=未配置 LICENSE_PUBLIC_KEY` |

同批验证：`ruff check app tests` 通过、`admin-frontend npm run build` 通过（产出 `LicenseView-*.js` / `.css`）。

## 7. 已知边界

- **不提供签发工具与私钥**：`license_issue.py`、密钥生成与续期清单在私有仓库；开源仓库只做校验与挂载点。
- **不做机器码硬绑、代码混淆、在线激活**：都会推高运维成本却不增实质安全（D6）。
- **企业功能页尚未实现（历史）**：当时 `require_feature()` 与后台授权页已就位，`audit` / `white_label` / `eval_pro` /
  `sso` 的实际页面在私有仓库；`rbac` 位预留给**资源级**（场景/知识库/工单）授权。
  现状：功能位概念已不存在，模块级的人员-角色-菜单读写矩阵在开源版落地（`app/core/rbac.py`、`0010_rbac.sql`、
  `/admin/rbac/*`，见 [U26](26-rbac-menu-matrix.md)），资源级授权未实现。
- **`eval_pro` 不限制评测能力（历史）**：当时评测集、CLI 与 Markdown 报告留在社区版（D1），功能位只管“管理与定时回归”。
- **时区**：`days_left` 按 UTC 整天差计算；跨时区部署的“到期日”以授权文件里的 UTC 时间为准。
