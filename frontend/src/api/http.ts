import { createHttpClient } from '@shared'

/**
 * 客户侧 HTTP 客户端：token key 与后台（cs_admin_*）隔离。
 * 401 单飞刷新、业务错误归一化等逻辑来自 @shared。
 */
const env = import.meta.env as Record<string, string | undefined>
// 可配置为 /cs/api/v1 等子路径；默认 /api/v1
export const API_BASE = env.VITE_API_BASE || '/api/v1'

const client = createHttpClient({
  tokenKey: 'cs_access_token',
  refreshKey: 'cs_refresh_token',
  // 与 Vite base 对齐：部署在 /smart-cs/ 时 401 要跳 /smart-cs/login，
  // 用裸 '/login' 会跑到域名根，子路径部署下就是一个 404。
  loginPath: `${import.meta.env.BASE_URL.replace(/\/+$/, '')}/login`,
  baseURL: API_BASE,
})

export const http = client.http
export const getToken = client.getToken
export const setTokens = client.setTokens
export const clearTokens = client.clearTokens
export const unwrap = client.unwrap
