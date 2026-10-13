<template>
  <div>
    <el-card>
      <template #header>
        <div class="head">
          <b>客户咨询信息</b>
          <el-button type="success" plain :loading="exporting" @click="onExport">导出 CSV</el-button>
        </div>
      </template>

      <div class="filters">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px">
          <el-option label="进行中" value="active" />
          <el-option label="已关闭" value="closed" />
          <el-option label="转人工" value="handover" />
        </el-select>
        <el-select v-model="filters.last_route_to" placeholder="路由 Agent" clearable style="width: 170px">
          <el-option v-for="a in agents" :key="a.name" :label="a.display_name || a.name" :value="a.name" />
        </el-select>
        <el-date-picker
          v-model="dateRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          value-format="YYYY-MM-DDTHH:mm:ss"
          style="width: 340px"
        />
        <el-input v-model="filters.user_id" placeholder="客户 ID（可选）" clearable style="width: 220px" />
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="reset">重置</el-button>
      </div>

      <el-table :data="rows" v-loading="loading" style="margin-top: 16px" @row-click="openDetail">
        <el-table-column prop="id" label="会话 ID" width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="客户 ID" width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ row.user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="客户" width="140">
          <template #default="{ row }">{{ row.display_name || row.username }}</template>
        </el-table-column>
        <el-table-column prop="title" label="会话标题" min-width="160" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_route_to" label="路由 Agent" width="150" />
        <el-table-column prop="message_count" label="消息数" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="200" />
      </el-table>

      <el-pagination
        style="margin-top: 16px; justify-content: flex-end"
        layout="total, prev, pager, next, sizes"
        :total="total"
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :page-sizes="[10, 20, 50, 100]"
        @current-change="load"
        @size-change="load"
      />
    </el-card>

    <el-drawer v-model="drawerVisible" :title="current?.session.title || '对话详情'" size="45%">
      <div v-loading="detailLoading" class="conversation">
        <div
          v-for="m in current?.messages"
          :key="m.id"
          class="msg"
          :class="m.role"
        >
          <div class="msg-meta">
            <el-tag size="small" :type="m.role === 'user' ? 'primary' : 'success'">{{ m.role }}</el-tag>
            <span v-if="m.routed_to" class="route">→ {{ m.routed_to }}</span>
            <span class="time">{{ m.created_at }}</span>
          </div>
          <div class="msg-content">{{ m.content }}</div>
        </div>
        <el-empty v-if="current && !current.messages.length" description="暂无消息" />
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiExportInquiries, apiInquiryDetail, apiListAgents, apiListInquiries } from '@/api'
import type { AgentInfo, InquiryDetail, InquirySession } from '@/types'

const rows = ref<InquirySession[]>([])
const agents = ref<AgentInfo[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const exporting = ref(false)

const filters = ref<{ status?: string; last_route_to?: string; user_id?: string }>({})
const dateRange = ref<[string, string] | null>(null)

const drawerVisible = ref(false)
const detailLoading = ref(false)
const current = ref<InquiryDetail | null>(null)

function statusType(s: string) {
  return ({ active: 'primary', closed: 'info', handover: 'warning' } as any)[s] ?? 'info'
}
function statusText(s: string) {
  return ({ active: '进行中', closed: '已关闭', handover: '转人工' } as any)[s] ?? s
}

function buildParams() {
  const p: Record<string, unknown> = { ...filters.value }
  if (dateRange.value && dateRange.value.length === 2) {
    p.date_from = dateRange.value[0]
    p.date_to = dateRange.value[1]
  }
  return p
}

async function load() {
  loading.value = true
  try {
    const data = await apiListInquiries({ ...buildParams(), page: page.value, page_size: pageSize.value } as any)
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

function reset() {
  filters.value = {}
  dateRange.value = null
  reload()
}

async function openDetail(row: InquirySession) {
  drawerVisible.value = true
  detailLoading.value = true
  current.value = null
  try {
    current.value = await apiInquiryDetail(row.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载对话失败')
  } finally {
    detailLoading.value = false
  }
}

async function onExport() {
  exporting.value = true
  try {
    const blob = await apiExportInquiries(buildParams())
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `inquiries_${Date.now()}.csv`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  load()
  try {
    agents.value = await apiListAgents()
  } catch {
    /* agents 仅 admin 可见，agent 角色忽略 */
  }
})
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filters {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.mono {
  font-family: Consolas, Menlo, monospace;
  font-size: 12px;
  color: #606266;
  /* 超长 UUID 以省略号收尾，完整值由 show-overflow-tooltip 提示 */
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.msg {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f4f4f5;
}
.msg.user {
  align-self: flex-end;
  background: #ecf5ff;
}
.msg.assistant {
  align-self: flex-start;
}
.msg-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
  font-size: 12px;
  color: #909399;
}
.route {
  color: #e6a23c;
}
.msg-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: #303133;
}
</style>
