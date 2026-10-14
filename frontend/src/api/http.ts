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

http.interceptors.response.use(
  (resp) => {
    const body = resp.data as ApiResponse<unknown>
    if (body && typeof body.code === 'number' && body.code !== 0) {
      return Promise.reject(new ApiError(body.message || '请求失败', body.code))
    }
    return resp
  },
  (err) => {
    const status = err.response?.status
    if (status === 401) {
      clearTokens()
      window.location.href = '/login'
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
