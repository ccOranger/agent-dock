<template>
  <el-timeline>
    <el-timeline-item
      v-for="m in items"
      :key="m.id"
      :timestamp="m.created_at"
      :type="tagType(m.type)"
    >
      <b>{{ m.type }}</b> · 重要性 {{ m.importance.toFixed(2) }} · 访问 {{ m.access_count }} 次
      <div class="content">{{ m.content }}</div>
    </el-timeline-item>
  </el-timeline>
  <el-empty v-if="!items.length" description="暂无记忆" />
</template>

<script setup lang="ts">
import type { MemoryItem } from '@/types'

defineProps<{ items: MemoryItem[] }>()

function tagType(type: MemoryItem['type']) {
  return { working: 'primary', episodic: 'success', semantic: 'warning', perceptual: 'info' }[type] as any
}
</script>

<style scoped>
.content {
  margin-top: 4px;
  color: #606266;
  font-size: 13px;
}
</style>
