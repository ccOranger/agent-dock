<template>
  <div class="bubble" :class="message.role">
    <div class="meta">
      <AgentBadge v-if="message.routed_to" :agent="message.routed_to" />
      <span v-if="message.streaming" class="typing">输入中…</span>
    </div>
    <StreamRenderer v-if="message.role === 'assistant'" :content="message.content" />
    <span v-else>{{ message.content }}</span>
  </div>
</template>

<script setup lang="ts">
import type { Message } from '@/types'
import StreamRenderer from './StreamRenderer.vue'
import AgentBadge from './AgentBadge.vue'

defineProps<{ message: Message }>()
</script>

<style scoped>
.bubble {
  max-width: 72%;
  padding: 10px 14px;
  border-radius: 10px;
  margin: 8px 0;
  word-break: break-word;
  background: #fff;
  border: 1px solid #ebeef5;
}
.bubble.user {
  margin-left: auto;
  background: #ecf5ff;
  border-color: #d9ecff;
}
.meta {
  margin-bottom: 4px;
}
.typing {
  font-size: 12px;
  color: #909399;
}
</style>
