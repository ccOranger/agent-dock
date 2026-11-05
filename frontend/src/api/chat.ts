import { streamSSE, type SSEEvent } from '@shared'

import { API_BASE, getToken } from './http'

export type { SSEEvent }

/**
 * 流式对话：POST /api/v1/chat/stream（SSE）
 * scene_code 留空落到默认场景；传了就只在该场景绑定的 Agent 里回答。
 * 返回 AbortController 以便随时中断；读取/分帧逻辑见 @shared 的 streamSSE。
 */
export function streamChat(
  sessionId: string | null,
  message: string,
  handlers: Record<string, (data: any) => void>,
  onClose?: () => void,
  sceneCode?: string | null,
): AbortController {
  return streamSSE({
    url: `${API_BASE}/chat/stream`,
    token: getToken(),
    body: {
      session_id: sessionId,
      message,
      stream: true,
      ...(sceneCode ? { scene_code: sceneCode } : {}),
    },
    handlers,
    onClose,
  })
}
