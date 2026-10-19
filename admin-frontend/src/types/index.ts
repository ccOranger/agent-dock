export interface User {
  id: string
  username: string
  role: 'customer' | 'agent' | 'admin'
  display_name?: string | null
}

export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: User
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

// ===== 咨询管理 =====
export interface InquirySession {
  id: string
  title: string
  status: 'active' | 'closed' | 'handover'
  last_route_to?: string | null
  user_id: string
  username: string
  display_name?: string | null
  message_count: number
  created_at: string
  updated_at: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  routed_to?: string | null
  /** 本轮链路 ID：仅助手消息且链路未被采样丢弃时有值 */
  trace_id?: string | null
  created_at: string
}

export interface InquiryDetail {
  session: {
    id: string
    title: string
    status: string
    last_route_to?: string | null
    user_id: string
    created_at: string
    updated_at: string
  }
  messages: ChatMessage[]
}

// ===== 请求链路追踪 =====
export type TraceOutcome = 'answered' | 'handover' | 'error'

export interface TraceSpan {
  span_id: string
  parent_id?: string | null
  /** router.llm / route.intent / canned.script / context.memory / context.rag / agent.resolve /
   *  agent.build / agent.run / tool.call / memory.persist / context.budget */
  type: string
  name: string
  start_ms: number
  duration_ms: number
  status: 'ok' | 'error' | 'empty' | 'skipped' | string
  attributes: Record<string, unknown>
}

export interface RequestTraceSummary {
  id: string
  trace_id: string
  session_id?: string | null
  user_id?: string | null
  user_message_id?: string | null
  assistant_message_id?: string | null
  intent_count: number
  agent_names: string[]
  agent_sources: string[]
  llm_models: string[]
  mcp_server_names: string[]
  tool_names: string[]
  rag_namespaces: string[]
  memory_hit_count: number
  rag_chunk_count: number
  tool_call_count: number
  tool_error_count: number
  confidence?: number | null
  outcome?: TraceOutcome | null
  handover_reason?: string | null
  error_code?: number | null
  error_message?: string | null
  ttft_ms?: number | null
  total_latency_ms?: number | null
  response_chars: number
  span_count: number
  created_at?: string | null
}

export interface RequestTraceDetail extends RequestTraceSummary {
  spans: TraceSpan[]
}

export interface TraceUsageItem {
  name: string
  count: number
}

// ===== 知识库 =====
export interface KnowledgeBase {
  namespace: string
  display_name: string
  description?: string | null
  is_active: boolean
  document_count: number
}

export interface DocumentItem {
  id: string
  title: string
  rag_namespace: string
  kind: 'upload' | 'manual'
  status: 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count: number
  content_type?: string | null
  error_message?: string | null
  original_filename?: string | null
  has_archive?: boolean
  created_at: string
}

export interface Chunk {
  id: string
  content: string
  doc_id?: string | null
  source_path?: string | null
  chunk_index?: number | null
  heading_path?: string | null
}

export interface ChunkList {
  chunks: Chunk[]
  next_offset: string | null
}

export interface SearchResult {
  content: string
  score: number
  source?: string | null
  source_name?: string | null
  heading_path?: string | null
}

// ===== Agent / Memory（迁入） =====
export interface AgentInfo {
  name: string
  description: string
  /** 路由线索：触发关键词 / 典型用户问法 */
  routing_keywords?: string[]
  routing_examples?: string[]
  model: string
  rag_namespace: string | null
  rag_namespaces?: string[]
  mcp_tools: string[]
  custom_tool_names?: string[]
  registered_tools?: string[]
  enabled: boolean
  display_name?: string
  source: 'code' | 'dynamic'
  stale?: boolean
  system_prompt?: string | null
  search_all_knowledge_bases?: boolean
  sort_order?: number
}

// ===== 路由分发器（只读）=====
export interface RouterPlaceholderInfo {
  name: string
  desc: string
}

/** 路由 Agent 由代码 + .env 定义，后台只能看不能改 */
export interface RouterAgentInfo {
  name: string
  agent_type: string
  source: 'code'
  editable: boolean
  responsibility: string
  model: string
  max_intents: number
  confidence_threshold: number
  confidence_threshold_effect: string
  fallback_candidates: string[]
  fallback_agent_default: string
  /** 兜底意图的约定置信度（固定低值，不是模型真实判断） */
  fallback_confidence: number
  /** 兜底意图是否交给兜底 Agent 应答（false = 仍按低置信转人工） */
  fallback_answer: boolean
  fallback_answer_effect: string
  history_turns: number
  history_chars: number
  keywords_limit: number
  examples_limit: number
  system_prompt_template: string
  placeholders: RouterPlaceholderInfo[]
  definition_files: { agent: string; prompt: string }
  env_keys: Record<string, string>
}

export type RouterClueLevel = 'full' | 'keywords_only' | 'examples_only' | 'description_only'

export interface RouterCandidate {
  name: string
  description: string
  source: 'code' | 'dynamic'
  routing_keywords: string[]
  routing_examples: string[]
  keywords_total: number
  examples_total: number
  keywords_injected: string[]
  examples_injected: string[]
  clue_level: RouterClueLevel
}

export interface RouterPromptPreview {
  prompt: string
  prompt_chars: number
  candidate_count: number
  fallback_agent: string
  fallback_from_candidates: boolean
  candidates: RouterCandidate[]
}

export type MCPTransport = 'sse' | 'http' | 'stdio'

export interface MCPServer {
  id: string
  name: string
  description?: string | null
  type: string
  enabled: boolean
  config: {
    transport: MCPTransport
    endpoint?: string | null
    command?: string | null
    args?: string[]
    headers?: Record<string, string> | null
    auth?: Record<string, unknown> | null
    timeout_seconds?: number
  }
  used_by_agents: string[]
}

export interface MCPServerPayload {
  name: string
  description?: string | null
  transport: MCPTransport
  endpoint?: string | null
  command?: string | null
  args?: string[]
  headers?: Record<string, string> | null
  auth?: Record<string, unknown> | null
  timeout_seconds: number
  enabled?: boolean
}

export interface MCPServerTool {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface MCPServerToolsResult {
  ok: boolean
  server_name?: string
  tools?: MCPServerTool[]
  error?: string
}

// ===== 自定义工具（实现来自代码目录，后台登记/开关）=====
export interface CustomToolSpec {
  name: string
  description: string
  parameters: Record<string, unknown>
  registered: boolean
  enabled: boolean
  display_description?: string | null
}

export interface DynamicAgentPayload {
  name: string
  display_name?: string | null
  description: string
  /** 路由线索：不填也能保存，但路由只能依描述猜测适用范围 */
  routing_keywords?: string[]
  routing_examples?: string[]
  system_prompt: string
  llm_model?: string
  rag_namespace?: string | null
  rag_namespaces?: string[]
  search_all_knowledge_bases?: boolean
  mcp_tool_names: string[]
  custom_tool_names?: string[]
  enabled?: boolean
  sort_order?: number
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

/** 记忆类型的中文展示名（后台下拉与时间线共用） */
export const MEMORY_TYPE_LABELS: Record<MemoryItem['type'], string> = {
  working: '工作记忆',
  episodic: '情景记忆',
  semantic: '语义记忆',
  perceptual: '感知记忆',
}

// ===== 工单管理 =====
export type TicketStatus = 'open' | 'in_progress' | 'escalated' | 'resolved' | 'closed'
export type TicketUrgency = 'low' | 'medium' | 'high'

export interface TicketUpdateEntry {
  content: string
  at: string
  action?: string
}

export interface TicketSummary {
  id: string
  ticket_no: string
  user_id?: string | null
  session_id?: string | null
  category: string
  urgency: TicketUrgency
  status: TicketStatus
  title?: string | null
  content: string
  update_count: number
  created_at?: string | null
  updated_at?: string | null
}

export interface TicketDetail extends TicketSummary {
  updates: TicketUpdateEntry[]
}

export const TICKET_STATUS_LABELS: Record<TicketStatus, string> = {
  open: '待处理',
  in_progress: '处理中',
  escalated: '已升级',
  resolved: '已解决',
  closed: '已关闭',
}

export const TICKET_URGENCY_LABELS: Record<TicketUrgency, string> = {
  low: '低',
  medium: '中',
  high: '高',
}

// ===== 高频固定话术（硬模板）=====
export type CannedScriptCategory = 'identity' | 'capability' | 'probing' | 'business'

export interface CannedScript {
  id: string
  key: string
  title?: string | null
  category: CannedScriptCategory
  /** 问法主干：归一化后整句全等才命中 */
  patterns: string[]
  /** 关键词：整句包含即命中，判定更宽，误吞风险也更高 */
  keywords: string[]
  reply_text: string
  priority: number
  enabled: boolean
  /** 种子三条（建库时写入）：可改文案、可停用，不可删除；运行时只读这张表，代码里无兜底规则 */
  is_builtin: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface CannedScriptPayload {
  key: string
  title?: string | null
  category: CannedScriptCategory
  patterns: string[]
  keywords: string[]
  reply_text: string
  priority: number
  enabled: boolean
}

export interface CannedMatchResult {
  query: string
  normalized: string
  matched: boolean
  key?: string | null
  category?: CannedScriptCategory | null
  title?: string | null
  reply_text?: string | null
  rule_snapshot: CannedScript[]
}

export const CANNED_CATEGORY_LABELS: Record<CannedScriptCategory, string> = {
  identity: '身份介绍',
  capability: '能力介绍',
  probing: '探听拒答',
  business: '业务话术',
}
