import { createHttpClient } from '@shared'

/**
 * 客户侧 HTTP 客户端：token key 与后台（cs_admin_*）隔离。
 * 401 单飞刷新、业务错误归一化等逻辑来自 @cs/shared。
 */
const client = createHttpClient({
  tokenKey: 'cs_access_token',
  refreshKey: 'cs_refresh_token',
  loginPath: '/login',
})

export const http = client.http
export const getToken = client.getToken
export const setTokens = client.setTokens
export const clearTokens = client.clearTokens
export const unwrap = client.unwrap
