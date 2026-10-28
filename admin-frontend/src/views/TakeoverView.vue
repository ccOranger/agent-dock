<template>
  <div class="takeover">
    <!-- 左：待接入 / 已接入队列 -->
    <el-card class="queue-card" shadow="never">
      <template #header>
        <div class="head">
          <b>接管队列</b>
          <el-button size="small" text :loading="queueLoading" @click="loadQueue()">刷新</el-button>
        </div>
      </template>

      <el-tabs v-model="activeTab" @tab-change="onTabChange">
        <el-tab-pane name="queued">
          <template #label>
            <span>待接入</span>
            <el-badge v-if="queuedCount > 0" :value="queuedCount" class="tab-badge" />
          </template>
        </el-tab-pane>
        <el-tab-pane label="我已接入" name="handling" />
      </el-tabs>

      <div v-loading="queueLoading" class="queue-list">
        <div
          v-for="row in rows"
          :key="row.id"
          class="queue-item"
          :class="{ active: selectedId === row.id }"
          @click="selectSession(row)"
        >
          <div class="qi-top">
            <span class="qi-title">{{ row.title || '未命名会话' }}</span>
            <el-tag :type="row.status === 'human_handling' ? 'success' : 'warning'" size="small">
              {{ row.status === 'human_handling' ? '已接入' : '待接入' }}
            </el-tag>
          </div>
          <div class="qi-sub">
            <span>{{ customerLabel(row) }}</span>
            <el-tag v-if="row.scene_code" size="small" type="info">{{ row.scene_code }}</el-tag>
            <span class="muted">{{ row.message_count }} 条</span>
          </div>
          <div class="qi-foot">
            <span class="muted">等待 {{ waitingText(row.updated_at) }}</span>
            <el-button
              v-if="row.status === 'handover'"
              type="primary"
              size="small"
              link
              @click.stop="onClaim(row)"
            >
              接入
            </el-button>
            <span v-else-if="row.assigned_agent_name" class="muted">
              {{ row.assigned_agent_name }}
            </span>
          </div>
        </div>
        <el-empty v-if="!queueLoading && !rows.length" description="暂无待接入会话" />
      </div>

      <el-pagination
        v-if="total > pageSize"
        style="margin-top: 12px; justify-content: center"
        layout="prev, pager, next"
        :total="total"
        :page-size="pageSize"
        v-model:current-page="page"
        @current-change="loadQueue"
      />
    </el-card>

    <!-- 右：会话工作区 -->
    <el-card class="work-card" shadow="never">
      <template #header>
        <div class="head">
          <div class="work-title">
            <b>{{ detail?.session.title || '选择一个会话开始接入' }}</b>
            <el-tag v-if="detail" :type="detail.session.status === 'human_handling' ? 'success' : 'warning'" size="small">
              {{ detail.session.status === 'human_handling' ? '人工接管中' : '待接入' }}
            </el-tag>
            <span v-if="detail?.session.assigned_agent?.name" class="muted">
              接手：{{ detail.session.assigned_agent.name }}
            </span>
          </div>
          <div v-if="detail" class="work-actions">
            <el-button size="small" @click="refreshDetail(true)">刷新</el-button>
            <el-button size="small" @click="onRelease('bot')" :disabled="!canManage">交还机器人</el-button>
            <el-button size="small" type="danger" plain @click="onRelease('close')" :disabled="!canManage">
              结束会话
            </el-button>
          </div>
        </div>
      </template>

      <div v-if="!detail" class="placeholder">
        <el-empty description="左侧队列选择会话；未接入的可直接「接入」" />
      </div>

      <template v-else>
        <div v-loading="detailLoading" class="conversation" ref="convRef">
          <div v-for="m in messages" :key="m.id" class="msg" :class="msgClass(m)">
            <div class="msg-meta">
              <el-tag size="small" :type="msgTagType(m)">{{ msgLabel(m) }}</el-tag>
              <span class="time">{{ fmt(m.created_at) }}</span>
            </div>
            <div class="msg-content">{{ m.content }}</div>
          </div>
          <el-empty v-if="!messages.length" description="暂无消息" />
        </div>

        <!-- 接入 + 回复区 -->
        <div class="composer">
          <div v-if="detail.session.status === 'handover'" class="claim-bar">
            <el-alert type="warning" :closable="false" show-icon title="该会话尚未接入，接入后客户消息只会转给你、机器人不再应答。" />
            <el-button type="primary" @click="onClaimById(detail.session.id)">立即接入</el-button>
          </div>
          <div v-else-if="!canReply" class="claim-bar">
            <el-alert type="info" :closable="false" show-icon title="该会话由其他坐席接手，仅接手人或管理员可回复（管理员可调岗释放）。" />
          </div>
          <div v-else class="reply-row">
            <el-input
              v-model="replyText"
              type="textarea"
              :rows="2"
              resize="none"
              placeholder="以人工客服身份回复客户…"
              @keydown.enter.exact.prevent="onSend"
            />
            <el-button type="primary" :loading="sending" @click="onSend">发送</el-button>
          </div>
        </div>
      </template>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  apiClaimTakeover,
  apiListTakeovers,
  apiReleaseTakeover,
  apiSendTakeoverMessage,
  apiTakeoverDetail,
} from '@/api'
import type { TakeoverDetail, TakeoverMessage, TakeoverSession } from '@/types'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const myId = computed(() => auth.user?.id || '')
const isAdmin = computed(() => auth.user?.role === 'admin')

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '-'
}
function waitingText(v?: string | null) {
  if (!v) return '-'
  const mins = dayjs().diff(dayjs(v), 'minute')
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟`
  const hrs = Math.floor(mins / 60)
  return hrs < 24 ? `${hrs} 小时` : `${Math.floor(hrs / 24)} 天`
}
function customerLabel(row: TakeoverSession) {
  if (row.display_name || row.username) return row.display_name || (row.username as string)
  return row.user_id ? '客户' : `访客 ${(row.visitor_id || '').slice(0, 8)}`
}

// ===== 队列 =====
const activeTab = ref<'queued' | 'handling'>('queued')
const rows = ref<TakeoverSession[]>([])
const total = ref(0)
const queuedCount = ref(0)
const page = ref(1)
const pageSize = 20
const queueLoading = ref(false)

async function loadQueue(silent = false) {
  if (!silent) queueLoading.value = true
  try {
    const data = await apiListTakeovers({
      status: activeTab.value,
      page: page.value,
      page_size: pageSize,
    })
    rows.value = data.items
    total.value = data.total
    if (activeTab.value === 'queued') queuedCount.value = data.total
  } catch (e: any) {
    if (!silent) ElMessage.error(e?.message || '加载队列失败')
  } finally {
    queueLoading.value = false
  }
}

// 待接入数量单独静默刷新（供「待接入」页签角标，即便当前停在「我已接入」）
async function refreshQueuedCount() {
  try {
    const data = await apiListTakeovers({ status: 'queued', page: 1, page_size: 1 })
    queuedCount.value = data.total
  } catch {
    /* 角标失败不影响主流程 */
  }
}

function onTabChange() {
  page.value = 1
  loadQueue()
}

// ===== 详情 + 增量轮询 =====
const selectedId = ref('')
const detail = ref<TakeoverDetail | null>(null)
const messages = ref<TakeoverMessage[]>([])
const detailLoading = ref(false)
const sending = ref(false)
const replyText = ref('')
const convRef = ref<HTMLElement | null>(null)

const isHandling = computed(() => detail.value?.session.status === 'human_handling')
const isMine = computed(() => detail.value?.session.assigned_agent?.id === myId.value)
const canReply = computed(() => !!detail.value && isHandling.value && (isAdmin.value || isMine.value))
// 释放/交还：接手人或管理员可调岗
const canManage = computed(() => !!detail.value && (isAdmin.value || isMine.value))

function selectSession(row: TakeoverSession) {
  selectedId.value = row.id
  loadDetail(row.id)
}

async function loadDetail(sessionId: string) {
  detailLoading.value = true
  try {
    const data = await apiTakeoverDetail(sessionId)
    detail.value = data
    messages.value = dedupe(data.messages)
    scrollToBottom()
  } catch (e: any) {
    ElMessage.error(e?.message || '加载会话失败')
  } finally {
    detailLoading.value = false
  }
}

// after_id 增量：只追加新消息，按 id 去重（同刻消息后端允许重复返回）
async function refreshDetail(manual = false) {
  if (!selectedId.value || !detail.value) return
  const lastId = messages.value.length ? messages.value[messages.value.length - 1].id : undefined
  try {
    if (manual || !lastId) {
      const data = await apiTakeoverDetail(selectedId.value)
      detail.value = data
      messages.value = dedupe(data.messages)
    } else {
      const data = await apiTakeoverDetail(selectedId.value, lastId)
      if (data.messages.length) {
        messages.value = dedupe([...messages.value, ...data.messages])
      }
      detail.value.session.status = data.session.status
      detail.value.session.assigned_agent = data.session.assigned_agent
    }
    scrollToBottom()
  } catch (e: any) {
    if (manual) ElMessage.error(e?.message || '刷新失败')
  }
}

function dedupe(list: TakeoverMessage[]): TakeoverMessage[] {
  const map = new Map<string, TakeoverMessage>()
  for (const m of list) map.set(m.id, m)
  return [...map.values()].sort((a, b) => a.created_at.localeCompare(b.created_at))
}

function scrollToBottom() {
  nextTick(() => {
    const el = convRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function msgClass(m: TakeoverMessage) {
  if (m.role === 'user') return 'customer'
  if (m.sender_type === 'staff' || m.routed_to === 'staff') return 'staff'
  return 'bot'
}
function msgLabel(m: TakeoverMessage) {
  if (m.role === 'user') return '客户'
  if (m.sender_type === 'staff' || m.routed_to === 'staff') return `人工${m.staff_name ? ' · ' + m.staff_name : ''}`
  return '机器人'
}
function msgTagType(m: TakeoverMessage) {
  if (m.role === 'user') return 'primary'
  if (m.sender_type === 'staff' || m.routed_to === 'staff') return 'success'
  return 'info'
}

// ===== 操作 =====
async function onClaim(row: TakeoverSession) {
  await doClaim(row.id)
}
async function onClaimById(sessionId: string) {
  await doClaim(sessionId)
}
async function doClaim(sessionId: string) {
  try {
    const res = await apiClaimTakeover(sessionId)
    ElMessage.success('已接入，机器人已让位')
    selectedId.value = sessionId
    await loadDetail(sessionId)
    void res
    loadQueue(true)
    refreshQueuedCount()
  } catch (e: any) {
    // 他人已接入：后端 BizError 归一化为 ApiError，code=3002 / status=409
    if (e?.code === 3002 || e?.status === 409) ElMessage.warning('该会话已被其他坐席接入')
    else ElMessage.error(e?.message || '接入失败')
    loadQueue(true)
  }
}

async function onSend() {
  const content = replyText.value.trim()
  if (!content || !selectedId.value) return
  sending.value = true
  try {
    await apiSendTakeoverMessage(selectedId.value, content)
    replyText.value = ''
    await refreshDetail(true)
  } catch (e: any) {
    ElMessage.error(e?.message || '发送失败')
  } finally {
    sending.value = false
  }
}

async function onRelease(to: 'bot' | 'close') {
  if (!selectedId.value) return
  const tip = to === 'bot' ? '交还机器人后，客户后续消息将由机器人应答。确认交还？' : '结束会话后不可再接入，确认结束？'
  try {
    await ElMessageBox.confirm(tip, '释放接管', { type: 'warning' })
  } catch {
    return
  }
  try {
    await apiReleaseTakeover(selectedId.value, to)
    ElMessage.success(to === 'bot' ? '已交还机器人' : '会话已结束')
    detail.value = null
    messages.value = []
    selectedId.value = ''
    loadQueue(true)
    refreshQueuedCount()
  } catch (e: any) {
    ElMessage.error(e?.message || '释放失败')
  }
}

// ===== 轮询 =====
let queueTimer: ReturnType<typeof setInterval> | null = null
let detailTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  loadQueue()
  refreshQueuedCount()
  queueTimer = setInterval(() => loadQueue(true), 8000)
  detailTimer = setInterval(() => refreshDetail(), 4000)
})

onBeforeUnmount(() => {
  if (queueTimer) clearInterval(queueTimer)
  if (detailTimer) clearInterval(detailTimer)
})
</script>

<style scoped>
.takeover {
  display: grid;
  grid-template-columns: minmax(320px, 380px) 1fr;
  gap: 16px;
  align-items: start;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.tab-badge {
  margin-left: 6px;
}
.queue-list {
  max-height: calc(100vh - 260px);
  overflow-y: auto;
}
.queue-item {
  padding: 10px 12px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.15s;
}
.queue-item:hover {
  border-color: #c6e2ff;
  background: #f5faff;
}
.queue-item.active {
  border-color: #409eff;
  background: #ecf5ff;
}
.qi-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.qi-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.qi-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 13px;
  color: #606266;
}
.qi-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 6px;
  font-size: 12px;
}
.muted {
  color: #909399;
  font-size: 12px;
}
.work-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.work-actions {
  display: flex;
  gap: 8px;
}
.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 320px;
}
.conversation {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: calc(100vh - 320px);
  overflow-y: auto;
  padding-bottom: 8px;
}
.msg {
  max-width: 80%;
  padding: 8px 12px;
  border-radius: 8px;
  background: #f4f4f5;
}
/* 坐席工作台视角：客户在左、我方（人工/机器人）在右 */
.msg.customer {
  align-self: flex-start;
  background: #ecf5ff;
}
.msg.staff {
  align-self: flex-end;
  background: #f0f9eb;
}
.msg.bot {
  align-self: flex-end;
  background: #f4f4f5;
}
.msg.staff .msg-meta,
.msg.bot .msg-meta {
  justify-content: flex-end;
}
.msg-meta {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
  font-size: 12px;
  color: #909399;
}
.msg-content {
  white-space: pre-wrap;
  word-break: break-word;
  color: #303133;
}
.composer {
  border-top: 1px solid #ebeef5;
  padding-top: 12px;
  margin-top: 8px;
}
.claim-bar {
  display: flex;
  align-items: center;
  gap: 12px;
}
.claim-bar :deep(.el-alert) {
  flex: 1;
}
.reply-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
</style>
