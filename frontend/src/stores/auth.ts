import { defineStore } from 'pinia'
import { ref } from 'vue'
import { apiLogin, apiMe } from '@/api'
import { clearTokens, getToken, setTokens } from '@/api/http'
import type { User } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<User | null>(null)
  const loggedIn = ref(!!getToken())

  async function login(username: string, password: string) {
    const result = await apiLogin(username, password)
    setTokens(result.access_token, result.refresh_token)
    user.value = result.user
    loggedIn.value = true
  }

  async function fetchMe() {
    if (!getToken()) return
    user.value = await apiMe()
    loggedIn.value = true
  }

  function logout() {
    clearTokens()
    user.value = null
    loggedIn.value = false
  }

  return { user, loggedIn, login, fetchMe, logout }
})
