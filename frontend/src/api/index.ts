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
  unwrap(http.post<never, { data: { data: LoginResult } }>('/auth/login', { username, password }))

export const apiMe = () => unwrap(http.get<never, { data: { data: User } }>('/auth/me'))

// ===== Sessions =====
export const apiListSessions = (page = 1, pageSize = 20) =>
  unwrap(
    http.get<never, { data: { data: { items: SessionInfo[]; total: number } } }>('/sessions', {
      params: { page, page_size: pageSize },
    }),
  )

export const apiSessionMessages = (sessionId: string, page = 1, pageSize = 50) =>
  unwrap(
    http.get<never, { data: { data: { items: Message[] } } }>(`/sessions/${sessionId}/messages`, {
      params: { page, page_size: pageSize },
    }),
  )

export const apiDeleteSession = (sessionId: string) => http.delete(`/sessions/${sessionId}`)

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
