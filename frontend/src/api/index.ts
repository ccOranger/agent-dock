import { http, unwrap } from './http'
import type {
  AgentInfo,
  DocumentItem,
  LoginResult,
  MemoryItem,
  Message,
  SessionInfo,
  User,
} from '@/types'

// ===== Auth =====
export const apiLogin = (username: string, password: string) =>
  unwrap(
    http.post<never, { data: { data: LoginResult } }>('/auth/customer/login', { username, password }),
  )

export const apiRegister = (username: string, password: string) =>
  unwrap(
    http.post<never, { data: { data: LoginResult } }>('/auth/customer/register', {
      username,
      password,
    }),
  )

export const apiMe = () => unwrap(http.get<never, { data: { data: User } }>('/auth/me'))

// ===== Sessions =====
// 会话按 (场景, 客户) 隔离：在场景内打开聊天/历史时必须带上 scene_code，
// 缺省只能看到默认场景的会话（跨场景一律 404）。
export const apiListSessions = (page = 1, pageSize = 20, sceneCode?: string | null) =>
  unwrap(
    http.get<never, { data: { data: { items: SessionInfo[]; total: number } } }>('/sessions', {
      params: { page, page_size: pageSize, ...(sceneCode ? { scene_code: sceneCode } : {}) },
    }),
  )

export const apiSessionMessages = (
  sessionId: string,
  page = 1,
  pageSize = 50,
  sceneCode?: string | null,
) =>
  unwrap(
    http.get<never, { data: { data: { items: Message[] } } }>(`/sessions/${sessionId}/messages`, {
      params: { page, page_size: pageSize, ...(sceneCode ? { scene_code: sceneCode } : {}) },
    }),
  )

export const apiDeleteSession = (sessionId: string, sceneCode?: string | null) =>
  http.delete(`/sessions/${sessionId}`, {
    params: sceneCode ? { scene_code: sceneCode } : {},
  })

// 结束会话：后端触发该会话 working 记忆全量晋升为长期记忆（spec 8.6）
export const apiCloseSession = (sessionId: string, sceneCode?: string | null) =>
  http.patch(
    `/sessions/${sessionId}`,
    { status: 'closed' },
    { params: sceneCode ? { scene_code: sceneCode } : {} },
  )

// ===== Memory =====
export const apiMyMemories = (type?: string) =>
  unwrap(http.get<never, { data: { data: MemoryItem[] } }>('/memory/me', { params: { type } }))

export const apiUserMemories = (userId: string, type?: string) =>
  unwrap(
    http.get<never, { data: { data: MemoryItem[] } }>(`/memory/users/${userId}`, {
      params: { type },
    }),
  )

export const apiForgetMemory = (id: string) => http.delete(`/memory/${id}`)

// ===== Knowledge =====
export const apiListDocuments = (namespace?: string, page = 1, pageSize = 20) =>
  unwrap(
    http.get<never, { data: { data: { items: DocumentItem[]; total: number } } }>(
      '/knowledge/documents',
      { params: { namespace, page, page_size: pageSize } },
    ),
  )

export const apiUploadDocument = (file: File, namespace: string, title?: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('namespace', namespace)
  if (title) form.append('title', title)
  return unwrap(http.post<never, { data: { data: { id: string; status: string } } }>('/knowledge/documents', form))
}

export const apiDeleteDocument = (id: string) => http.delete(`/knowledge/documents/${id}`)

export const apiRagSearch = (namespace: string, query: string, topK = 5) =>
  unwrap(http.post<never, { data: { data: { results: unknown } } }>('/knowledge/search', {
    namespace,
    query,
    top_k: topK,
  }))

// ===== Admin =====
export const apiListAgents = () =>
  unwrap(http.get<never, { data: { data: AgentInfo[] } }>('/admin/agents'))

export const apiToggleAgent = (name: string, enabled: boolean) =>
  http.patch(`/admin/agents/${name}/toggle`, { enabled })
