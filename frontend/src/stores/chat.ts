import { defineStore } from 'pinia'
import { ref } from 'vue'
import { nanoid } from 'nanoid'
import type { Message, ToolCallEvent, ToolResultEvent } from '@/types'

export interface ToolCallItem extends ToolCallEvent {
  result?: ToolResultEvent
}

/** 打字机逐字揭示的定时器间隔（毫秒） */
const REVEAL_INTERVAL = 16

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string | null>(null)
  const messages = ref<Message[]>([])
  const currentAgent = ref<string | null>(null)
  const streaming = ref(false)
  const toolCalls = ref<ToolCallItem[]>([])

  // 打字机：已收到但尚未显示的文本缓冲 + 逐字揭示定时器
  const pending = ref('')
  let revealTimer: ReturnType<typeof setInterval> | null = null
  let streamEnded = false

  function reset() {
    stopReveal()
    pending.value = ''
    streamEnded = false
    sessionId.value = null
    messages.value = []
    currentAgent.value = null
    streaming.value = false
    toolCalls.value = []
  }

  function pushUser(content: string) {
    // 新一轮对话：清空上一轮可能残留的缓冲
    stopReveal()
    pending.value = ''
    streamEnded = false
    messages.value.push({ id: nanoid(), role: 'user', content })
    // 立即放一个"思考中"占位气泡，statusText 随后由 SSE status 事件按真实阶段推进，
    // 避免路由/构建阶段的数秒空窗
    messages.value.push({
      id: nanoid(),
      role: 'assistant',
      content: '',
      streaming: true,
      statusText: '思考中…',
    })
  }

  /** status 事件：更新占位气泡的阶段文案（已开始出正文则不再覆盖） */
  function setStatus(text: string) {
    const msg = currentStreamMessage()
    if (msg && !msg.content && !pending.value && text) msg.statusText = text
  }

  /** 一轮结束仍无正文（转人工/出错/空应答）：移除占位气泡，不留空气泡壳 */
  function dropEmptyStream() {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && !last.content && !pending.value) {
      messages.value.splice(messages.value.indexOf(last), 1)
    }
  }

  function currentStreamMessage(): Message | undefined {
    const last = messages.value[messages.value.length - 1]
    return last && last.role === 'assistant' && last.streaming ? last : undefined
  }

  /** 收到一段流式文本：进入缓冲队列，由定时器逐字揭示，形成打字机效果 */
  function appendText(text: string) {
    if (!text) return
    if (!currentStreamMessage()) {
      messages.value.push({ id: nanoid(), role: 'assistant', content: '', streaming: true })
    }
    pending.value += text
    startReveal()
  }

  function revealTick() {
    const msg = currentStreamMessage()
    if (!msg) {
      stopReveal()
      return
    }
    if (pending.value.length > 0) {
      // 每 tick 揭示的字符数随缓冲长度自适应：突发到达的长文本也能较快跟上，
      // 但设上限以保持逐字观感。
      const step = Math.min(pending.value.length, Math.max(1, Math.ceil(pending.value.length / 10)))
      msg.content += pending.value.slice(0, step)
      pending.value = pending.value.slice(step)
      msg.statusText = '' // 正文已开始，撤下阶段进度行
      onReveal?.()
    } else if (streamEnded) {
      // 缓冲清空且流已结束：收尾
      msg.streaming = false
      streaming.value = false
      stopReveal()
      dropEmptyStream()
    }
  }

  function startReveal() {
    if (revealTimer != null) return
    revealTimer = setInterval(revealTick, REVEAL_INTERVAL)
  }

  function stopReveal() {
    if (revealTimer != null) {
      clearInterval(revealTimer)
      revealTimer = null
    }
  }

  /** 立即把剩余缓冲全部刷出（用于停止/出错，避免文字卡在缓冲里） */
  function flushPending() {
    const msg = currentStreamMessage()
    if (msg && pending.value) {
      msg.content += pending.value
      pending.value = ''
      onReveal?.()
    }
  }

  // 每次揭示一个字后的回调（ChatView 注册用于自动滚动到底部）
  let onReveal: (() => void) | null = null
  function setRevealHook(fn: (() => void) | null) {
    onReveal = fn
  }

  function addToolCall(data: ToolCallEvent) {
    toolCalls.value.push({ ...data })
  }

  function finishToolCall(data: ToolResultEvent) {
    const item = toolCalls.value.find((t) => t.id === data.id)
    if (item) item.result = data
  }

  function setRoute(agentName: string) {
    currentAgent.value = agentName
  }

  /** 接管轮询拉到的坐席回复：直接落一条定稿的 assistant 气泡（非流式） */
  function pushResolvedAssistant(content: string, senderType: string | null = null) {
    messages.value.push({ id: nanoid(), role: 'assistant', content, senderType })
  }

  /** 流结束：不立即收尾，等缓冲逐字揭示完再关闭打字机 */
  function finishStreaming() {
    streamEnded = true
    if (pending.value.length === 0) {
      const msg = currentStreamMessage()
      if (msg) msg.streaming = false
      streaming.value = false
      stopReveal()
      dropEmptyStream()
    } else {
      startReveal()
    }
  }

  /** 强制立即结束（用户点击停止 / 出错）：刷出缓冲并收尾 */
  function forceFinish() {
    flushPending()
    const msg = currentStreamMessage()
    if (msg) msg.streaming = false
    streaming.value = false
    streamEnded = false
    stopReveal()
    dropEmptyStream()
  }

  return {
    sessionId,
    messages,
    currentAgent,
    streaming,
    toolCalls,
    reset,
    pushUser,
    setStatus,
    appendText,
    addToolCall,
    finishToolCall,
    setRoute,
    pushResolvedAssistant,
    finishStreaming,
    forceFinish,
    setRevealHook,
  }
})
