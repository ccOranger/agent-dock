import { defineStore } from 'pinia'
import { ref, type Ref } from 'vue'

import type { TokenStorage } from './token'
import type { LoginResult } from './types'

export interface AuthApi<U> {
  login: (username: string, password: string) => Promise<LoginResult<U>>
  me: () => Promise<U>
  /** 仅客户侧支持自助注册 */
  register?: (username: string, password: string) => Promise<LoginResult<U>>
}

export interface AuthStoreOptions<U> {
  /** Pinia store id，默认 auth（两个应用独立运行，互不冲突） */
  id?: string
  tokens: TokenStorage
  api: AuthApi<U>
  /** 登录/取用户后的校验：抛错即拒绝（如后台只允许 agent/admin） */
  validateUser?: (user: U) => void
  /** 应用专属扩展（如后台的 isAdmin），由调用方自行提供 */
  extra?: (ctx: { user: Ref<U | null> }) => Record<string, unknown>
}

/**
 * 创建 auth store 工厂：登录/注册/取用户/登出与 token 生命周期只维护一份，
 * 两端差异通过 api、validateUser、extra 注入。
 */
export function createAuthStore<U>(options: AuthStoreOptions<U>) {
  const { id = 'auth', tokens, api, validateUser, extra } = options

  return defineStore(id, () => {
    const user = ref<U | null>(null) as Ref<U | null>
    const loggedIn = ref(!!tokens.getToken())

    async function login(username: string, password: string) {
      const result = await api.login(username, password)
      // 先校验再落 token：无权限的账号不留下登录态
      validateUser?.(result.user)
      tokens.setTokens(result.access_token, result.refresh_token)
      user.value = result.user
      loggedIn.value = true
    }

    async function register(username: string, password: string) {
      if (!api.register) throw new Error('当前应用不支持注册')
      // 注册成功后后端直接下发 Token，自动登录
      const result = await api.register(username, password)
      validateUser?.(result.user)
      tokens.setTokens(result.access_token, result.refresh_token)
      user.value = result.user
      loggedIn.value = true
    }

    async function fetchMe() {
      if (!tokens.getToken()) return
      const me = await api.me()
      try {
        validateUser?.(me)
      } catch (e) {
        tokens.clearTokens()
        user.value = null
        loggedIn.value = false
        throw e
      }
      user.value = me
      loggedIn.value = true
    }

    function logout() {
      tokens.clearTokens()
      user.value = null
      loggedIn.value = false
    }

    return {
      user,
      loggedIn,
      login,
      register,
      fetchMe,
      logout,
      ...(extra ? extra({ user }) : {}),
    }
  })
}
