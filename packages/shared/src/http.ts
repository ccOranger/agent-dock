import axios, { type AxiosInstance } from 'axios'

import { createTokenStorage, type TokenStorage } from './token'
import type { ApiResponse } from './types'

/** 携带业务错误码的自定义错误，供上层按 code 做差异化处理（如 2004 引导注册） */
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

export interface HttpClientOptions {
  /** access token 的 localStorage key（两端隔离，如 cs_ / cs_admin_） */
  tokenKey: string
  /** refresh token 的 localStorage key */
  refreshKey: string
  /** 401 且刷新失败后跳转的登录页，默认 /login */
  loginPath?: string
  /** 业务接口前缀，默认 /api/v1 */
  baseURL?: string
  /** 刷新接口路径，默认 `${baseURL}/auth/refresh` */
  refreshPath?: string
  timeout?: number
}

export interface HttpClient {
  http: AxiosInstance
  tokens: TokenStorage
  getToken: () => string | null
  setTokens: (access: string, refresh: string) => void
  clearTokens: () => void
  unwrap: <T>(resp: Promise<{ data: { data: T } }>) => Promise<T>
  downloadBlob: (path: string, params?: Record<string, unknown>) => Promise<Blob>
}

// 刷新失败分两类：凭证无效才登出；网络中断/5xx 保留登录态，避免滚动发布时误踢
export const REFRESH_INVALID = 'refresh_invalid'
export const REFRESH_UNAVAILABLE = 'refresh_unavailable'

/** 把后端/axios 错误统一成可展示的 ApiError：业务码、422 校验、detail.message */
export function toApiError(err: any): ApiError {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail

  // BizError 以 {detail:{code,message}} 形式返回
  if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'code' in detail) {
    return new ApiError(detail.message || '请求失败', detail.code, status)
  }
  // 参数校验失败（422）：detail 是 pydantic 数组，转成 "字段: 文案"
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] ?? {}
    const field = Array.isArray(first.loc) ? first.loc.slice(1).join('.') : ''
    const msg = String(first.msg ?? '请求参数不合法')
    return new ApiError(field ? `${field}: ${msg}` : msg, undefined, status)
  }
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') {
    return new ApiError(detail.message || '请求失败', undefined, status)
  }
  if (typeof err?.response?.data?.message === 'string') {
    return new ApiError(err.response.data.message, undefined, status)
  }
  return new ApiError(err?.message || '请求失败', undefined, status)
}

/**
 * 创建一套独立的应用客户端：token key、登录页、刷新路径均可配置。
 * 内含 401 单飞刷新（并发 401 共用一次刷新）与统一的错误归一化。
 */
export function createHttpClient(options: HttpClientOptions): HttpClient {
  const {
    tokenKey,
    refreshKey,
    loginPath = '/login',
    baseURL = '/api/v1',
    timeout = 30000,
  } = options
  const refreshPath = options.refreshPath ?? `${baseURL}/auth/refresh`
  const tokens = createTokenStorage(tokenKey, refreshKey)

  const http = axios.create({ baseURL, timeout })

  http.interceptors.request.use((config) => {
    const token = tokens.getToken()
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })

  // ===== 401 单飞刷新：access token 过期时用 refresh token 换新并重放原请求 =====
  let refreshing: Promise<string> | null = null

  async function refreshAccessToken(): Promise<string> {
    const refresh = localStorage.getItem(refreshKey)
    if (!refresh) throw new Error(REFRESH_INVALID)
    // 用裸 axios 发请求：避免再次进入本实例的 401 拦截器造成递归
    const resp = await axios.post(refreshPath, { refresh_token: refresh }).catch((e: any) => {
      if (!e?.response || e.response.status >= 500) throw new Error(REFRESH_UNAVAILABLE)
      throw new Error(REFRESH_INVALID)
    })
    const data = resp.data?.data
    if (!data?.access_token || !data?.refresh_token) throw new Error(REFRESH_INVALID)
    tokens.setTokens(data.access_token, data.refresh_token)
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
    tokens.clearTokens()
    if (!window.location.pathname.startsWith(loginPath)) {
      window.location.href = loginPath
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
          return Promise.reject(toApiError(err))
        }
      }
      if (status === 401) redirectToLogin()
      return Promise.reject(toApiError(err))
    },
  )

  async function unwrap<T>(resp: Promise<{ data: { data: T } }>): Promise<T> {
    const r = await resp
    return r.data.data
  }

  // 带认证的下载（CSV 导出等）：返回 Blob
  async function downloadBlob(path: string, params?: Record<string, unknown>): Promise<Blob> {
    const resp = await http.get(path, { params, responseType: 'blob' })
    return resp.data as Blob
  }

  return {
    http,
    tokens,
    getToken: tokens.getToken,
    setTokens: tokens.setTokens,
    clearTokens: tokens.clearTokens,
    unwrap,
    downloadBlob,
  }
}
