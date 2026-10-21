/** SSE 事件：event 名 + 已解析的 data */
export interface SSEEvent {
  event: string
  data: any
}

/** 解析单个 SSE 报文块（event: / data: 多行；':' 开头为心跳注释，忽略） */
export function parseSSE(raw: string): SSEEvent {
  let event = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
  }
  let data: any = null
  try {
    data = dataStr ? JSON.parse(dataStr) : null
  } catch {
    data = dataStr
  }
  return { event, data }
}

export interface StreamSSEOptions {
  url: string
  body: unknown
  /** event 名 -> 处理函数；未注册的事件静默丢弃 */
  handlers: Record<string, (data: any) => void>
  token?: string | null
  headers?: Record<string, string>
  /** 网络异常时的错误码，默认 5001 */
  errorCode?: number
  /**
   * 流结束兜底回调：正常 EOF / 网络异常 / 主动中断都会触发。
   * 供调用方复位自身状态（如 streaming 标记），避免未收到终止事件时状态永久卡住。
   */
  onClose?: () => void
}

/**
 * POST + SSE 流式读取：按 event 分发给 handlers，返回 AbortController 以便随时中断。
 * 调用方只提供 URL、鉴权 token 与事件处理表，读取/分帧逻辑只维护这一份。
 */
export function streamSSE(options: StreamSSEOptions): AbortController {
  const { url, body, handlers, token, headers, errorCode = 5001, onClose } = options
  const controller = new AbortController()
  fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: JSON.stringify(body),
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
      if (e.name !== 'AbortError') handlers.error?.({ code: errorCode, message: String(e) })
    })
    .finally(() => {
      // 兜底：无论流因何结束（done / 服务端断开 / 代理超时 / 主动中断）都通知调用方，
      // 保证没有终止事件时外部也能收尾，不会出现输入框永久禁用的状态
      onClose?.()
    })
  return controller
}
