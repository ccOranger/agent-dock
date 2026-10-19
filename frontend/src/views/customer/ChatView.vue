<template>
  <div class="chat-layout">
    <el-container>
      <el-header class="header">
        <span class="title">智能客服</span>
        <AgentBadge v-if="store.currentAgent" :agent="store.currentAgent" />
        <div class="spacer" />
        <el-button size="small" @click="onClose" :disabled="!store.sessionId || store.streaming">结束会话</el-button>
        <el-button size="small" @click="onNew">新建会话</el-button>
        <el-button size="small" @click="router.push('/history')">历史</el-button>
        <el-button size="small" @click="goAdmin" v-if="isAdminOrAgent()">管理后台</el-button>
        <el-button size="small" type="danger" plain @click="onLogout">退出</el-button>
      </el-header>
      <el-main class="main">
        <div class="messages" ref="listRef">
          <MessageBubble v-for="m in store.messages" :key="m.id" :message="m" />
          <el-alert
            v-if="handoverReason"
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
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiCloseSession } from '@/api'
import { streamChat } from '@/api/chat'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import MessageBubble from '@/components/MessageBubble.vue'
import AgentBadge from '@/components/AgentBadge.vue'

const store = useChatStore()
const auth = useAuthStore()
const router = useRouter()
const input = ref('')
const listRef = ref<HTMLElement>()
const handoverReason = ref('')
let controller: AbortController | null = null

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
  store.forceFinish()
})

function send() {
  const text = input.value.trim()
  if (!text || store.streaming) return
  input.value = ''
  handoverReason.value = ''
  store.pushUser(text)
  store.streaming = true

  controller = streamChat(store.sessionId, text, {
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
    done: () => {
      store.finishStreaming()
    },
    error: (d: any) => {
      store.forceFinish()
      ElMessage.error(d?.message || '对话出错')
    },
  })
}

function abort() {
  controller?.abort()
  store.forceFinish()
}

function onNew() {
  abort()
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
  await apiCloseSession(store.sessionId)
  store.reset()
  handoverReason.value = ''
  ElMessage.success('会话已结束，短期记忆已归档')
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
