import { getToken } from './http'

export interface SSEEvent {
  event: string
  data: any
}

function parseSSE(raw: string): SSEEvent {
  let event = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
    // ':' 开头为心跳注释，忽略
  }
  let data: any = null
  try {
    data = dataStr ? JSON.parse(dataStr) : null
  } catch {
    data = dataStr
  }
  return { event, data }
}

/**
 * 流式对话：POST /api/v1/chat/stream（SSE）
 * 返回 AbortController 以便随时中断
 */
export function streamChat(
  sessionId: string | null,
  message: string,
  handlers: Record<string, (data: any) => void>,
): AbortController {
  const controller = new AbortController()
  fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ session_id: sessionId, message, stream: true }),
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok || !resp.body) {
        handlers.error?.({ code: resp.status, message: `HTTP ${resp.status}` })
        return
      }
      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const events = buffer.split('\n\n')
        buffer = events.pop() || ''
        for (const raw of events) {
          if (!raw.trim() || raw.startsWith(':')) continue
          const { event, data } = parseSSE(raw)
          handlers[event]?.(data)
        }
      }
    })
    .catch((e) => {
      if (e.name !== 'AbortError') handlers.error?.({ code: 5001, message: String(e) })
    })
  return controller
}
