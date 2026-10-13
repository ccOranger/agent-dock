import { downloadBlob, http, unwrap } from './http'
import type {
  AgentInfo,
  ChunkList,
  DynamicAgentPayload,
  DocumentItem,
  InquiryDetail,
  InquirySession,
  KnowledgeBase,
  LoginResult,
  MCPServer,
  MCPServerPayload,
  MemoryItem,
  Paginated,
  SearchResult,
  User,
} from '@/types'

// ===== Auth =====
export const apiLogin = (username: string, password: string) =>
  unwrap(http.post<never, { data: { data: LoginResult } }>('/auth/login', { username, password }))

export const apiMe = () => unwrap(http.get<never, { data: { data: User } }>('/auth/me'))

// ===== 咨询管理 =====
export interface InquiryFilters {
  user_id?: string
  status?: string
  last_route_to?: string
  date_from?: string
  date_to?: string
  page?: number
  page_size?: number
}

export const apiListInquiries = (filters: InquiryFilters = {}) =>
  unwrap(
    http.get<never, { data: { data: Paginated<InquirySession> } }>('/admin/inquiries/sessions', {
      params: filters,
    }),
  )

export const apiInquiryDetail = (sessionId: string) =>
  unwrap(http.get<never, { data: { data: InquiryDetail } }>(`/admin/inquiries/sessions/${sessionId}`))

// CSV 导出（Blob 下载）
export const apiExportInquiries = (filters: Omit<InquiryFilters, 'page' | 'page_size'> = {}) =>
  downloadBlob('/admin/inquiries/sessions/export', filters as Record<string, unknown>)

// ===== 知识库：命名空间 =====
export const apiListBases = () =>
  unwrap(http.get<never, { data: { data: KnowledgeBase[] } }>('/knowledge/bases'))

export const apiCreateBase = (body: { namespace: string; display_name: string; description?: string }) =>
  unwrap(http.post<never, { data: { data: KnowledgeBase } }>('/knowledge/bases', body))

export const apiUpdateBase = (
  namespace: string,
  body: { display_name?: string; description?: string; is_active?: boolean },
) => unwrap(http.patch<never, { data: { data: KnowledgeBase } }>(`/knowledge/bases/${namespace}`, body))

export const apiDeleteBase = (namespace: string) => http.delete(`/knowledge/bases/${namespace}`)

// ===== 知识库：文档 =====
export const apiListDocuments = (
  namespace?: string,
  page = 1,
  pageSize = 20,
  extra: { status?: string; kind?: string } = {},
) =>
  unwrap(
    http.get<never, { data: { data: Paginated<DocumentItem> } }>('/knowledge/documents', {
      params: { namespace, page, page_size: pageSize, ...extra },
    }),
  )

export const apiUploadDocument = (file: File, namespace: string, title?: string) => {
  const form = new FormData()
  form.append('file', file)
  form.append('namespace', namespace)
  if (title) form.append('title', title)
  return unwrap(http.post<never, { data: { data: { id: string; status: string } } }>('/knowledge/documents', form))
}

export const apiDeleteDocument = (id: string) => http.delete(`/knowledge/documents/${id}`)

export const apiReindexDocument = (id: string) => http.post(`/knowledge/documents/${id}/reindex`)

export const apiDocChunks = (id: string, limit = 50, offset?: string) =>
  unwrap(http.get<never, { data: { data: ChunkList } }>(`/knowledge/documents/${id}/chunks`, {
    params: { limit, offset },
  }))

// 下载/查看上传文档的原始文件本体（Blob）
export const apiDocRaw = (id: string) => downloadBlob(`/knowledge/documents/${id}/raw`)

// ===== 知识库：手工条目 =====
export const apiCreateEntry = (body: { namespace: string; title: string; content: string }) =>
  unwrap(http.post<never, { data: { data: { id: string; status: string; chunk_count: number } } }>('/knowledge/entries', body))

export const apiUpdateEntry = (id: string, body: { title?: string; content: string }) =>
  unwrap(http.put<never, { data: { data: { id: string; status: string; chunk_count: number } } }>(`/knowledge/entries/${id}`, body))

// ===== 知识库：检索测试 =====
export async function apiRagSearch(
  namespace: string,
  query: string,
  topK = 5,
  opts: { advanced?: boolean; min_score?: number | null } = {},
): Promise<SearchResult[]> {
  const data = await unwrap(http.post<never, { data: { data: { results: SearchResult[] } } }>('/knowledge/search', {
    namespace,
    query,
    top_k: topK,
    advanced: opts.advanced ?? true,
    min_score: opts.min_score ?? null,
  }))
  return data.results
}

// ===== Admin：Agent =====
export const apiListAgents = () =>
  unwrap(http.get<never, { data: { data: AgentInfo[] } }>('/admin/agents'))

export const apiToggleAgent = (name: string, enabled: boolean) =>
  http.patch(`/admin/agents/${name}/toggle`, { enabled })

export const apiCreateAgent = (body: DynamicAgentPayload) =>
  unwrap(http.post<never, { data: { data: { name: string } } }>('/admin/agents', body))

export const apiUpdateAgent = (name: string, body: Partial<DynamicAgentPayload>) =>
  unwrap(http.put<never, { data: { data: { name: string } } }>(`/admin/agents/${name}`, body))

export const apiDeleteAgent = (name: string) => http.delete(`/admin/agents/${name}`)

// ===== Admin：MCP 服务 =====
export const apiListMcpServers = () =>
  unwrap(http.get<never, { data: { data: MCPServer[] } }>('/admin/mcp-servers'))

export const apiCreateMcpServer = (body: MCPServerPayload) =>
  unwrap(http.post<never, { data: { data: { id: string; name: string } } }>('/admin/mcp-servers', body))

export const apiUpdateMcpServer = (id: string, body: Partial<MCPServerPayload>) =>
  unwrap(http.put<never, { data: { data: { id: string; name: string } } }>(`/admin/mcp-servers/${id}`, body))

export const apiDeleteMcpServer = (id: string) => http.delete(`/admin/mcp-servers/${id}`)

export const apiTestMcpServer = (body: MCPServerPayload) =>
  unwrap(
    http.post<never, { data: { data: { ok: boolean; tool_count?: number; tools?: unknown[]; error?: string } } }>(
      '/admin/mcp-servers/test',
      body,
    ),
  )

// ===== Admin：Memory =====
export const apiMyMemories = (type?: string) =>
  unwrap(http.get<never, { data: { data: MemoryItem[] } }>('/memory/me', { params: { type } }))

export const apiUserMemories = (userId: string, type?: string) =>
  unwrap(http.get<never, { data: { data: MemoryItem[] } }>(`/memory/users/${userId}`, { params: { type } }))
