<template>
  <div>
    <el-row :gutter="16">
      <el-col :span="8">
        <el-card>
          <el-statistic title="业务 Agent 数" :value="agents.length" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <el-statistic title="已启用" :value="agents.filter((a) => a.enabled).length" />
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card>
          <el-statistic title="MCP 工具引用" :value="mcpCount" />
        </el-card>
      </el-col>
    </el-row>

    <el-card style="margin-top: 16px">
      <template #header><b>Prometheus 指标</b></template>
      <p>
        访问后端
        <el-link type="primary" href="/metrics" target="_blank">/metrics</el-link>
        查看 cs_chat_requests_total / cs_router_decisions_total / cs_active_sse_connections 等指标
      </p>
      <el-table :data="agents">
        <el-table-column prop="name" label="Agent" />
        <el-table-column prop="model" label="模型" />
        <el-table-column prop="rag_namespace" label="Namespace" />
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { apiListAgents } from '@/api'
import type { AgentInfo } from '@/types'

const agents = ref<AgentInfo[]>([])
const mcpCount = computed(() => agents.value.reduce((n, a) => n + (a.mcp_tools?.length ?? 0), 0))

onMounted(async () => {
  agents.value = await apiListAgents()
})
</script>
