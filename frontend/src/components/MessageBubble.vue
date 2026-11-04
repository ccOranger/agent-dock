<template>
  <div class="bubble" :class="[message.role, { staff: message.senderType === 'staff' }]">
    <div class="meta">
      <el-tag v-if="message.senderType === 'staff'" size="small" type="success">人工客服</el-tag>
      <AgentBadge v-else-if="message.routed_to" :agent="message.routed_to" />
      <span v-if="message.streaming" class="typing">{{ message.statusText || '输入中…' }}</span>
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
  border-radius: 16px;
  margin: 10px 0;
  word-break: break-word;
  color: var(--fc-text);
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(79, 107, 255, 0.12);
  box-shadow: 0 6px 18px rgba(31, 42, 80, 0.06);
}
/* 靠说话方一侧收一个小圆角，给气泡一个“指向” */
.bubble.assistant {
  border-bottom-left-radius: 6px;
}
.bubble.user {
  margin-left: auto;
  color: #fff;
  border: 0;
  background: linear-gradient(135deg, var(--fc-brand), var(--fc-brand-2));
  box-shadow: 0 8px 22px rgba(79, 107, 255, 0.26);
  border-bottom-right-radius: 6px;
}
.bubble.staff {
  background: linear-gradient(135deg, rgba(232, 255, 246, 0.96), rgba(232, 247, 255, 0.96));
  border-color: rgba(60, 190, 140, 0.25);
}
.meta {
  margin-bottom: 4px;
}
.typing {
  font-size: 12px;
  color: var(--fc-text-2);
  animation: typing-blink 1.2s ease-in-out infinite;
}
@keyframes typing-blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* 手机屏幕窄，气泡放宽到 86% 再配合更紧的内边距，避免一屏只装两行字 */
@media (max-width: 768px) {
  .bubble {
    max-width: 86%;
    padding: 9px 12px;
  }
}
</style>
