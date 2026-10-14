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
  model: string
  rag_namespace: string | null
  mcp_tools: string[]
  enabled: boolean
  display_name?: string
  source: 'code' | 'dynamic'
  system_prompt?: string | null
  search_all_knowledge_bases?: boolean
  sort_order?: number
}

export type MCPTransport = 'sse' | 'http' | 'stdio'

export interface MCPServer {
  id: string
  name: string
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
  transport: MCPTransport
  endpoint?: string | null
  command?: string | null
  args?: string[]
  headers?: Record<string, string> | null
  auth?: Record<string, unknown> | null
  timeout_seconds: number
  enabled?: boolean
}

export interface DynamicAgentPayload {
  name: string
  display_name?: string | null
  description: string
  system_prompt: string
  llm_model?: string
  rag_namespace?: string | null
  search_all_knowledge_bases?: boolean
  mcp_tool_names: string[]
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
