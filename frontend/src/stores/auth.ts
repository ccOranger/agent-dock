import { createAuthStore, type User } from '@shared'

import { apiLogin, apiMe, apiRegister } from '@/api'
import { clearTokens, getToken, setTokens } from '@/api/http'

/** 客户侧 auth store：登录 / 注册 / 取用户 / 登出，token 生命周期由 @shared 统一维护 */
export const useAuthStore = createAuthStore<User>({
  tokens: { getToken, setTokens, clearTokens },
  api: { login: apiLogin, me: apiMe, register: apiRegister },
})
