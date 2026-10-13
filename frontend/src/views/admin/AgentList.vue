<template>
  <div class="page">
    <el-card>
      <template #header><b>Agent 管理</b></template>
      <el-table :data="agents" v-loading="loading">
        <el-table-column prop="name" label="名称" width="180" />
        <el-table-column prop="description" label="描述" />
        <el-table-column prop="model" label="模型" width="120" />
        <el-table-column prop="rag_namespace" label="Namespace" width="150" />
        <el-table-column label="MCP 工具" width="200">
          <template #default="{ row }">
            <el-tag v-for="t in row.mcp_tools" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="toggle(row)" />
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiListAgents, apiToggleAgent } from '@/api'
import type { AgentInfo } from '@/types'

const agents = ref<AgentInfo[]>([])
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    agents.value = await apiListAgents()
  } finally {
    loading.value = false
  }
}

async function toggle(row: AgentInfo) {
  await apiToggleAgent(row.name, row.enabled)
  ElMessage.success(`${row.name} 已${row.enabled ? '启用' : '禁用'}`)
}

onMounted(load)
</script>

<style scoped>
.page {
  padding: 24px 8%;
}
</style>
