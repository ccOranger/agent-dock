import { createHttpClient } from '@shared'

/**
 * 管理后台 HTTP 客户端：token key 与客户侧（cs_*）隔离。
 * 401 单飞刷新、422/业务错误归一化为可读文案的逻辑来自 @shared。
 */
const env = import.meta.env as Record<string, string | undefined>
// 可配置为 /cs/api/v1 等子路径；默认 /api/v1
export const API_BASE = env.VITE_API_BASE || '/api/v1'

const client = createHttpClient({
  tokenKey: 'cs_admin_access_token',
  refreshKey: 'cs_admin_refresh_token',
  // 生产镜像部署在 /admin/ 下，登录页需带 base 前缀（开发时 BASE_URL 为 /）
  loginPath: `${import.meta.env.BASE_URL}login`,
  baseURL: API_BASE,
})

export const http = client.http
export const getToken = client.getToken
export const setTokens = client.setTokens
export const clearTokens = client.clearTokens
export const unwrap = client.unwrap

// 带认证的下载（CSV 导出等）：返回 Blob
export const downloadBlob = client.downloadBlob
