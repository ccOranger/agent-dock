<template>
  <div class="page">
    <PageHeader
      title="工单管理"
      description="客户问题沉淀成的工单：按状态、紧急度与时间筛选，抽屉内可改状态并补录处理进展。"
      :count="total"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="reload" @reset="reset">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px">
          <el-option v-for="(label, val) in TICKET_STATUS_LABELS" :key="val" :label="label" :value="val" />
        </el-select>
        <el-select v-model="filters.urgency" placeholder="紧急度" clearable style="width: 120px">
          <el-option v-for="(label, val) in TICKET_URGENCY_LABELS" :key="val" :label="label" :value="val" />
        </el-select>
        <el-input v-model="filters.category" placeholder="分类（如 it_network / logistics）" clearable style="width: 220px" />
        <el-input v-model="filters.keyword" placeholder="工单号 / 内容关键字" clearable style="width: 220px" />
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

      <el-table :data="rows" v-loading="loading" style="margin-top: 16px">
        <el-table-column prop="ticket_no" label="工单号" width="150">
          <template #default="{ row }"><span class="mono">{{ row.ticket_no }}</span></template>
        </el-table-column>
        <el-table-column prop="content" label="问题描述" min-width="220" show-overflow-tooltip />
        <el-table-column prop="category" label="分类" width="120" />
        <el-table-column prop="urgency" label="紧急度" width="90">
          <template #default="{ row }">
            <el-tag :type="urgencyType(row.urgency)" size="small">{{ urgencyLabel(row.urgency) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="update_count" label="补录数" width="80" align="right" />
        <el-table-column prop="created_at" label="创建时间" width="180">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row.ticket_no)">查看</el-button>
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

    <el-drawer v-model="drawerVisible" :title="`工单 ${current?.ticket_no || ''}`" size="45%">
      <div v-loading="detailLoading" class="detail">
        <el-descriptions :column="1" border v-if="current">
          <el-descriptions-item label="状态">
            <el-select v-model="nextStatus" size="small" style="width: 140px" @change="onStatusChange">
              <el-option v-for="(label, val) in TICKET_STATUS_LABELS" :key="val" :label="label" :value="val" />
            </el-select>
          </el-descriptions-item>
          <el-descriptions-item label="分类">{{ current.category }}</el-descriptions-item>
          <el-descriptions-item label="紧急度">{{ urgencyLabel(current.urgency) }}</el-descriptions-item>
          <el-descriptions-item label="用户 ID">
            <span class="mono">{{ current.user_id || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="问题描述">{{ current.content }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ fmt(current.created_at) }}</el-descriptions-item>
        </el-descriptions>

        <div class="section-title">补录历史</div>
        <div class="timeline">
          <div v-for="(u, i) in current?.updates" :key="i" class="upd">
            <div class="upd-meta">{{ fmt(u.at) }}<span v-if="u.action" class="act">（{{ u.action }}）</span></div>
            <div class="upd-content">{{ u.content }}</div>
          </div>
          <el-empty v-if="current && !current.updates.length" description="暂无补录" :image-size="60" />
        </div>

        <div>
          <el-input
            v-model="noteText"
            type="textarea"
            :rows="2"
            placeholder="补录一条信息（资产编号 / 报错信息 / 处理进展…）"
          />
          <el-button type="primary" :loading="noteSaving" @click="onAddNote" style="margin-top: 8px">
            保存补录
          </el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { apiAddTicketNote, apiListTickets, apiTicketDetail, apiUpdateTicketStatus } from '@/api'
import type { TicketFilters } from '@/api'
import { usePagedList } from '@/composables/usePagedList'
import type { TicketDetail, TicketSummary } from '@/types'
import { TICKET_STATUS_LABELS, TICKET_URGENCY_LABELS } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'
}
function statusType(s: string) {
  return ({ open: 'warning', in_progress: 'primary', escalated: 'danger', resolved: 'success', closed: 'info' } as any)[s] ?? 'info'
}
function urgencyType(u: string) {
  return ({ low: 'info', medium: 'warning', high: 'danger' } as any)[u] ?? 'info'
}
function urgencyLabel(u: string) {
  return TICKET_URGENCY_LABELS[u as keyof typeof TICKET_URGENCY_LABELS] ?? u
}
function statusLabel(s: string) {
  return TICKET_STATUS_LABELS[s as keyof typeof TICKET_STATUS_LABELS] ?? s
}

const { rows, total, page, pageSize, loading, load: fetchPage, reload: reloadPage } =
  usePagedList<TicketSummary>((params, p, size) => apiListTickets(params as TicketFilters, p, size))

const filters = ref<{ status?: string; urgency?: string; category?: string; keyword?: string }>({})
const dateRange = ref<[string, string] | null>(null)

const drawerVisible = ref(false)
const detailLoading = ref(false)
const current = ref<TicketDetail | null>(null)
const nextStatus = ref<string>('')
const noteText = ref('')
const noteSaving = ref(false)

function buildParams() {
  const p: Record<string, unknown> = { ...filters.value }
  if (dateRange.value && dateRange.value.length === 2) {
    p.date_from = dateRange.value[0]
    p.date_to = dateRange.value[1]
  }
  return p
}

function load() {
  return fetchPage(buildParams())
}

function reload() {
  return reloadPage(buildParams())
}

function reset() {
  filters.value = {}
  dateRange.value = null
  reload()
}

async function openDetail(ticketNo: string) {
  drawerVisible.value = true
  detailLoading.value = true
  current.value = null
  noteText.value = ''
  try {
    current.value = await apiTicketDetail(ticketNo)
    nextStatus.value = current.value.status
  } catch (e: any) {
    ElMessage.error(e?.message || '加载工单失败')
  } finally {
    detailLoading.value = false
  }
}

async function onStatusChange(val: string) {
  if (!current.value) return
  try {
    await apiUpdateTicketStatus(current.value.ticket_no, val)
    ElMessage.success('状态已更新')
    current.value = await apiTicketDetail(current.value.ticket_no)
    load()
  } catch (e: any) {
    ElMessage.error(e?.message || '更新失败')
    nextStatus.value = current.value.status
  }
}

async function onAddNote() {
  if (!current.value || !noteText.value.trim()) {
    ElMessage.warning('请输入补录内容')
    return
  }
  noteSaving.value = true
  try {
    await apiAddTicketNote(current.value.ticket_no, noteText.value.trim())
    noteText.value = ''
    ElMessage.success('已补录')
    current.value = await apiTicketDetail(current.value.ticket_no)
    load()
  } catch (e: any) {
    ElMessage.error(e?.message || '补录失败')
  } finally {
    noteSaving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

.detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--cs-text-1);
}

.timeline {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.upd {
  padding: 8px 12px;
  border-radius: var(--cs-radius-sm);
  background: var(--cs-surface-sub);
  border: 1px solid var(--cs-border-light);
}

.upd-meta {
  font-size: 12px;
  color: var(--cs-text-3);
  margin-bottom: 4px;
}

.upd .act {
  color: var(--cs-warning);
}

.upd-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--cs-text-1);
}
</style>
