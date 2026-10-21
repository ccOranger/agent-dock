// 基础类型由 @cs/shared 维护，这里集中转出，业务代码继续从 '@/types' 导入
import type { ApiResponse, LoginResult, MemoryItem, Paginated, User } from '@shared'

export type { ApiResponse, LoginResult, MemoryItem, Paginated, User }

// ===== 咨询管理 =====
export interface InquirySession {
  id: string
  title: string
  status: 'active' | 'closed' | 'handover'
  last_route_to?: string | null
  /** 会话归属场景：跨场景会话互不可见 */
  scene_id?: string | null
  scene_code?: string | null
  /** 登录客户会话才有 user_id；嵌入匿名访客会话只有 visitor_id */
  user_id?: string | null
  visitor_id?: string | null
  username?: string | null
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
    scene_id?: string | null
    scene_code?: string | null
    user_id: string | null
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
  /** 本轮请求归属场景（无码请求落默认场景） */
  scene_code?: string | null
  /** 路由维度：直连跳过 / 兜底降级 / 路由耗时 / 提示词指纹（与后台路由档案同源） */
  router_skipped?: boolean | null
  router_fallback?: boolean | null
  router_ms?: number | null
  prompt_hash?: string | null
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
  /** 归属场景：动态 Agent 属于创建它的场景；NULL=全局（代码 Agent/平台共享） */
  scene_id?: string | null
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

// ===== M3 场景路由档案（派生视图，不是实体）=====
/** 实际生效方式：direct=直连、llm=走路由模型、unconfigured=未配置可用 Agent */
export type RouterEffectiveMode = 'direct' | 'llm' | 'unconfigured'
/** 参数来源：scene=本场景覆盖、global=继承全局默认 */
export type RouterConfigSource = 'scene' | 'global'
/** 兜底 Agent 来源：scene=场景配置；configured_missing=配置失效已降级；preferred/pool_first=池内派生 */
export type RouterFallbackSource =
  | 'scene'
  | 'configured_missing'
  | 'preferred'
  | 'pool_first'
  | 'empty'

export type RouterWarningCode =
  | 'no_agents'
  | 'single_agent_direct'
  | 'fallback_not_in_pool'
  | 'agent_description_only'
  | 'no_knowledge_base'
  | 'no_identity_script'
  | 'scene_disabled'

/** 单个场景的路由档案：后台看到的就是运行时用的那份 */
export interface RouterSceneProfile {
  scene_id: string
  scene_code: string
  scene_name: string
  scene_status: SceneStatus
  is_default: boolean
  mode: RouterEffectiveMode
  /** 直连目标（仅 mode=direct 时非空） */
  direct_agent: string | null
  configured_mode: SceneRouterMode
  model: string
  model_source: RouterConfigSource
  confidence_threshold: number
  threshold_source: RouterConfigSource
  max_intents: number
  max_intents_source: RouterConfigSource
  fallback_agent: string | null
  fallback_source: RouterFallbackSource
  fallback_text: string
  fallback_text_source: 'scene' | 'default'
  candidate_count: number
  warnings: RouterWarningCode[]
  prompt_chars: number
  prompt_hash: string
  /** 列表摘要不带候选明细 */
  candidates?: RouterCandidate[]
}

/** 列表接口：不返回候选明细与提示词正文 */
export type RouterSceneSummary = Omit<RouterSceneProfile, 'candidates'>

/** 详情接口：带候选明细与线索完整度 */
export interface RouterSceneDetail extends RouterSceneProfile {
  candidates: RouterCandidate[]
}

export interface RouterScenePrompt {
  scene_code: string
  scene_name: string
  mode: RouterEffectiveMode
  prompt: string
  prompt_chars: number
  prompt_hash: string
  scene_block: string
  candidate_count: number
  candidates: RouterCandidate[]
}

export interface RouterIntent {
  agent_name: string
  confidence: number
  reason: string
  is_fallback?: boolean
  matched_keywords?: string[]
  clue_basis?: string
}

export interface RouterSceneTestResult {
  scene_code: string
  mode: RouterEffectiveMode
  router_skipped: boolean
  candidate_count: number
  prompt_hash: string
  elapsed_ms: number
  agent_name: string | null
  confidence: number | null
  reason: string | null
  intents: RouterIntent[]
  fallback_agent: string | null
}

export interface RouterSceneStats {
  scene_code: string
  scene_name: string
  hours: number
  requests: number
  router_skipped: number
  router_fallback: number
  handover: number
  skip_rate: number
  fallback_rate: number
  handover_rate: number
  avg_router_ms: number | null
  avg_total_latency_ms: number | null
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

// ===== 场景（多业务接入单元）=====
export type SceneStatus = 'active' | 'paused' | 'archived'
export type SceneRouterMode = 'auto' | 'llm' | 'direct'

/**
 * 路由不是实体：每个场景的「路由档案」由 router_mode + 绑定 Agent 派生。
 * auto 下 0 个=未配置、1 个=直连、>=2 个=LLM 路由；llm 永不直连；direct 强制直连。
 */
export interface Scene {
  id: string
  /** 对外唯一标识：chat 请求用它区分场景，创建后不可修改 */
  code: string
  display_name: string
  description?: string | null
  status: SceneStatus
  /** 默认场景：无 scene_code 的请求落在这里，只允许改展示名与描述 */
  is_default: boolean
  router_mode: SceneRouterMode
  router_model?: string | null
  router_confidence_threshold?: number | null
  router_max_intents?: number | null
  fallback_agent_id?: string | null
  fallback_text?: string | null
  extra_router_prompt?: string | null
  publish_key?: string | null
  allowed_origins: string[]
  /** 嵌入组件外观；只含后台显式保存过的键，缺省值以后端为准 */
  widget_config?: Record<string, unknown>
  /** 是否已配置服务端 API key（哈希不出后台接口） */
  api_key_set?: boolean
  /** 启用中的绑定 Agent 数（停用的绑定不计入） */
  agent_count: number
  created_at?: string | null
  updated_at?: string | null
  bound_agent_ids?: string[]
}

export interface SceneOption {
  /** 场景 id：前端把各类资源的 scene_id 映射回可读名称 */
  id: string
  code: string
  display_name: string
  is_default: boolean
}

/** 绑定弹窗的一行：可绑定范围仅全局 Agent + 本场景专属 Agent */
export interface SceneAgentOption {
  id: string
  name: string
  display_name: string
  source: 'code' | 'dynamic'
  enabled: boolean
  bound: boolean
  scene_enabled: boolean
  sort_order: number
}

export interface SceneBoundAgent {
  id: string
  name: string
  display_name: string
  description?: string | null
  source: 'code' | 'dynamic'
  sort_order: number
}

/** 嵌入与公开接入配置：未传字段保持原值，widget_config 是全量替换而不是深合并 */
export interface SceneWidgetPayload {
  widget_config?: Record<string, unknown>
  allowed_origins?: string[]
  allow_anonymous?: boolean
}

export interface SceneRotateKeyResult {
  /** 一次性明文，关闭抽屉后无法再次查看 */
  api_key: string
  api_key_set: boolean
}

export interface SceneCreatePayload {
  code: string
  display_name: string
  description?: string | null
  router_mode?: SceneRouterMode
  fallback_agent_id?: string | null
  fallback_text?: string | null
  extra_router_prompt?: string | null
  agent_ids?: string[]
}

export interface SceneUpdatePayload {
  display_name?: string
  description?: string | null
  status?: SceneStatus
  router_mode?: SceneRouterMode
  router_model?: string | null
  router_confidence_threshold?: number | null
  router_max_intents?: number | null
  fallback_agent_id?: string | null
  fallback_text?: string | null
  extra_router_prompt?: string | null
  /** 传了才全量替换绑定；顺序即路由候选顺序 */
  agent_ids?: string[]
}

// ===== 高频固定话术（硬模板）=====
export type CannedScriptCategory = 'identity' | 'capability' | 'probing' | 'business'

export interface CannedScript {
  id: string
  key: string
  /** 归属场景：NULL=全局（如 probing），非空=该场景覆盖行（同 key 覆盖全局） */
  scene_id?: string | null
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
  scene_code?: string | null
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
  scene_code?: string | null
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
