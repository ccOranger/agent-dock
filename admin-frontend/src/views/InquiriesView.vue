<template>
  <div class="page">
    <PageHeader title="咨询管理" :count="total">
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="success" plain :loading="exporting" @click="onExport">导出 CSV</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="reload" @reset="reset">
        <el-select v-model="filters.status" placeholder="状态" clearable style="width: 130px">
          <el-option label="进行中" value="active" />
          <el-option label="已关闭" value="closed" />
          <el-option label="转人工" value="handover" />
        </el-select>
        <el-select v-model="filters.scene_code" placeholder="场景" clearable style="width: 150px">
          <el-option
            v-for="scene in scenes"
            :key="scene.code"
            :label="scene.display_name"
            :value="scene.code"
          />
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
      </FilterBar>

      <el-table :data="rows" v-loading="loading" style="margin-top: 16px">
        <el-table-column prop="id" label="会话 ID" width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span class="mono">{{ row.id }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_id" label="客户 ID" width="200" show-overflow-tooltip>
          <template #default="{ row }">
            <span v-if="row.user_id" class="mono">{{ row.user_id }}</span>
            <span v-else class="muted">访客 {{ (row.visitor_id || '').slice(0, 8) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="display_name" label="客户" width="140">
          <template #default="{ row }">
            <span v-if="row.display_name || row.username">{{ row.display_name || row.username }}</span>
            <el-tag v-else size="small" type="info">匿名访客</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="场景" width="128">
          <template #default="{ row }">
            <el-tag size="small" type="info">{{ sceneName(row.scene_code) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="会话标题" min-width="160" />
        <el-table-column prop="status" label="状态" width="96">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="last_route_to" label="路由 Agent" width="152" />
        <el-table-column prop="message_count" label="消息数" width="96" />
        <el-table-column prop="created_at" label="创建时间" width="168">
          <template #default="{ row }">{{ fmt(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="152" fixed="right">
          <template #default="{ row }">
            <el-button type="primary" link @click="openDetail(row)">查看</el-button>
            <el-button link @click="openMemory(row)">记忆</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <TableState :error="error" :loading="loading" @retry="reload" />
        </template>
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

    <DetailDrawer v-model="drawerVisible" :title="drawerTitle" size="45%">
      <div v-if="current" class="detail-meta">
        <el-tag size="small" type="info">{{ sceneName(current.session.scene_code) }}</el-tag>
        <span class="muted" v-if="current.session.scene_code">{{ current.session.scene_code }}</span>
        <span class="muted mono-id">会话 {{ current.session.id }}</span>
      </div>
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
            <span class="time">{{ fmt(m.created_at) }}</span>
            <el-button v-if="m.trace_id" type="primary" link size="small" @click="openTrace(m)">
              查看链路
            </el-button>
          </div>
          <div class="msg-content">{{ m.content }}</div>
        </div>
        <el-empty v-if="current && !current.messages.length" description="暂无消息" />
      </div>
    </DetailDrawer>
  </div>
</template>

<script setup lang="ts">
import DetailDrawer from '@/components/DetailDrawer.vue'
import TableState from '@/components/TableState.vue'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { apiExportInquiries, apiInquiryDetail, apiListAgents, apiListInquiries, apiSceneOptions } from '@/api'
import type { AgentInfo, ChatMessage, InquiryDetail, InquirySession, SceneOption } from '@/types'
import { usePagedList } from '@/composables/usePagedList'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'

const route = useRoute()
const router = useRouter()

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'
}

const { rows, total, page, pageSize, loading, error, load: fetchPage, reload: reloadPage } =
  usePagedList<InquirySession>((params, p, size) =>
    apiListInquiries({ ...params, page: p, page_size: size } as any),
  )
const agents = ref<AgentInfo[]>([])
const scenes = ref<SceneOption[]>([])
const exporting = ref(false)

const filters = ref<{ status?: string; scene_code?: string; last_route_to?: string; user_id?: string }>({})
const dateRange = ref<[string, string] | null>(null)

const drawerVisible = ref(false)
const detailLoading = ref(false)
const current = ref<InquiryDetail | null>(null)

const drawerTitle = computed(() => {
  const title = current.value?.session.title || '对话详情'
  return title
})

function sceneName(code?: string | null) {
  if (!code) return '默认'
  return scenes.value.find((s) => s.code === code)?.display_name || code
}

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

// 链路未采集（被采样丢弃或开关关闭）时不展示入口
function openTrace(m: ChatMessage) {
  if (!m.trace_id) return
  router.push({ path: '/traces', query: { trace: m.trace_id, session_id: current.value?.session.id } })
}

/** 跳记忆查看：登录客户用 user_id，匿名嵌入访客只能用 visitor: 前缀 */
function openMemory(row: InquirySession) {
  const subject = row.user_id || (row.visitor_id ? `visitor:${row.visitor_id}` : '')
  if (!subject) {
    ElMessage.warning('该会话没有客户标识，无法查看记忆')
    return
  }
  router.push({ path: `/memory/${encodeURIComponent(subject)}` })
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
  // 从链路页「跳到咨询记录」带过来的会话：直接展开该会话对话
  const sid = route.query.session_id as string | undefined
  if (sid) openDetail({ id: sid } as InquirySession)
  try {
    agents.value = await apiListAgents()
  } catch {
    /* agents 仅 admin 可见，agent 角色忽略 */
  }
  apiSceneOptions()
    .then((list) => (scenes.value = list))
    .catch(() => {
      /* 场景下拉失败不影响咨询列表 */
    })
})
</script>

<style scoped>
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}

/* UUID 类长 ID：等宽 + 省略收尾，完整值由 show-overflow-tooltip 提示 */
.mono {
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

.detail-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  font-size: 12px;
}

.mono-id {
  margin-left: auto;
}

/* 对话气泡：用户靠右淡蓝，客服靠左淡灰 */
.msg {
  max-width: 85%;
  padding: 10px 12px;
  border-radius: var(--cs-radius-md);
  background: var(--cs-surface-sub);
  border: 1px solid var(--cs-border-light);
}

.msg.user {
  align-self: flex-end;
  background: var(--cs-brand-soft);
  border-color: var(--cs-brand-line);
}

.msg.assistant {
  align-self: flex-start;
}

.msg-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--cs-text-3);
}

.route {
  color: var(--cs-warning);
}

.msg-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--cs-text-1);
}
</style>
