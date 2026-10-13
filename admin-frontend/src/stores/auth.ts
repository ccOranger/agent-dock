import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiLogin, apiMe } from '@/api'
import { clearTokens, getToken, setTokens } from '@/api/http'
import type { User } from '@/types'

const STAFF_ROLES = ['agent', 'admin']

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loggedIn = ref(!!getToken())

  async function login(username: string, password: string) {
    const result = await apiLogin(username, password)
    if (!STAFF_ROLES.includes(result.user.role)) {
      // 非后台人员（普通客户）禁止登录管理后台
      throw new Error('该账号无管理后台访问权限')
    }
    setTokens(result.access_token, result.refresh_token)
    user.value = result.user
    loggedIn.value = true
  }

  async function fetchMe() {
    if (!getToken()) return
    const me = await apiMe()
    if (!STAFF_ROLES.includes(me.role)) {
      clearTokens()
      loggedIn.value = false
      user.value = null
      throw new Error('该账号无管理后台访问权限')
    }
    user.value = me
    loggedIn.value = true
  }

  function logout() {
    clearTokens()
    user.value = null
    loggedIn.value = false
  }

  const isAdmin = () => user.value?.role === 'admin'

  return { user, loggedIn, login, fetchMe, logout, isAdmin }
})
