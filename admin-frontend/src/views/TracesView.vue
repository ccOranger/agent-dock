<template>
  <div class="page">
    <PageHeader
      title="请求链路"
      description="逐次请求的意图、RAG、记忆、工具与耗时归因；行可点开查看时间线。"
      :count="total"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="reload" @reset="reset">
        <el-select v-model="filters.outcome" placeholder="结局" clearable style="width: 120px">
          <el-option label="已应答" value="answered" />
          <el-option label="转人工" value="handover" />
          <el-option label="异常" value="error" />
        </el-select>
        <el-select v-model="filters.scene_code" placeholder="场景" clearable style="width: 150px">
          <el-option
            v-for="scene in scenes"
            :key="scene.code"
            :label="scene.display_name"
            :value="scene.code"
          />
        </el-select>
        <el-select v-model="filters.agent_name" placeholder="业务 Agent" clearable filterable style="width: 170px">
          <el-option v-for="a in agents" :key="a.name" :label="a.display_name || a.name" :value="a.name" />
        </el-select>
        <el-input v-model="filters.tool_name" placeholder="工具名" clearable style="width: 150px" />
        <el-input v-model="filters.mcp_server" placeholder="MCP 服务名" clearable style="width: 150px" />
        <el-input v-model="filters.session_id" placeholder="会话 ID / 链路 ID" clearable style="width: 230px" />
        <el-input-number v-model="filters.min_latency_ms" :min="0" :step="500" placeholder="最小耗时" style="width: 140px" />
        <el-checkbox v-model="filters.has_error">仅看异常</el-checkbox>
        <el-date-picker
          v-model="dateRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 340px"
        />
      </FilterBar>

      <el-alert
        v-if="route.query.session_id"
        type="info"
        show-icon
        :closable="false"
        class="tip"
      >
        当前按 ID 过滤：{{ route.query.session_id }}
        <el-button link type="primary" @click="clearSession">清除过滤</el-button>
      </el-alert>

      <el-table :data="rows" v-loading="loading" style="margin-top: 12px" @row-click="openDetail">
        <el-table-column prop="created_at" label="时间" width="160">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="场景" width="130">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ row.scene_code || '默认' }}</el-tag>
            <el-tag v-if="row.router_skipped" size="small" class="chip">直连</el-tag>
            <el-tag v-else-if="row.router_fallback" size="small" type="warning" class="chip">兜底</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="outcome" label="结局" width="90">
          <template #default="{ row }">
            <el-tag :type="outcomeTag(row.outcome).type" size="small">{{ outcomeTag(row.outcome).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="Agent" min-width="170">
          <template #default="{ row }">
            <el-tag v-for="n in row.agent_names" :key="n" size="small" class="chip">{{ n }}</el-tag>
            <span v-if="!row.agent_names?.length" class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="工具" min-width="170">
          <template #default="{ row }">
            <el-tag v-for="n in row.tool_names" :key="n" size="small" type="success" class="chip">{{ n }}</el-tag>
            <span v-if="!row.tool_names?.length" class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="MCP" min-width="140">
          <template #default="{ row }">
            <el-tag v-for="n in row.mcp_server_names" :key="n" size="small" type="warning" class="chip">{{ n }}</el-tag>
            <span v-if="!row.mcp_server_names?.length" class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="意图/RAG/记忆" width="120">
          <template #default="{ row }">
            <span class="mono">{{ row.intent_count }}</span> /
            <span class="mono">{{ row.rag_chunk_count }}</span> /
            <span class="mono">{{ row.memory_hit_count }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="total_latency_ms" label="耗时" width="90" sortable>
          <template #default="{ row }">{{ fmtMs(row.total_latency_ms) }}</template>
        </el-table-column>
        <el-table-column prop="confidence" label="置信" width="80">
          <template #default="{ row }">{{ fmtConf(row.confidence) }}</template>
        </el-table-column>
        <el-table-column label="异常" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.tool_error_count" type="danger" size="small">工具×{{ row.tool_error_count }}</el-tag>
            <el-tag v-else-if="row.error_code" type="danger" size="small">{{ row.error_code }}</el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="150" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click.stop="openDetail(row)">链路详情</el-button>
            <el-button link @click.stop="gotoInquiry(row)">咨询</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, prev, pager, next, sizes"
        :total="total"
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="load"
        @size-change="load"
      />
    </el-card>

    <el-drawer v-model="drawerVisible" :title="drawerTitle" size="60%">
      <TraceTimeline :detail="detail" :loading="detailLoading" />
      <div class="drawer-actions" v-if="detail">
        <el-button link type="primary" @click="copyTraceId">复制 trace_id</el-button>
        <el-button link type="primary" @click="gotoInquiry(detail)">跳到该会话的咨询记录</el-button>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { apiListAgents, apiListTraces, apiSceneOptions, apiTraceDetail } from '@/api'
import type { AgentInfo, RequestTraceDetail, RequestTraceSummary, SceneOption } from '@/types'
import TraceTimeline from '@/components/TraceTimeline.vue'
import { usePagedList } from '@/composables/usePagedList'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'

const route = useRoute()
const router = useRouter()

const { rows, total, page, pageSize, loading, load: fetchPage, reload: reloadPage } =
  usePagedList<RequestTraceSummary>((params, p, size) =>
    apiListTraces(params, p, size).catch((e: any) => {
      ElMessage.error(e?.message || '加载链路失败')
      throw e
    }),
  )
const agents = ref<AgentInfo[]>([])
const scenes = ref<SceneOption[]>([])

const filters = ref<{
  outcome?: string
  scene_code?: string
  agent_name?: string
  tool_name?: string
  mcp_server?: string
  session_id?: string
  min_latency_ms?: number
  has_error: boolean
}>({ has_error: false })
const dateRange = ref<[string, string] | null>(null)

const drawerVisible = ref(false)
const detailLoading = ref(false)
const detail = ref<RequestTraceDetail | null>(null)
const drawerTitle = ref('链路详情')

function fmt(v?: string | null) {
  return v ? dayjs(v).format('MM-DD HH:mm:ss') : '-'
}
function fmtMs(v?: number | null) {
  if (v === null || v === undefined) return '-'
  return v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v}ms`
}
function fmtConf(v?: number | null) {
  return v === null || v === undefined ? '-' : v.toFixed(2)
}
function outcomeTag(outcome?: string | null) {
  if (outcome === 'handover') return { type: 'warning' as const, text: '转人工' }
  if (outcome === 'error') return { type: 'danger' as const, text: '异常' }
  if (outcome === 'answered') return { type: 'success' as const, text: '已应答' }
  return { type: 'info' as const, text: outcome || '未知' }
}

function buildParams() {
  const p: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(filters.value)) {
    if (v === undefined || v === null || v === '' || v === false) continue
    p[k] = v
  }
  if (dateRange.value && dateRange.value.length === 2) {
    p.date_from = dateRange.value[0]
    p.date_to = dateRange.value[1]
  }
  return p
}

async function load() {
  return fetchPage(buildParams())
}

function reload() {
  return reloadPage(buildParams())
}

function reset() {
  filters.value = { has_error: false }
  dateRange.value = null
  router.replace({ path: '/traces' })
  reload()
}

async function openDetail(row: RequestTraceSummary | RequestTraceDetail) {
  drawerVisible.value = true
  detailLoading.value = true
  detail.value = null
  drawerTitle.value = `链路 ${row.trace_id}`
  try {
    detail.value = await apiTraceDetail(row.id || row.trace_id)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载链路详情失败')
  } finally {
    detailLoading.value = false
  }
}

function gotoInquiry(row: RequestTraceSummary | RequestTraceDetail) {
  if (!row.session_id) {
    ElMessage.info('该链路没有会话 ID')
    return
  }
  router.push({ path: '/inquiries', query: { session_id: row.session_id } })
}

function clearSession() {
  filters.value.session_id = undefined
  router.replace({ path: '/traces' })
  reload()
}

function copyTraceId() {
  if (!detail.value) return
  navigator.clipboard?.writeText(detail.value.trace_id)
  ElMessage.success('已复制 trace_id，可在服务端日志中直接检索')
}

onMounted(() => {
  // 支持从咨询详情带参跳转：/traces?session_id=xxx 或 /traces?trace=xxx
  const sid = route.query.session_id as string | undefined
  if (sid) filters.value.session_id = sid
  load()
  const trace = route.query.trace as string | undefined
  if (trace) openDetail({ id: trace, trace_id: trace } as RequestTraceSummary)
  apiListAgents()
    .then((list) => (agents.value = list))
    .catch(() => {
      /* Agent 下拉仅 admin 可用，agent 角色忽略 */
    })
  apiSceneOptions()
    .then((list) => (scenes.value = list))
    .catch(() => {
      /* 场景下拉失败不影响按会话/Agent 归因 */
    })
})
</script>

<style scoped>
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.chip {
  margin-right: 4px;
}

/* 表格内密排数字：等宽 + 统一字号 */
.mono {
  font-size: 12px;
}

.tip {
  margin-top: 12px;
}

.drawer-actions {
  margin-top: 16px;
  display: flex;
  gap: 8px;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
