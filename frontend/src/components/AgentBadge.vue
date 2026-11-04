<template>
  <span class="agent-pill">🤖 {{ agent }}</span>
</template>

<script setup lang="ts">
defineProps<{ agent: string }>()
</script>

<style scoped>
/* 顶部与消息里的 Agent 标识统一成小药丸，信息密度比整块 el-tag 更低 */
.agent-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  line-height: 18px;
  color: var(--fc-brand);
  background: rgba(79, 107, 255, 0.1);
  border: 1px solid rgba(79, 107, 255, 0.18);
  white-space: nowrap;
}
</style>
