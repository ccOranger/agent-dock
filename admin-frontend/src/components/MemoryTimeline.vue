<template>
  <el-timeline v-if="items.length">
    <el-timeline-item
      v-for="m in items"
      :key="m.id"
      :timestamp="fmt(m.created_at)"
      :type="tagType(m.type)"
    >
      <div class="head">
        <b>{{ MEMORY_TYPE_LABELS[m.type] || m.type }}</b>
        <span class="meta">重要性 {{ m.importance.toFixed(2) }} · 访问 {{ m.access_count }} 次</span>
        <span class="spacer" />
        <el-button link type="primary" size="small" @click="copy(m)">复制</el-button>
      </div>
      <div class="content" :class="{ clamped: isLong(m.content) && !expanded.has(m.id) }">
        {{ m.content }}
      </div>
      <el-button v-if="isLong(m.content)" link size="small" @click="toggle(m.id)">
        {{ expanded.has(m.id) ? '收起' : '展开全部' }}
      </el-button>
    </el-timeline-item>
  </el-timeline>
  <el-empty v-else description="暂无记忆" />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import dayjs from 'dayjs'
import { ElMessage } from 'element-plus'
import { MEMORY_TYPE_LABELS, type MemoryItem } from '@/types'

defineProps<{ items: MemoryItem[] }>()

// 长内容默认折叠 3 行：客户原话可能很长，全部铺开会把时间线撑散
const CLAMP_CHARS = 120
const expanded = ref(new Set<string>())

function isLong(content: string) {
  return (content || '').length > CLAMP_CHARS
}

function toggle(id: string) {
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}

async function copy(m: MemoryItem) {
  try {
    await navigator.clipboard.writeText(m.content)
    ElMessage.success('已复制记忆内容')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中')
  }
}

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'
}

function tagType(type: MemoryItem['type']) {
  return { working: 'primary', episodic: 'success', semantic: 'warning', perceptual: 'info' }[type] as any
}
</script>

<style scoped>
.head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.meta {
  color: var(--cs-text-3);
  font-size: 12px;
}

.spacer {
  flex: 1;
}

.content {
  margin-top: 4px;
  color: var(--cs-text-2);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}

.content.clamped {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
