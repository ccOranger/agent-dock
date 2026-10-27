/** 两端共用的基础类型。业务专属类型仍留在各应用的 src/types 中。 */

export interface User {
  id: string
  username: string
  role: 'customer' | 'agent' | 'admin'
  display_name?: string | null
}

export interface LoginResult<U = User> {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: U
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}

export interface Paginated<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface MemoryItem {
  id: string
  memory_uid: string
  type: 'working' | 'episodic' | 'semantic' | 'perceptual'
  content: string
  importance: number
  access_count: number
  created_at: string
}
