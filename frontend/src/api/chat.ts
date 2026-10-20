import { streamSSE, type SSEEvent } from '@shared'

import { getToken } from './http'

export type { SSEEvent }

/**
 * 流式对话：POST /api/v1/chat/stream（SSE）
 * 返回 AbortController 以便随时中断；读取/分帧逻辑见 @cs/shared 的 streamSSE。
 */
export function streamChat(
  sessionId: string | null,
  message: string,
  handlers: Record<string, (data: any) => void>,
): AbortController {
  return streamSSE({
    url: '/api/v1/chat/stream',
    token: getToken(),
    body: { session_id: sessionId, message, stream: true },
    handlers,
  })
}
