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

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  routed_to?: string | null
  streaming?: boolean
  created_at?: string
}

export interface SessionInfo {
  id: string
  title: string
  status: 'active' | 'closed' | 'handover'
  last_route_to?: string | null
  created_at: string
  updated_at: string
}

export interface ToolCallEvent {
  id: string
  name: string
  args: Record<string, unknown>
}

export interface ToolResultEvent {
  id: string
  name: string
  result: unknown
  error: string | null
}

export interface RouteIntent {
  agent_name: string
  confidence: number
  reason?: string
}

export interface RouteEvent {
  /** 主意图（旧契约字段，向后兼容） */
  agent_name: string
  confidence: number
  reason?: string
  /** 按主次排序的意图清单（多意图串行应答） */
  intents?: RouteIntent[]
  intent_total?: number
}

export interface AgentStartEvent extends RouteIntent {
  intent_index: number
  intent_total: number
}

export interface AgentEndEvent {
  agent_name: string
  intent_index: number
  intent_total: number
}

export interface AgentInfo {
  name: string
  description: string
  model: string
  rag_namespace: string | null
  mcp_tools: string[]
  enabled: boolean
  display_name?: string
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

export interface DocumentItem {
  id: string
  title: string
  rag_namespace: string
  status: 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count: number
  content_type?: string | null
  created_at: string
}

export interface ApiResponse<T> {
  code: number
  message: string
  data: T
}
