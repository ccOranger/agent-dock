<template>
  <el-card class="tool-card" shadow="never">
    <div class="head">
      <span class="name">🔧 {{ tool.name }}</span>
      <el-tag v-if="!tool.result" size="small">调用中</el-tag>
      <el-tag v-else-if="tool.result.error" size="small" type="danger">失败</el-tag>
      <el-tag v-else size="small" type="success">完成</el-tag>
    </div>
    <pre class="json">{{ JSON.stringify(tool.args, null, 2) }}</pre>
    <pre v-if="tool.result" class="json">{{ JSON.stringify(tool.result.result, null, 2) }}</pre>
  </el-card>
</template>

<script setup lang="ts">
import type { ToolCallItem } from '@/stores/chat'

defineProps<{ tool: ToolCallItem }>()
</script>

<style scoped>
.tool-card {
  margin: 8px 0;
  background: #f9f9fb;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.name {
  font-weight: 600;
  font-size: 13px;
}
.json {
  font-size: 12px;
  max-height: 160px;
  overflow: auto;
  margin: 6px 0 0;
}
</style>
