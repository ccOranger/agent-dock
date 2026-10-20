import axios from 'axios'
import type { ApiResponse } from '@/types'

// 携带业务错误码的自定义错误，供上层按 code 做差异化处理（如 2004 引导注册）
export class ApiError extends Error {
  code?: number
  status?: number
  constructor(message: string, code?: number, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

const TOKEN_KEY = 'cs_access_token'
const REFRESH_KEY = 'cs_refresh_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem(TOKEN_KEY, access)
  localStorage.setItem(REFRESH_KEY, refresh)
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export const http = axios.create({
  baseURL: '/api/v1',
  timeout: 30000,
})

http.interceptors.request.use((config) => {
  const token = getToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// ===== 401 单飞刷新：access token 过期时用 refresh token 换新并重放原请求 =====
// 刷新失败分两类：凭证无效才登出；网络中断/5xx 保留登录态，避免滚动发布时误踢
const REFRESH_INVALID = 'refresh_invalid'
const REFRESH_UNAVAILABLE = 'refresh_unavailable'

let refreshing: Promise<string> | null = null

async function refreshAccessToken(): Promise<string> {
  const refresh = localStorage.getItem(REFRESH_KEY)
  if (!refresh) throw new Error(REFRESH_INVALID)
  // 用裸 axios 发请求：避免再次进入本实例的 401 拦截器造成递归
  const resp = await axios.post('/api/v1/auth/refresh', { refresh_token: refresh }).catch((e: any) => {
    if (!e?.response || e.response.status >= 500) throw new Error(REFRESH_UNAVAILABLE)
    throw new Error(REFRESH_INVALID)
  })
  const data = resp.data?.data
  if (!data?.access_token || !data?.refresh_token) throw new Error(REFRESH_INVALID)
  setTokens(data.access_token, data.refresh_token)
  return data.access_token as string
}

function getRefreshedToken(): Promise<string> {
  // 并发 401 共用同一个刷新请求，避免多次刷新相互覆盖
  if (!refreshing) {
    refreshing = refreshAccessToken().finally(() => {
      refreshing = null
    })
  }
  return refreshing
}

function redirectToLogin() {
  clearTokens()
  if (!window.location.pathname.startsWith('/login')) {
    window.location.href = '/login'
  }
}

http.interceptors.response.use(
  (resp) => {
    const body = resp.data as ApiResponse<unknown>
    if (body && typeof body.code === 'number' && body.code !== 0) {
      return Promise.reject(new ApiError(body.message || '请求失败', body.code))
    }
    return resp
  },
  async (err) => {
    const status = err.response?.status
    const original = err.config
    // 只对「非刷新请求、且未重试过」的 401 尝试续期；刷新失败再回登录页
    if (
      status === 401 &&
      original &&
      !original._retried &&
      !String(original.url || '').includes('/auth/refresh')
    ) {
      original._retried = true
      try {
        const token = await getRefreshedToken()
        original.headers = original.headers ?? {}
        original.headers.Authorization = `Bearer ${token}`
        return http(original)
      } catch (refreshErr: any) {
        // 暂时不可用（断网/超时/5xx）：保留 token，让用户稍后重试
        if (refreshErr?.message !== REFRESH_UNAVAILABLE) redirectToLogin()
        return Promise.reject(err)
      }
    }
    if (status === 401) {
      redirectToLogin()
    }
    // BizError 以 {detail:{code,message}} 形式返回，提取后统一抛 ApiError
    const detail = err.response?.data?.detail
    if (detail && typeof detail === 'object' && 'code' in detail) {
      return Promise.reject(new ApiError(detail.message || '请求失败', detail.code, status))
    }
    return Promise.reject(err)
  },
)

export async function unwrap<T>(resp: Promise<{ data: { data: T } }>): Promise<T> {
  const r = await resp
  return r.data.data
}
