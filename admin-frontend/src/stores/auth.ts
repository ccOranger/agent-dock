import { createAuthStore, type User } from '@shared'

import { apiLogin, apiMe } from '@/api'
import { clearTokens, getToken, setTokens } from '@/api/http'

const STAFF_ROLES = ['agent', 'admin']

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
