<template>
  <div class="chat-layout">
    <el-container>
      <el-header class="header">
        <span class="title">AgentDock</span>
        <el-tag v-if="sceneCode" class="scene-tag" size="small" type="info">场景 {{ sceneCode }}</el-tag>
        <AgentBadge v-if="store.currentAgent" :agent="store.currentAgent" />
        <div class="spacer" />
        <el-button class="header-btn" @click="goHistory">
          <svg class="hc-ico" viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8.2" />
            <path d="M12 7.6v4.6l3.1 1.9" />
          </svg>
          历史
        </el-button>
        <el-dropdown trigger="click" popper-class="cs-header-menu" @command="onHeaderCommand">
          <el-button class="header-btn">
            更多
            <svg class="hc-caret" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M6.5 9.5 12 15l5.5-5.5" />
            </svg>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="new">新建会话</el-dropdown-item>
              <el-dropdown-item command="close" :disabled="readOnly || !store.sessionId || store.streaming">
                结束会话
              </el-dropdown-item>
              <el-dropdown-item v-if="isAdminOrAgent()" command="admin">管理后台</el-dropdown-item>
              <el-dropdown-item command="logout" divided>退出</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
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
      <el-footer v-if="readOnly" class="footer readonly-bar">
        <span class="readonly-text">该会话已结束，只能查看</span>
        <el-button class="send-btn" @click="onNew">开始新对话</el-button>
      </el-footer>
      <el-footer v-else class="footer">
        <el-input
          v-model="input"
          class="composer"
          placeholder="请输入您的问题，Enter 发送"
          :disabled="store.streaming"
          @keyup.enter="send"
        />
        <el-button v-if="!store.streaming" class="send-btn" @click="send">发送</el-button>
        <el-button v-else class="send-btn stop" @click="abort">停止</el-button>
      </el-footer>
    </el-container>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiChatPoll, apiCloseSession, apiGetSession, apiSessionMessages } from '@/api'
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
// 历史会话：已结束的只读，未结束的可以直接续聊
const readOnly = ref(false)
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

// 顶部「更多」：把低频操作收进下拉，窄屏顶栏才能保持一行
function onHeaderCommand(cmd: string) {
  if (cmd === 'new') onNew()
  else if (cmd === 'close') void onClose()
  else if (cmd === 'admin') goAdmin()
  else if (cmd === 'logout') onLogout()
}

// 从历史页带 ?session= 进来：先确认状态，再决定是续聊还是只读
async function restoreSession(id: string) {
  try {
    const [sess, page] = await Promise.all([
      apiGetSession(id, sceneCode.value),
      apiSessionMessages(id, 1, 100, sceneCode.value),
    ])
    store.hydrate(id, page.items)
    readOnly.value = sess.status === 'closed'
    scrollBottom()
  } catch {
    ElMessage.error('历史会话加载失败')
  }
}

// 移动端键盘弹起/收起时可视高度会变化，顺手把列表贴回底部，避免新消息被遮住
function onViewportResize() {
  scrollBottom()
}

// 打字机每揭示一段文本后自动滚动到底部
onMounted(() => {
  store.setRevealHook(scrollBottom)
  window.visualViewport?.addEventListener('resize', onViewportResize)
  const sid = route.query.session
  if (typeof sid === 'string' && sid) void restoreSession(sid)
})
onBeforeUnmount(() => {
  store.setRevealHook(null)
  window.visualViewport?.removeEventListener('resize', onViewportResize)
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
  readOnly.value = false
  handoverReason.value = ''
  // 清掉 URL 上的 session，避免刷新后又回到那个只读的历史会话
  if (route.query.session) {
    const query = route.query.scene ? { scene: route.query.scene } : {}
    void router.replace({ path: '/chat', query })
  }
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
/* 撑满视口并锁住高度：否则整页会随消息变长，输入框被推到屏幕外 */
.chat-layout {
  height: 100vh;
  height: 100dvh;
  /* 双色光晕铺底，代替纯灰背景 */
  background:
    radial-gradient(900px 420px at 10% -8%, rgba(122, 92, 255, 0.18), transparent 60%),
    radial-gradient(760px 420px at 96% 2%, rgba(79, 107, 255, 0.2), transparent 62%),
    linear-gradient(180deg, #f7f8ff 0%, #ecefff 100%);
}

.chat-layout :deep(.el-container) {
  height: 100%;
}

.header {
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--fc-glass);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-bottom: 1px solid var(--fc-border);
}

/* 顶栏底部一道渐变线，比 1px 灰边更有“光” */
.header::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    rgba(79, 107, 255, 0.5),
    rgba(122, 92, 255, 0.5),
    transparent
  );
}

.title {
  font-weight: 700;
  letter-spacing: -0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.spacer {
  flex: 1;
}

/* ---------- 顶栏按钮：药丸玻璃，和渐变背景、气泡同一套语言 ---------- */
.header-btn {
  height: 32px;
  margin-left: 0;
  padding: 0 13px;
  border-radius: 999px;
  border: 1px solid rgba(79, 107, 255, 0.18);
  background: rgba(255, 255, 255, 0.72);
  color: var(--fc-text);
  font-size: 13px;
  font-weight: 500;
  box-shadow: 0 2px 8px rgba(31, 42, 80, 0.05);
  transition: transform 0.16s ease, box-shadow 0.16s ease, border-color 0.16s ease,
    color 0.16s ease, background 0.16s ease;
}

.header-btn:hover,
.header-btn:focus {
  background: #fff;
  border-color: rgba(79, 107, 255, 0.42);
  color: var(--fc-brand);
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(79, 107, 255, 0.16);
}

.header-btn:active {
  transform: translateY(0);
}

/* Element Plus 给相邻按钮加了 12px margin，这里由 flex gap 统一接管 */
.header .el-button + .el-button {
  margin-left: 0;
}

.hc-ico,
.hc-caret {
  width: 14px;
  height: 14px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.hc-ico {
  margin-right: 4px;
}

.hc-caret {
  margin-left: 2px;
  opacity: 0.65;
}
.main {
  background: transparent;
  overflow: hidden;
  min-height: 0;
}
.messages {
  height: 100%;
  overflow-y: auto;
  padding: 12px 4%;
}

/* 新消息轻微上浮进场；流式追加只改文本，不会重复播放 */
.messages > * {
  animation: msg-in 0.28s cubic-bezier(0.22, 0.7, 0.3, 1) both;
}

@keyframes msg-in {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

.footer {
  display: flex;
  align-items: center;
  gap: 10px;
  /* el-footer 默认写死 60px，这里内容更高，取消固定高度 */
  height: auto;
  padding: 12px 4% calc(12px + env(safe-area-inset-bottom));
  background: var(--fc-glass);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
  border-top: 1px solid var(--fc-border);
}

.composer {
  flex: 1;
}

.composer :deep(.el-input__wrapper) {
  min-height: 44px;
  padding: 0 18px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  box-shadow: 0 0 0 1px var(--fc-border) inset;
  transition: box-shadow 0.2s ease;
}

.composer :deep(.el-input__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--fc-brand) inset, 0 0 0 4px var(--fc-glow);
}

/* 主按钮：品牌渐变 + 柔光投影 */
.footer .send-btn {
  height: 44px;
  padding: 0 20px;
  border: 0;
  border-radius: 999px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, var(--fc-brand), var(--fc-brand-2));
  box-shadow: 0 8px 20px var(--fc-glow);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}

.footer .send-btn:hover {
  background: linear-gradient(135deg, #5f79ff, #8a6bff);
  transform: translateY(-1px);
}

.footer .send-btn:active {
  transform: translateY(0);
}

.footer .send-btn.stop {
  background: linear-gradient(135deg, #f3a83c, #ef8140);
  box-shadow: 0 8px 20px rgba(240, 140, 60, 0.28);
}

/* 只读历史会话：底部换成说明 + 新建入口 */
.readonly-bar {
  justify-content: space-between;
}

.readonly-text {
  margin-left: 8px;
  font-size: 13px;
  color: var(--fc-text-2);
}

/* ---------- 移动端 ---------- */
@media (max-width: 768px) {
  .header {
    height: 52px;
    padding: 0 10px;
    gap: 6px;
  }
  .title {
    font-size: 15px;
  }
  .main {
    padding: 0;
  }
  .messages {
    padding: 12px 10px;
  }
  .footer {
    gap: 8px;
    padding: 10px 10px calc(10px + env(safe-area-inset-bottom));
  }
  .footer .send-btn {
    padding: 0 16px;
  }
  /* 触控目标放大：手机上按钮高度抬到 34px */
  .header-btn {
    height: 34px;
    padding: 0 12px;
  }
}

/* 极窄屏（≤420px）：场景码让位给标题，避免挤掉「历史」 */
@media (max-width: 420px) {
  .scene-tag {
    display: none;
  }
}
</style>
