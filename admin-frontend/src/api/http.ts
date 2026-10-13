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
    return Promise.reject(err)
  },
)

export async function unwrap<T>(resp: Promise<{ data: { data: T } }>): Promise<T> {
  const r = await resp
  return r.data.data
}

// 带认证的下载（CSV 导出等）：返回 Blob
export async function downloadBlob(path: string, params?: Record<string, unknown>): Promise<Blob> {
  const resp = await http.get(path, { params, responseType: 'blob' })
  return resp.data as Blob
}
