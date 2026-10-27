/** 按应用隔离的 token 存储：两端各用一套 key，互不覆盖。 */

export interface TokenStorage {
  getToken: () => string | null
  setTokens: (access: string, refresh: string) => void
  clearTokens: () => void
}

export function createTokenStorage(tokenKey: string, refreshKey: string): TokenStorage {
  return {
    getToken: () => localStorage.getItem(tokenKey),
    setTokens: (access: string, refresh: string) => {
      localStorage.setItem(tokenKey, access)
      localStorage.setItem(refreshKey, refresh)
    },
    clearTokens: () => {
      localStorage.removeItem(tokenKey)
      localStorage.removeItem(refreshKey)
    },
  }
}
