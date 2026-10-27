// 基础类型由 @cs/shared 维护，这里集中转出，业务代码继续从 '@/types' 导入
import type { ApiResponse, LoginResult, MemoryItem, User } from '@shared'

export type { ApiResponse, LoginResult, MemoryItem, User }

export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  routed_to?: string | null
  /** 发送者标识：'staff' 表示人工坐席接管回复，用于区分机器人与人工气泡 */
  senderType?: string | null
  streaming?: boolean
  /** 占位气泡的阶段进度文案（后端 status 事件驱动，正文到达后清空） */
  statusText?: string
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

export interface DocumentItem {
  id: string
  title: string
  rag_namespace: string
  status: 'pending' | 'processing' | 'ready' | 'failed'
  chunk_count: number
  content_type?: string | null
  created_at: string
}
