-- 0010：RBAC 子系统——角色 / 人员-角色 / 角色-菜单读写授权
--
-- 菜单结构仍在前端（与 Vue 路由绑定），库里只存"角色↔模块key↔读写"授权映射。
-- 幂等：建表 IF NOT EXISTS、seed ON CONFLICT DO NOTHING、user_roles 回填可重复执行。
-- 一经应用，checksum 锁定，后续修正请新增下一个编号。

CREATE TABLE IF NOT EXISTS roles (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key             VARCHAR(32) UNIQUE NOT NULL,
    name            VARCHAR(64) NOT NULL,
    description     VARCHAR(255),
    is_system       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id         UUID NOT NULL REFERENCES staff_users(id) ON DELETE CASCADE,
    role_id         UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

CREATE TABLE IF NOT EXISTS role_permissions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id         UUID NOT NULL UNIQUE REFERENCES roles(id) ON DELETE CASCADE,
    permissions     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 内置角色（固定 UUID，与 init_db.sql 一致）
INSERT INTO roles (id, key, name, description, is_system) VALUES
    ('00000000-0000-0000-0000-0000000000a1', 'admin', '管理员',
     '超级管理员，默认拥有全部模块的读写权限', TRUE),
    ('00000000-0000-0000-0000-0000000000a2', 'agent', '客服坐席',
     '一线客服：接管/咨询/工单读写，知识与链路按需查看', TRUE)
ON CONFLICT (key) DO NOTHING;

-- 坐席默认授权（贴合历史 require_staff 可见范围）；admin 超级角色不落权限行
INSERT INTO role_permissions (role_id, permissions) VALUES
    ('00000000-0000-0000-0000-0000000000a2',
     '{"takeover":"write","inquiries":"write","tickets":"write","traces":"read","knowledge":"read","memory":"write"}'::jsonb)
ON CONFLICT (role_id) DO NOTHING;

-- 回填：把存量 staff_users.role 映射成 user_roles（一人一角色起步，权限取并集）
INSERT INTO user_roles (user_id, role_id)
SELECT u.id, r.id
FROM staff_users u
JOIN roles r ON r.key = u.role
ON CONFLICT (user_id, role_id) DO NOTHING;
