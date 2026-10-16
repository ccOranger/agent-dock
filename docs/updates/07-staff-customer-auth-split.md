# U07 · 人员/客户分表与双入口鉴权

> 状态：已落地 ｜ 影响面：users 表拆分、登录/注册接口、RBAC 依赖、外键重指向

## 1. 背景与问题

初版单表 `users`（role ∈ customer/agent/admin）把后台人员与客户混在一起，客户可自助注册而后台账号需受控，二者数据边界与会话/记忆归属混浊。拆成两张物理隔离的表。

## 2. 表结构（models/entities.py、init_db.sql）

| 表 | 用途 | 角色 |
|----|------|------|
| `staff_users` | 后台人员（管理员/客服坐席） | `agent` \| `admin`（CHECK 约束） |
| `customer_users` | 客户账号，可自助注册 | 恒为 `customer`（实体上以类属性 `role="customer"` 暴露） |

外键重指向：
- `sessions.user_id → customer_users(id)`，`assigned_agent → staff_users(id)`；
- `documents.uploaded_by → staff_users(id)`；
- `memories.user_id → customer_users(id)`。

两表都带 `password_hash / display_name / metadata / is_active / created_at / updated_at`。

## 3. 鉴权接口（api/v1/auth.py）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/auth/staff/login` | 后台人员登录（admin-frontend）；失败写 `audit_logs.login_failed` |
| POST | `/auth/customer/login` | 客户登录（frontend）；账号不存在返回 `code=2004 AccountNotRegistered` 以引导注册 |
| POST | `/auth/customer/register` | 客户自助注册，成功直接下发 Token 自动登录 |
| POST | `/auth/refresh` | 按 refresh token 的 role 路由到对应表校验后重发 Token |
| GET | `/auth/me` | 当前用户信息 |

`_issue()` 生成 `access/refresh` 双 Token（`create_access_token/create_refresh_token(sub=user.id, role)`）+ `expires_in` + `UserInfo`。

## 4. RBAC 依赖（api/deps.py）

`current_user` 解 Bearer Token → 按 `role` 声明查对应仓储：

```python
if role == "customer":        user = await CustomerRepo.get_by_id(sub)
elif role in ("agent","admin"): user = await StaffRepo.get_by_id(sub)
# 再校验 is_active
```

派生依赖：
- `current_customer`：限定 `role==customer`（聊天/会话/我的记忆）；
- `require_staff = require_roles("agent","admin")`：后台读/操作类接口；
- `require_admin = require_roles("admin")`：Agent/MCP/知识库写操作、指标。

## 5. 权限矩阵（实况）

| 资源 | customer | agent | admin |
|------|----------|-------|-------|
| 自己的会话/历史 | RW | — | — |
| 跨客户咨询（inquiries） | — | R | R |
| 客户记忆 | 自己 R | RW | RW |
| 知识库文档 | — | R | RW |
| 工单 | — | RW（含状态流转） | RW |
| Agent / MCP 管理 | — | 只读列表 | RW |
| 指标 | — | — | R |

## 6. 错误码（core/exceptions.py）

统一 `BizError(HTTPException)`，`detail={code, message, data}`。相关新增/关键码：

| code | HTTP | 异常类 | 含义 |
|------|------|--------|------|
| 1001 | 400 | ParamError | 参数校验失败 |
| 2001 | 401 | Unauthorized | 未认证 |
| 2002 | 401 | TokenExpired | Token 过期 |
| 2003 | 403 | Forbidden | 无权限 |
| **2004** | 404 | **AccountNotRegistered** | 账号不存在，请先注册 |
| 3001 | 404 | NotFound | 资源不存在 |
| **3002** | 409 | **Conflict** | 资源冲突（重名/命名空间已存在等） |
| 4001 | 400 | ToolParamError | 工具参数校验失败 |
| 4003 | 400 | UnsafeInput | 输入含不安全内容 |
| 4029 | 429 | RateLimited | 请求过于频繁 |
| 5001–5006 | 5xx | Internal/LLM/RAG/Timeout/Memory/Agent | 见 [api.md](../api.md) |

## 7. 安全（core/security.py）

- JWT HS256，payload `{sub, role, exp, iat, jti}`；
- `detect_injection`（ignore previous/above instructions、system prompt 等）→ 命中 `UnsafeInput(4003)`；
- `sanitize_output` 对手机号/身份证/银行卡正则脱敏（ChatService 逐块 `sanitize_output`）。

## 8. 种子账号（scripts/seed_data.py，生产务必改密）

| 用户名 | 密码 | 角色 | 表 |
|--------|------|------|----|
| admin | Admin@123 | admin | staff_users |
| service01 | Agent@123 | agent | staff_users |
| alice | Alice@123 | customer | customer_users |

## 9. 验证

`tests/test_security.py`、`tests/test_admin_inquiries.py`（staff 跨客户访问）、`test_knowledge_bases.py`（admin vs staff 权限）。
