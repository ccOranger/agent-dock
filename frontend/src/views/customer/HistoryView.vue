<template>
  <div class="history-page">
    <el-card>
      <template #header>
        <div class="head">
          <div class="head-left">
            <b>历史会话</b>
            <el-tag v-if="sceneCode" size="small" type="info">场景 {{ sceneCode }}</el-tag>
          </div>
          <el-button class="hist-btn" @click="backToChat">返回对话</el-button>
        </div>
      </template>
      <!-- 移动端：表格 6 列在手机上只能横向滚动，改为卡片列表 -->
      <div v-if="isMobile" v-loading="loading" class="session-cards">
        <div v-for="s in sessions" :key="s.id" class="session-card">
          <div class="sc-top">
            <span class="sc-title">{{ s.title || '未命名会话' }}</span>
            <el-tag size="small" :type="statusType(s.status)">
              {{ STATUS_LABELS[s.status] || s.status }}
            </el-tag>
          </div>
          <div class="sc-meta">
            <span>{{ dayjs(s.created_at).format('MM-DD HH:mm') }}</span>
            <span v-if="s.last_route_to">· {{ s.last_route_to }}</span>
          </div>
          <div class="sc-actions hist-actions">
            <el-button v-if="s.status !== 'closed'" class="hist-btn is-primary" @click="resume(s.id)">
              继续对话
            </el-button>
            <el-button class="hist-btn" @click="viewMessages(s.id)">查看</el-button>
            <el-button v-if="s.status === 'active'" class="hist-btn is-warn" @click="close(s.id)">
              结束
            </el-button>
            <el-button class="hist-btn is-danger" @click="remove(s.id)">删除</el-button>
          </div>
        </div>
        <p v-if="!loading && !sessions.length" class="sc-empty">暂无历史会话</p>
      </div>

      <el-table v-else :data="sessions" v-loading="loading">
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">{{ STATUS_LABELS[row.status] || row.status }}</template>
        </el-table-column>
        <el-table-column prop="last_route_to" label="最近路由" width="150" />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ dayjs(row.created_at).format('YYYY-MM-DD HH:mm:ss') }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300">
          <template #default="{ row }">
            <span class="hist-actions">
              <el-button v-if="row.status !== 'closed'" class="hist-btn is-primary" @click="resume(row.id)">
                继续对话
              </el-button>
              <el-button class="hist-btn" @click="viewMessages(row.id)">查看</el-button>
              <el-button v-if="row.status === 'active'" class="hist-btn is-warn" @click="close(row.id)">
                结束
              </el-button>
              <el-button class="hist-btn is-danger" @click="remove(row.id)">删除</el-button>
            </span>
          </template>
        </el-table-column>
      </el-table>
      <el-pagination
        style="margin-top: 12px"
        layout="prev, pager, next"
        :total="total"
        :page-size="20"
        v-model:current-page="page"
        @current-change="load"
      />
    </el-card>

    <el-dialog v-model="dialogVisible" title="历史消息" :width="isMobile ? '92%' : '640px'">
      <div class="msgs">
        <MessageBubble v-for="m in messages" :key="m.id" :message="m" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import dayjs from 'dayjs'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiCloseSession, apiDeleteSession, apiListSessions, apiSessionMessages } from '@/api'
import type { Message, SessionInfo } from '@/types'
import MessageBubble from '@/components/MessageBubble.vue'

const router = useRouter()
const route = useRoute()
// 历史页继承聊天入口的场景参数：不带 scene 时只看默认场景的会话
const sceneCode = computed(() => {
  const raw = route.query.scene
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
})
const sessions = ref<SessionInfo[]>([])
const messages = ref<Message[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const dialogVisible = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await apiListSessions(page.value, 20, sceneCode.value)
    sessions.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

async function viewMessages(id: string) {
  const data = await apiSessionMessages(id, 1, 100, sceneCode.value)
  messages.value = data.items
  dialogVisible.value = true
}

async function remove(id: string) {
  await ElMessageBox.confirm('确认删除该会话？', '提示', { type: 'warning' })
  await apiDeleteSession(id, sceneCode.value)
  load()
}

const STATUS_LABELS: Record<string, string> = { active: '进行中', closed: '已结束', handover: '转人工' }

function statusType(s: string): 'primary' | 'info' | 'warning' {
  return s === 'active' ? 'primary' : s === 'handover' ? 'warning' : 'info'
}

// 用 matchMedia 而不是一次性读 window.innerWidth：旋转屏幕、桌面分屏时也能切换布局
const isMobile = ref(false)
let mql: MediaQueryList | null = null

function syncIsMobile(e: MediaQueryList | MediaQueryListEvent) {
  isMobile.value = e.matches
}

async function close(id: string) {
  await ElMessageBox.confirm('确认结束该会话？短期记忆将归档为长期记忆。', '提示', { type: 'warning' })
  await apiCloseSession(id, sceneCode.value)
  ElMessage.success('会话已结束')
  load()
}

function backToChat() {
  router.push(sceneCode.value ? { path: '/chat', query: { scene: sceneCode.value } } : '/chat')
}

/** 继续对话：带上 session 回到聊天页，由 ChatView 判断是续聊还是只读 */
function resume(id: string) {
  const query: Record<string, string> = { session: id }
  if (sceneCode.value) query.scene = sceneCode.value
  router.push({ path: '/chat', query })
}

onMounted(() => {
  mql = window.matchMedia('(max-width: 768px)')
  syncIsMobile(mql)
  mql.addEventListener('change', syncIsMobile)
  load()
})

onBeforeUnmount(() => {
  mql?.removeEventListener('change', syncIsMobile)
  mql = null
})
</script>

<style scoped>
.history-page {
  min-height: 100%;
  padding: 24px 8%;
  background:
    radial-gradient(900px 420px at 10% -8%, rgba(122, 92, 255, 0.14), transparent 60%),
    radial-gradient(760px 420px at 96% 2%, rgba(79, 107, 255, 0.16), transparent 62%),
    linear-gradient(180deg, #f7f8ff 0%, #ecefff 100%);
}

.history-page :deep(.el-card) {
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.7);
  background: rgba(255, 255, 255, 0.86);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  box-shadow: 0 18px 44px rgba(31, 42, 80, 0.12);
}
.head {
  display: flex;
  justify-content: space-between;
}
.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.msgs {
  max-height: 60vh;
  overflow-y: auto;
}

/* ---------- 移动端卡片列表 ---------- */
.session-cards {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.session-card {
  padding: 12px;
  border: 1px solid rgba(79, 107, 255, 0.14);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 6px 18px rgba(31, 42, 80, 0.06);
}

.sc-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sc-title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sc-meta {
  margin-top: 6px;
  display: flex;
  gap: 6px;
  font-size: 12px;
  color: #909399;
}

.sc-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}

.sc-empty {
  margin: 24px 0;
  text-align: center;
  font-size: 13px;
  color: #909399;
}

/* ---------- 按钮：与聊天页同一套药丸语言 ---------- */
.hist-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.el-button.hist-btn {
  height: 28px;
  margin-left: 0;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(79, 107, 255, 0.18);
  background: rgba(255, 255, 255, 0.8);
  color: var(--fc-text);
  font-size: 13px;
  font-weight: 500;
  transition: background 0.16s ease, border-color 0.16s ease, color 0.16s ease,
    box-shadow 0.16s ease, transform 0.16s ease;
}

.el-button.hist-btn:hover,
.el-button.hist-btn:focus {
  background: #fff;
  border-color: rgba(79, 107, 255, 0.42);
  color: var(--fc-brand);
  transform: translateY(-1px);
}

.el-button.hist-btn.is-primary {
  border-color: transparent;
  color: #fff;
  background: linear-gradient(135deg, var(--fc-brand), var(--fc-brand-2));
  box-shadow: 0 6px 16px var(--fc-glow);
}

.el-button.hist-btn.is-primary:hover {
  color: #fff;
  background: linear-gradient(135deg, #5f79ff, #8a6bff);
}

.el-button.hist-btn.is-warn {
  color: #a96300;
  border-color: rgba(240, 160, 60, 0.35);
  background: rgba(240, 160, 60, 0.1);
}

.el-button.hist-btn.is-warn:hover {
  color: #a96300;
  border-color: rgba(240, 160, 60, 0.55);
  background: rgba(240, 160, 60, 0.18);
}

.el-button.hist-btn.is-danger {
  color: #c0392b;
  border-color: rgba(220, 80, 70, 0.28);
  background: rgba(220, 80, 70, 0.08);
}

.el-button.hist-btn.is-danger:hover {
  color: #c0392b;
  border-color: rgba(220, 80, 70, 0.5);
  background: rgba(220, 80, 70, 0.16);
}

@media (max-width: 768px) {
  .history-page {
    padding: 12px;
  }

  .msgs {
    max-height: 70dvh;
  }

  /* 手机上按钮抬到 32px，触控更好点 */
  .el-button.hist-btn {
    height: 32px;
  }
}
</style>
