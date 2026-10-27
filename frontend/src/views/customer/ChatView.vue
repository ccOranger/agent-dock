<template>
  <div class="chat-layout">
    <el-container>
      <el-header class="header">
        <span class="title">AgentDock</span>
        <el-tag v-if="sceneCode" size="small" type="info">场景 {{ sceneCode }}</el-tag>
        <AgentBadge v-if="store.currentAgent" :agent="store.currentAgent" />
        <div class="spacer" />
        <el-button size="small" @click="onClose" :disabled="!store.sessionId || store.streaming">结束会话</el-button>
        <el-button size="small" @click="onNew">新建会话</el-button>
        <el-button size="small" @click="goHistory">历史</el-button>
        <el-button size="small" @click="goAdmin" v-if="isAdminOrAgent()">管理后台</el-button>
        <el-button size="small" type="danger" plain @click="onLogout">退出</el-button>
      </el-header>
      <el-main class="main">
        <div class="messages" ref="listRef">
          <MessageBubble v-for="m in store.messages" :key="m.id" :message="m" />
          <el-alert
            v-if="takeover"
            type="warning"
            :closable="false"
            title="人工客服已接入，您的消息将直接转达客服。"
          />
          <el-alert
            v-else-if="handoverReason"
            type="warning"
            :closable="false"
            :title="`已转人工处理（${handoverReason}）`"
          />
        </div>
      </el-main>
      <el-footer class="footer">
        <el-input
          v-model="input"
          placeholder="请输入您的问题，Enter 发送"
          :disabled="store.streaming"
          @keyup.enter="send"
        >
          <template #append>
            <el-button v-if="!store.streaming" type="primary" @click="send">发送</el-button>
            <el-button v-else type="warning" @click="abort">停止</el-button>
          </template>
        </el-input>
      </el-footer>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiChatPoll, apiCloseSession } from '@/api'
import { streamChat } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import MessageBubble from '@/components/MessageBubble.vue'
import AgentBadge from '@/components/AgentBadge.vue'

const store = useChatStore()
const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
// 聊天入口只有这一个：URL 带 ?scene=xxx 就落到该场景，不带走默认场景
const sceneCode = computed(() => {
  const raw = route.query.scene
  return typeof raw === 'string' && raw.trim() ? raw.trim() : null
})
const input = ref('')
const listRef = ref<HTMLElement>()
const handoverReason = ref('')
let controller: AbortController | null = null

// ===== 人工接管轮询 =====
// 接管期间无 SSE：靠定时轮询 /chat/poll 拉取坐席回复；cursor 为已消费的最后一条消息 id。
const takeover = ref(false)
const pollCursor = ref<string | null>(null)
const seenStaffIds = new Set<string>()
let pollTimer: ReturnType<typeof setInterval> | null = null

function startTakeover(cursorId?: string | null) {
  takeover.value = true
  if (cursorId) pollCursor.value = cursorId
  if (!pollTimer) pollTimer = setInterval(poll, 3500)
  poll()
}

function stopTakeover(note?: string) {
  takeover.value = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
  if (note) ElMessage.info(note)
}

function resetTakeover() {
  stopTakeover()
  pollCursor.value = null
  seenStaffIds.clear()
}

async function poll() {
  if (!store.sessionId || !takeover.value) return
  try {
    const res = await apiChatPoll(store.sessionId, pollCursor.value, sceneCode.value)
    if (res.messages.length) pollCursor.value = res.messages[res.messages.length - 1].id
    let added = false
    for (const m of res.messages) {
      // 只追加坐席回复（role=assistant 且标记 staff）；客户自己的消息与机器人应答不进这里
      if (m.role === 'assistant' && (m.sender_type === 'staff' || m.routed_to === 'staff')) {
        if (!seenStaffIds.has(m.id)) {
          seenStaffIds.add(m.id)
          store.pushResolvedAssistant(m.content, 'staff')
          added = true
        }
      }
    }
    if (added) scrollBottom()
    if (res.status === 'active') stopTakeover('人工客服已结束，机器人继续为您服务')
    else if (res.status === 'closed') stopTakeover('会话已结束')
  } catch {
    /* 轮询是幂等旁路：单次失败静默等下一轮，不打断对话 */
  }
}

const isAdminOrAgent = () => ['agent', 'admin'].includes(auth.user?.role ?? '')

function scrollBottom() {
  nextTick(() => {
    if (listRef.value) listRef.value.scrollTop = listRef.value.scrollHeight
  })
}

// 打字机每揭示一段文本后自动滚动到底部
onMounted(() => store.setRevealHook(scrollBottom))
onBeforeUnmount(() => {
  store.setRevealHook(null)
  // 卸载时中断仍在读的流，避免后台继续消费与旧流的收尾回调干扰后续新流
  controller?.abort()
  controller = null
  store.forceFinish()
  stopTakeover()
})

function send() {
  const text = input.value.trim()
  if (!text || store.streaming) return
  input.value = ''
  handoverReason.value = ''
  store.pushUser(text)
  store.streaming = true

  const ctrl = streamChat(store.sessionId, text, {
    session: (d: any) => {
      store.sessionId = d.session_id
    },
    status: (d: any) => {
      // 后端真实阶段进度（routing/preparing/generating）驱动占位气泡文案
      store.setStatus(d?.text || '')
    },
    route: (d: any) => {
      store.setRoute(d.agent_name)
    },
    agent_start: (d: any) => {
      // 多意图串行应答：徽标跟随当前正在服务的业务 Agent
      store.setRoute(d.agent_name)
    },
    text: (d: any) => {
      store.appendText(d.content)
    },
    tool_call: (d: any) => {
      store.addToolCall(d)
      scrollBottom()
    },
    tool_result: (d: any) => {
      store.finishToolCall(d)
    },
    handover: (d: any) => {
      handoverReason.value = d.reason || 'low_confidence'
      store.finishStreaming()
    },
    done: (d: any) => {
      store.finishStreaming()
      // 转人工 / 接管中：done 带 session_status 时进入轮询，坐席回复靠 poll 送达
      const st = d?.session_status
      if (st === 'handover' || st === 'human_handling' || d?.takeover) {
        startTakeover(d?.message_id)
      }
    },
    error: (d: any) => {
      store.forceFinish()
      ElMessage.error(d?.message || '对话出错')
    },
  }, () => {
    // 兜底复位：流因任何原因结束（done / error / 服务端挂起被断开 / 代理超时断开）都
    // 必须收尾 streaming 状态，否则输入框会永久禁用；已正常收尾时重复调用是幂等的。
    // 仅当仍是当前流时收尾：已停止/已卸载的旧流收尾不得污染随后的新流。
    if (controller !== ctrl) return
    controller = null
    store.finishStreaming()
  }, sceneCode.value)
  controller = ctrl
}

function abort() {
  const ctrl = controller
  controller = null
  ctrl?.abort()
  store.forceFinish()
}

function onNew() {
  abort()
  resetTakeover()
  store.reset()
  handoverReason.value = ''
}

async function onClose() {
  if (!store.sessionId) return
  try {
    await ElMessageBox.confirm(
      '结束后，本次会话的短期记忆将自动归档为长期记忆。确认结束当前会话？',
      '结束会话',
      { type: 'warning' },
    )
  } catch {
    return // 用户取消
  }
  await apiCloseSession(store.sessionId, sceneCode.value)
  resetTakeover()
  store.reset()
  handoverReason.value = ''
  ElMessage.success('会话已结束，短期记忆已归档')
}

function goHistory() {
  // 历史页继承场景参数，否则会落到默认场景看不到本场景会话
  router.push(sceneCode.value ? { path: '/history', query: { scene: sceneCode.value } } : '/history')
}

function onLogout() {
  auth.logout()
  router.push('/login')
}

function goAdmin() {
  // 管理功能已迁移至独立后台工程 admin-frontend，此处跳转到外部控制台地址
  const url = (import.meta.env.VITE_ADMIN_CONSOLE_URL as string) || 'http://localhost:5174'
  window.open(url, '_blank', 'noopener')
}
</script>

<style scoped>
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #ebeef5;
  background: #fff;
}
.title {
  font-weight: 700;
}
.spacer {
  flex: 1;
}
.main {
  background: #f5f7fa;
  overflow: hidden;
}
.messages {
  height: 100%;
  overflow-y: auto;
  padding: 12px 4%;
}
.footer {
  padding: 12px 4%;
}
</style>
