import { downloadBlob, http, unwrap } from './http'
import type {
  AgentInfo,
  CannedMatchResult,
  CannedScript,
  CannedScriptPayload,
  ChunkList,
  CustomToolSpec,
  DynamicAgentPayload,
  DocumentItem,
  InquiryDetail,
  InquirySession,
  KnowledgeBase,
  LoginResult,
  MCPServer,
  MCPServerPayload,
  MCPServerToolsResult,
  MemoryItem,
  Paginated,
  RequestTraceDetail,
  RequestTraceSummary,
  RouterAgentInfo,
  RouterPromptPreview,
  RouterSceneDetail,
  RouterScenePrompt,
  RouterSceneStats,
  RouterSceneSummary,
  RouterSceneTestResult,
  Scene,
  SceneAgentOption,
  SceneBoundAgent,
  SceneCreatePayload,
  SceneOption,
  SceneRotateKeyResult,
  SceneUpdatePayload,
  SceneWidgetPayload,
  SearchResult,
  TicketDetail,
  TicketSummary,
  TraceUsageItem,
  User,
} from '@/types'

// ===== Auth =====
export const apiLogin = (username: string, password: string) =>
  unwrap(
    http.post<never, { data: { data: LoginResult } }>('/auth/staff/login', { username, password }),
  )

export const apiMe = () => unwrap(http.get<never, { data: { data: User } }>('/auth/me'))

// ===== 咨询管理 =====
export interface InquiryFilters {
  user_id?: string
  status?: string
  last_route_to?: string
  scene_code?: string
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

// ===== 工单管理 =====
export interface TicketFilters {
  user_id?: string
  status?: string
  category?: string
  urgency?: string
  keyword?: string
  date_from?: string
  date_to?: string
}

export const apiListTickets = (filters: TicketFilters = {}, page = 1, pageSize = 20) =>
  unwrap(
    http.get<never, { data: { data: Paginated<TicketSummary> } }>('/admin/tickets', {
      params: { ...filters, page, page_size: pageSize },
    }),
  )

export const apiTicketDetail = (ticketNo: string) =>
  unwrap(http.get<never, { data: { data: TicketDetail } }>(`/admin/tickets/${ticketNo}`))

export const apiUpdateTicketStatus = (ticketNo: string, status: string, note?: string) =>
  unwrap(
    http.patch<never, { data: { data: TicketSummary } }>(`/admin/tickets/${ticketNo}/status`, {
      status,
      note,
    }),
  )

export const apiAddTicketNote = (ticketNo: string, content: string) =>
  unwrap(http.post<never, { data: { data: TicketSummary } }>(`/admin/tickets/${ticketNo}/notes`, { content }))

// ===== 请求链路追踪 =====
export interface TraceFilters {
  session_id?: string
  user_id?: string
  scene_code?: string
  agent_name?: string
  tool_name?: string
  mcp_server?: string
  outcome?: string
  has_error?: boolean
  min_latency_ms?: number
  date_from?: string
  date_to?: string
}

export const apiListTraces = (filters: TraceFilters = {}, page = 1, pageSize = 20) =>
  unwrap(
    http.get<never, { data: { data: Paginated<RequestTraceSummary> } }>('/admin/traces', {
      params: { ...filters, page, page_size: pageSize },
    }),
  )

export const apiTraceDetail = (traceRef: string) =>
  unwrap(http.get<never, { data: { data: RequestTraceDetail } }>(`/admin/traces/${traceRef}`))

export const apiSessionTraces = (sessionId: string) =>
  unwrap(http.get<never, { data: { data: RequestTraceSummary[] } }>(`/admin/traces/sessions/${sessionId}`))

export type TraceUsageField = 'tool_names' | 'agent_names' | 'mcp_server_names'

export const apiTraceUsage = (field: TraceUsageField = 'tool_names', days = 7) =>
  unwrap(http.get<never, { data: { data: TraceUsageItem[] } }>('/admin/traces/usage', { params: { field, days } }))

// ===== 知识库：命名空间 =====
export const apiListBases = () =>
  unwrap(http.get<never, { data: { data: KnowledgeBase[] } }>('/knowledge/bases'))

export const apiCreateBase = (body: {
  namespace: string
  display_name: string
  description?: string
}) => unwrap(http.post<never, { data: { data: KnowledgeBase } }>('/knowledge/bases', body))

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

export const apiGetAgentDetail = (name: string) =>
  unwrap(http.get<never, { data: { data: AgentInfo } }>(`/admin/agents/${name}`))

export const apiToggleAgent = (name: string, enabled: boolean) =>
  http.patch(`/admin/agents/${name}/toggle`, { enabled })

export const apiCreateAgent = (body: DynamicAgentPayload) =>
  unwrap(http.post<never, { data: { data: { name: string } } }>('/admin/agents', body))

export const apiUpdateAgent = (name: string, body: Partial<DynamicAgentPayload>) =>
  unwrap(http.put<never, { data: { data: { name: string } } }>(`/admin/agents/${name}`, body))

export const apiDeleteAgent = (name: string) => http.delete(`/admin/agents/${name}`)

// ===== Admin：场景 =====
export const apiListScenes = () =>
  unwrap(http.get<never, { data: { data: Scene[] } }>('/admin/scenes'))

/** 聊天入口/下拉用：只返回启用中的场景 */
export const apiSceneOptions = () =>
  unwrap(http.get<never, { data: { data: SceneOption[] } }>('/admin/scenes/options'))

export const apiGetScene = (id: string) =>
  unwrap(http.get<never, { data: { data: Scene } }>(`/admin/scenes/${id}`))

export const apiCreateScene = (body: SceneCreatePayload) =>
  unwrap(http.post<never, { data: { data: Scene } }>('/admin/scenes', body))

export const apiUpdateScene = (id: string, body: SceneUpdatePayload) =>
  unwrap(http.patch<never, { data: { data: Scene } }>(`/admin/scenes/${id}`, body))

export const apiDeleteScene = (id: string) => http.delete(`/admin/scenes/${id}`)

/** 全量替换绑定；顺序即路由候选顺序 */
export const apiSetSceneAgents = (id: string, agentIds: string[]) =>
  unwrap(http.put<never, { data: { data: SceneBoundAgent[] } }>(`/admin/scenes/${id}/agents`, {
    agent_ids: agentIds,
  }))

/** 绑定弹窗：可绑定 Agent（全局 + 本场景专属）及当前绑定状态 */
export const apiSceneAgentOptions = (id: string) =>
  unwrap(http.get<never, { data: { data: SceneAgentOption[] } }>(`/admin/scenes/${id}/agents`))

/** 嵌入与公开接入：允许来源、匿名开关；开启匿名时后端自动生成 publish_key */
export const apiUpdateSceneWidget = (id: string, body: SceneWidgetPayload) =>
  unwrap(http.put<never, { data: { data: Scene } }>(`/admin/scenes/${id}/widget`, body))

/** 轮换服务端 API key：旧 key 立即失效，明文只返回这一次 */
export const apiRotateSceneKey = (id: string) =>
  unwrap(
    http.post<never, { data: { data: SceneRotateKeyResult } }>(
      `/admin/scenes/${id}/rotate-key`,
    ),
  )

// ===== Admin：路由分发器（只读，无任何写接口）=====
export const apiGetRouterInfo = () =>
  unwrap(http.get<never, { data: { data: RouterAgentInfo } }>('/admin/router'))

export const apiGetRouterPrompt = () =>
  unwrap(http.get<never, { data: { data: RouterPromptPreview } }>('/admin/router/prompt'))

// ===== Admin：场景路由档案（M3，派生视图）=====
/** 全局模板、占位符与 env 默认值 */
export const apiGetRouterTemplate = () =>
  unwrap(http.get<never, { data: { data: RouterAgentInfo } }>('/admin/router/template'))

/** 场景路由摘要列表：只给结论与警告，不渲染提示词 */
export const apiListRouterScenes = () =>
  unwrap(http.get<never, { data: { data: RouterSceneSummary[] } }>('/admin/router/scenes'))

export const apiGetRouterScene = (code: string) =>
  unwrap(http.get<never, { data: { data: RouterSceneDetail } }>(`/admin/router/scenes/${code}`))

export const apiGetRouterScenePrompt = (code: string) =>
  unwrap(http.get<never, { data: { data: RouterScenePrompt } }>(`/admin/router/scenes/${code}/prompt`))

/** 试运行：llm 模式会消耗一次路由模型调用；不写会话/链路 */
export const apiTestRouterScene = (code: string, message: string) =>
  unwrap(
    http.post<never, { data: { data: RouterSceneTestResult } }>(`/admin/router/scenes/${code}/test`, {
      message,
    }),
  )

export const apiGetRouterSceneStats = (code: string, hours = 24) =>
  unwrap(
    http.get<never, { data: { data: RouterSceneStats } }>(`/admin/router/scenes/${code}/stats`, {
      params: { hours },
    }),
  )

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

export const apiListMcpTools = (id: string) =>
  unwrap(http.get<never, { data: { data: MCPServerToolsResult } }>(`/admin/mcp-servers/${id}/tools`))

// ===== Admin：自定义工具 =====
export const apiListCustomTools = () =>
  unwrap(http.get<never, { data: { data: CustomToolSpec[] } }>('/admin/custom-tools'))

export const apiRegisterCustomTool = (body: { name: string; description?: string; enabled?: boolean }) =>
  unwrap(http.post<never, { data: { data: { name: string } } }>('/admin/custom-tools', body))

export const apiToggleCustomTool = (name: string, enabled: boolean) =>
  http.patch(`/admin/custom-tools/${name}/toggle`, { enabled })

export const apiDeleteCustomTool = (name: string) => http.delete(`/admin/custom-tools/${name}`)

// ===== Admin：高频固定话术 =====
/** 不传 sceneCode 返回全量；传了只返回该场景生效的全局行 + 场景覆盖行 */
export const apiListCannedScripts = (sceneCode?: string) =>
  unwrap(
    http.get<never, { data: { data: CannedScript[] } }>('/admin/canned-scripts', {
      params: sceneCode ? { scene_code: sceneCode } : {},
    }),
  )

export const apiCreateCannedScript = (body: CannedScriptPayload) =>
  unwrap(http.post<never, { data: { data: CannedScript } }>('/admin/canned-scripts', body))

export const apiUpdateCannedScript = (
  key: string,
  body: Partial<CannedScriptPayload>,
  sceneCode?: string | null,
) =>
  unwrap(
    http.put<never, { data: { data: CannedScript } }>(`/admin/canned-scripts/${key}`, body, {
      params: sceneCode ? { scene_code: sceneCode } : {},
    }),
  )

export const apiToggleCannedScript = (key: string, enabled: boolean, sceneCode?: string | null) =>
  http.patch(
    `/admin/canned-scripts/${key}/toggle`,
    { enabled },
    { params: sceneCode ? { scene_code: sceneCode } : {} },
  )

export const apiDeleteCannedScript = (key: string, sceneCode?: string | null) =>
  http.delete(`/admin/canned-scripts/${key}`, {
    params: sceneCode ? { scene_code: sceneCode } : {},
  })

// 命中测试：用真实问法验证规则，不写库、不产生会话消息
// 传 sceneCode 时按运行时的「全局 + 场景覆盖」合并规则测试
export const apiCannedScriptMatch = (query: string, sceneCode?: string | null) =>
  unwrap(
    http.post<never, { data: { data: CannedMatchResult } }>('/admin/canned-scripts/match', {
      query,
      ...(sceneCode ? { scene_code: sceneCode } : {}),
    }),
  )

// ===== Admin：Memory =====
export const apiMyMemories = (type?: string) =>
  unwrap(http.get<never, { data: { data: MemoryItem[] } }>('/memory/me', { params: { type } }))

export const apiUserMemories = (userId: string, type?: string) =>
  unwrap(http.get<never, { data: { data: MemoryItem[] } }>(`/memory/users/${userId}`, { params: { type } }))
