import { createAuthStore, type User } from '@shared'

import { apiLogin, apiMe } from '@/api'
import { clearTokens, getToken, setTokens } from '@/api/http'

const STAFF_ROLES = ['agent', 'admin']

// access 序：write 覆盖 read，与后端 core/rbac 保持一致
const ACCESS_RANK: Record<string, number> = { read: 1, write: 2 }

/**
 * 管理后台 auth store：在共享工厂上追加员工角色校验，
 * 非后台人员即使拿到合法 token 也不建立登录态。
 */
export const useAuthStore = createAuthStore<User>({
  tokens: { getToken, setTokens, clearTokens },
  api: { login: apiLogin, me: apiMe },
  validateUser: (user) => {
    if (!STAFF_ROLES.includes(user.role)) {
      // 非后台人员（普通客户）禁止登录管理后台
      throw new Error('该账号无管理后台访问权限')
    }
  },
  extra: ({ user }) => ({ isAdmin: () => user.value?.role === 'admin' }),
})

/**
 * RBAC 判定：是否拥有某菜单模块的访问等级（默认 read）。
 * 有效权限来自登录 / me 时后端下发的 user.permissions；admin 为超级角色恒为 true。
 * 单独导出为纯函数（而非 store 方法）以获得稳定类型，菜单显隐与路由守卫共用它。
 */
export function canAccess(
  user: User | null,
  key: string,
  access: 'read' | 'write' = 'read',
): boolean {
  if (!user) return false
  if (user.role === 'admin') return true
  const held = (user.permissions || {})[key]
  if (!held) return false
  return (ACCESS_RANK[held] || 0) >= (ACCESS_RANK[access] || 99)
}
