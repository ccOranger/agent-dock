<template>
  <div class="page">
    <PageHeader title="概览" />

    <!-- 顶部资源统计卡片 -->
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card>
          <el-statistic title="业务 Agent" :value="agents.length">
            <template #suffix>
              <span class="suffix">/ 已启用 {{ enabledAgentCount }}</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="知识库" :value="bases.length">
            <template #suffix>
              <span class="suffix">/ 已启用 {{ activeBaseCount }}</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="MCP 服务" :value="mcpServers.length">
            <template #suffix>
              <span class="suffix">/ 已启用 {{ enabledMcpCount }}</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="自定义工具" :value="customTools.length">
            <template #suffix>
              <span class="suffix">/ 已启用 {{ enabledCustomCount }}</span>
            </template>
          </el-statistic>
        </el-card>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="6">
        <el-card>
          <el-statistic title="MCP 工具引用次数" :value="mcpRefCount" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="待处理工单" :value="openTicketCount" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="进行中咨询" :value="activeInquiryCount" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card>
          <el-statistic title="近 7 天工具调用" :value="totalToolCalls" />
        </el-card>
      </el-col>
    </el-row>

    <!-- 近 7 天调用排行：基于 request_traces 聚合的真实数据，非 Prometheus 指标 -->
    <el-row :gutter="16">
      <el-col :span="12">
        <el-card>
          <template #header><span class="card-head-title">近 7 天工具调用 Top 5</span></template>
          <p v-if="!toolUsageBars.length" class="muted">近 7 天暂无工具调用记录</p>
          <div v-else class="bar-list">
            <div v-for="bar in toolUsageBars" :key="bar.name" class="bar-row">
              <span class="bar-label">{{ bar.name }}</span>
              <div class="bar-track"><div class="bar-fill" :style="{ width: bar.pct + '%' }" /></div>
              <span class="bar-count">{{ bar.count }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card>
          <template #header><span class="card-head-title">近 7 天 Agent 应答 Top 5</span></template>
          <p v-if="!agentUsageBars.length" class="muted">近 7 天暂无 Agent 应答记录</p>
          <div v-else class="bar-list">
            <div v-for="bar in agentUsageBars" :key="bar.name" class="bar-row">
              <span class="bar-label">{{ bar.name }}</span>
              <div class="bar-track"><div class="bar-fill" :style="{ width: bar.pct + '%' }" /></div>
              <span class="bar-count">{{ bar.count }}</span>
            </div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Agent 概览 -->
    <el-card>
      <template #header>
        <div class="card-head">
          <span class="card-head-title">Agent 概览</span>
        </div>
      </template>
      <el-table :data="agents">
        <el-table-column prop="name" label="Agent" />
        <el-table-column prop="model" label="模型" width="180" />
        <el-table-column label="来源" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="row.source === 'code' ? 'info' : 'warning'">
              {{ row.source === 'code' ? '代码' : '动态' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="96">
          <template #default="{ row }">
            <el-tag size="small" :type="row.enabled ? 'success' : 'danger'">
              {{ row.enabled ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="rag_namespace" label="Namespace" show-overflow-tooltip />
        <el-table-column label="MCP 工具" width="200">
          <template #default="{ row }">
            <span v-if="!row.mcp_tools?.length" class="muted">未绑定</span>
            <el-tag v-for="t in row.mcp_tools" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { TraceUsageItem } from '@/types'
import {
  apiListAgents,
  apiListBases,
  apiListCustomTools,
  apiListInquiries,
  apiListMcpServers,
  apiListTickets,
  apiTraceUsage,
} from '@/api'
import type { AgentInfo, KnowledgeBase, MCPServer, CustomToolSpec } from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const agents = ref<AgentInfo[]>([])
const bases = ref<KnowledgeBase[]>([])
const mcpServers = ref<MCPServer[]>([])
const customTools = ref<CustomToolSpec[]>([])
const openTicketCount = ref(0)
const activeInquiryCount = ref(0)
const toolUsage = ref<TraceUsageItem[]>([])
const agentUsage = ref<TraceUsageItem[]>([])

function toBars(items: TraceUsageItem[]) {
  const max = Math.max(...items.map((i) => i.count), 1)
  return items.map((i) => ({ name: i.name, count: i.count, pct: Math.round((i.count / max) * 100) }))
}
const toolUsageBars = computed(() => toBars(toolUsage.value))
const agentUsageBars = computed(() => toBars(agentUsage.value))

const enabledAgentCount = computed(() => agents.value.filter((a) => a.enabled).length)
const activeBaseCount = computed(() => bases.value.filter((b) => b.is_active).length)
const enabledMcpCount = computed(() => mcpServers.value.filter((s) => s.enabled).length)
const enabledCustomCount = computed(() => customTools.value.filter((t) => t.enabled).length)
const mcpRefCount = computed(() => agents.value.reduce((n, a) => n + (a.mcp_tools?.length ?? 0), 0))
const totalToolCalls = computed(() => toolUsage.value.reduce((n, t) => n + t.count, 0))

onMounted(async () => {
  // 各接口独立降级：任一接口失败（如角色权限不足/后端异常）不应阻塞其余卡片渲染
  const results = await Promise.allSettled([
    apiListAgents(),
    apiListBases(),
    apiListMcpServers(),
    apiListCustomTools(),
    apiListTickets({ status: 'open' }, 1, 1),
    apiListInquiries({ status: 'active', page: 1, page_size: 1 }),
    apiTraceUsage('tool_names', 7),
    apiTraceUsage('agent_names', 7),
  ])
  const [a, b, m, c, tk, iq, tu, au] = results
  if (a.status === 'fulfilled') agents.value = a.value
  if (b.status === 'fulfilled') bases.value = b.value
  if (m.status === 'fulfilled') mcpServers.value = m.value
  if (c.status === 'fulfilled') customTools.value = c.value
  if (tk.status === 'fulfilled') openTicketCount.value = tk.value.total
  if (iq.status === 'fulfilled') activeInquiryCount.value = iq.value.total
  if (tu.status === 'fulfilled') toolUsage.value = tu.value.slice(0, 5)
  if (au.status === 'fulfilled') agentUsage.value = au.value.slice(0, 5)
})
</script>

<style scoped>
.suffix {
  font-size: 13px;
  color: var(--cs-text-3);
}

/* 排行榜：细轨道 + 品牌色填充 */
.bar-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.bar-label {
  width: 130px;
  font-size: 13px;
  color: var(--cs-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  flex: 1;
  height: 8px;
  background: var(--cs-border-light);
  border-radius: 4px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: var(--cs-brand);
  border-radius: 4px;
}
.bar-count {
  width: 40px;
  text-align: right;
  font-size: 13px;
  color: var(--cs-text-2);
}
</style>
