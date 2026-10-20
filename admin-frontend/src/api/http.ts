import axios from 'axios'
import type { ApiResponse } from '@/types'

const TOKEN_KEY = 'cs_admin_access_token'
const REFRESH_KEY = 'cs_admin_refresh_token'

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
      return Promise.reject(new Error(body.message || '请求失败'))
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
        return Promise.reject(toReadableError(err))
      }
    }
    if (status === 401) {
      redirectToLogin()
    }
    // 把后端错误转成人话：业务错走 detail.message，参数校验失败（422）走 pydantic 的
    // detail 数组。不转的话页面只能拿到 "Request failed with status code 422"，
    // 运维看不出是“关键词超出上限”还是“正文为空”。
    return Promise.reject(toReadableError(err))
  },
)

function toReadableError(err: any): Error {
  const detail = err?.response?.data?.detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] ?? {}
    // loc 形如 ["body", "keywords"]：去掉请求位置，只留字段名
    const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : ''
    const msg = String(first.msg ?? '请求参数不合法')
    return new Error(field ? `${field}: ${msg}` : msg)
  }
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return new Error(detail.message || '请求失败')
  }
  if (typeof err?.response?.data?.message === 'string') {
    return new Error(err.response.data.message)
  }
  return new Error(err?.message || '请求失败')
}

export async function unwrap<T>(resp: Promise<{ data: { data: T } }>): Promise<T> {
  const r = await resp
  return r.data.data
}

// 带认证的下载（CSV 导出等）：返回 Blob
export async function downloadBlob(path: string, params?: Record<string, unknown>): Promise<Blob> {
  const resp = await http.get(path, { params, responseType: 'blob' })
  return resp.data as Blob
}
