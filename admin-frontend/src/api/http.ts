import { createHttpClient } from '@shared'

/**
 * 管理后台 HTTP 客户端：token key 与客户侧（cs_*）隔离。
 * 401 单飞刷新、422/业务错误归一化为可读文案的逻辑来自 @cs/shared。
 */
const client = createHttpClient({
  tokenKey: 'cs_admin_access_token',
  refreshKey: 'cs_admin_refresh_token',
  loginPath: '/login',
})

export const http = client.http
export const getToken = client.getToken
export const setTokens = client.setTokens
export const clearTokens = client.clearTokens
export const unwrap = client.unwrap

// 带认证的下载（CSV 导出等）：返回 Blob
export const downloadBlob = client.downloadBlob
