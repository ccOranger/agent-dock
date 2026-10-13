import { defineStore } from 'pinia'
import { ref } from 'vue'
import { nanoid } from 'nanoid'
import type { Message, ToolCallEvent, ToolResultEvent } from '@/types'

export interface ToolCallItem extends ToolCallEvent {
  result?: ToolResultEvent
}

export const useChatStore = defineStore('chat', () => {
  const sessionId = ref<string | null>(null)
  const messages = ref<Message[]>([])
  const currentAgent = ref<string | null>(null)
  const streaming = ref(false)
  const toolCalls = ref<ToolCallItem[]>([])

  function reset() {
    sessionId.value = null
    messages.value = []
    currentAgent.value = null
    streaming.value = false
    toolCalls.value = []
  }

  function pushUser(content: string) {
    messages.value.push({ id: nanoid(), role: 'user', content })
  }

  function appendText(text: string) {
    const last = messages.value[messages.value.length - 1]
    if (last?.streaming) last.content += text
    else
      messages.value.push({
        id: nanoid(),
        role: 'assistant',
        content: text,
        streaming: true,
      })
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

  function finishStreaming() {
    const last = messages.value[messages.value.length - 1]
    if (last) last.streaming = false
    streaming.value = false
  }

  return {
    sessionId,
    messages,
    currentAgent,
    streaming,
    toolCalls,
    reset,
    pushUser,
    appendText,
    addToolCall,
    finishToolCall,
    setRoute,
    finishStreaming,
  }
})
