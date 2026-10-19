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

http.interceptors.response.use(
  (resp) => {
    const body = resp.data as ApiResponse<unknown>
    if (body && typeof body.code === 'number' && body.code !== 0) {
      return Promise.reject(new Error(body.message || '请求失败'))
    }
    return resp
  },
  (err) => {
    if (err.response?.status === 401) {
      clearTokens()
      window.location.href = '/login'
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
