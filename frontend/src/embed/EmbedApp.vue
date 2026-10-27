<template>
  <div class="embed-shell" :style="{ '--brand': themeColor }">
    <template v-if="loading">
      <div class="center muted">正在加载…</div>
    </template>

    <template v-else-if="error">
      <div class="center error-box">
        <div class="error-title">暂时无法开始对话</div>
        <div class="error-text">{{ error }}</div>
        <button class="primary" @click="init">重试</button>
      </div>
    </template>

    <template v-else>
      <header class="head">
        <div class="head-main">
          <div class="head-title">{{ title }}</div>
          <div class="head-sub">{{ subtitle }}</div>
        </div>
        <button class="icon-btn" title="关闭" @click="closeWidget">×</button>
      </header>

      <main class="body" ref="listRef">
        <div class="bubble assistant welcome" v-if="bubbles.length === 0 && welcome">
          {{ welcome }}
        </div>
        <div
          v-for="item in bubbles"
          :key="item.id"
          class="bubble"
          :class="item.role === 'user' ? 'user' : 'assistant'"
        >
          <template v-if="item.content">{{ item.content }}</template>
          <span v-else-if="item.status" class="thinking">{{ item.status }}</span>
          <span v-if="item.status && item.content" class="thinking inline">{{ item.status }}</span>
        </div>
      </main>

      <footer class="foot">
        <input
          v-model="input"
          class="input"
          :placeholder="placeholder"
          :disabled="streaming || !ready"
          maxlength="2000"
          @keydown.enter.exact.prevent="send"
        />
        <button v-if="!streaming" class="primary send" :disabled="!input.trim()" @click="send">
          发送
        </button>
        <button v-else class="stop" @click="abort">停止</button>
      </footer>
      <div v-if="showBranding" class="brand">由 AgentDock 智能客服提供支持</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { streamSSE } from '@shared'

interface WidgetConfig {
  title: string
  welcome: string
  theme_color: string
  position: 'left' | 'right'
  placeholder: string
  show_branding: boolean
}

interface PublicScene {
  code: string
  display_name: string
  description?: string | null
  allow_anonymous: boolean
  publish_key: string | null
  auth_modes: string[]
  /** 匿名接入的归属信息；未带公开标识时为 null */
  integration?: { code: string; name: string } | null
  widget: WidgetConfig
}

interface Bubble {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: string
}

interface VisitorIssued {
  visitor_id: string
  scene_code: string
}

const params = new URLSearchParams(window.location.search)
const sceneCode = (params.get('scene') || '').trim()
// 接入方公开标识：只用于匿名访客归属，不是安全凭证
const integrationCode = (params.get('integration') || '').trim()
// 自定义埋点：扁平 JSON，渠道等设备信息都作为其中的普通键（如 {"platform":"web"}）
const extParam = params.get('ext') || ''

function parseExt(raw: string): Record<string, string | number | boolean> {
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: Record<string, string | number | boolean> = {}
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
        out[key] = value
      }
    }
    return out
  } catch {
    // 非法 JSON 静默忽略：埋点不能阻塞对话
    return {}
  }
}

const ext = parseExt(extParam)
// 仅调试用：主站已登录客户可在 URL 带 token；嵌入场景默认匿名
const customerToken = params.get('token') || ''

const loading = ref(true)
const ready = ref(false)
const error = ref('')
const scene = ref<PublicScene | null>(null)
const visitorId = ref('')
const sessionId = ref('')
const input = ref('')
const streaming = ref(false)
const bubbles = ref<Bubble[]>([])
const listRef = ref<HTMLElement>()

let seq = 0
let controller: AbortController | null = null

const themeColor = ref('#2b5aa0')
const title = ref('在线客服')
const subtitle = ref('')
const welcome = ref('')
const placeholder = ref('请输入您的问题')
const showBranding = ref(true)

function postToHost(type: string, payload: Record<string, unknown> = {}) {
  if (window.parent === window) return
  window.parent.postMessage({ type, ...payload }, '*')
}

function closeWidget() {
  postToHost('cs:widget:close')
}

function cacheKey(code: string) {
  // 同一场景可能被多个接入方嵌入：缓存键必须带接入方，否则会复用错命名空间的访客
  return `cs_embed_visitor_${integrationCode || 'anon'}_${code}`
}

function loadVisitor(code: string): string {
  try {
    return localStorage.getItem(cacheKey(code)) || ''
  } catch {
    return ''
  }
}

function saveVisitor(code: string, value: string) {
  try {
    localStorage.setItem(cacheKey(code), value)
  } catch {
    // 隐私模式下 localStorage 可能不可用：退化为内存态，刷新后重新签发
  }
}

async function requestJSON<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const detail = body?.detail
    const message =
      (typeof detail === 'object' && detail?.message) ||
      body?.message ||
      (typeof detail === 'string' ? detail : '') ||
      `请求失败（HTTP ${resp.status}）`
    throw new Error(message)
  }
  return body?.data as T
}

async function init() {
  loading.value = true
  error.value = ''
  ready.value = false
  if (!sceneCode) {
    loading.value = false
    error.value = '缺少 scene 参数，请检查嵌入代码'
    return
  }
  try {
    const info = await requestJSON<PublicScene>(
      `/api/v1/public/scenes/${encodeURIComponent(sceneCode)}`,
      integrationCode ? { headers: { 'X-Integration-Code': integrationCode } } : undefined,
    )
    scene.value = info
    themeColor.value = info.widget?.theme_color || '#2b5aa0'
    title.value = info.widget?.title || info.display_name
    subtitle.value = info.description || ''
    welcome.value = info.widget?.welcome || ''
    placeholder.value = info.widget?.placeholder || '请输入您的问题'
    showBranding.value = info.widget?.show_branding !== false
    document.title = title.value

    if (info.allow_anonymous) {
      visitorId.value = loadVisitor(sceneCode)
      if (!visitorId.value) {
        const headers: Record<string, string> = {}
        if (info.publish_key) headers['X-Publish-Key'] = info.publish_key
        if (integrationCode) headers['X-Integration-Code'] = integrationCode
        const issued = await requestJSON<VisitorIssued>(
          `/api/v1/public/scenes/${encodeURIComponent(sceneCode)}/visitors`,
          { method: 'POST', headers },
        )
        visitorId.value = issued.visitor_id
        saveVisitor(sceneCode, issued.visitor_id)
      }
    } else if (!customerToken) {
      throw new Error('该场景未开放匿名访问，请从主站登录后再使用')
    }

    ready.value = true
    postToHost('cs:widget:ready', { title: title.value, scene: sceneCode })
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
    postToHost('cs:widget:error', { message: error.value })
  } finally {
    loading.value = false
  }
}

function scrollBottom() {
  nextTick(() => {
    const el = listRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

function pushBubble(role: 'user' | 'assistant', content: string, status = ''): Bubble {
  const item: Bubble = { id: ++seq, role, content, status }
  bubbles.value.push(item)
  scrollBottom()
  return item
}

function finishStream() {
  streaming.value = false
  const last = bubbles.value[bubbles.value.length - 1]
  if (last && last.role === 'assistant') last.status = ''
  postToHost('cs:widget:done')
}

function send() {
  const text = input.value.trim()
  if (!text || streaming.value || !ready.value) return
  input.value = ''
  pushBubble('user', text)
  const reply = pushBubble('assistant', '', '正在思考…')
  streaming.value = true

  const info = scene.value
  const ctrl = streamSSE({
    url: `/api/v1/chat/stream?scene_code=${encodeURIComponent(sceneCode)}`,
    token: customerToken || undefined,
    headers: {
      'X-Scene-Code': sceneCode,
      ...(integrationCode ? { 'X-Integration-Code': integrationCode } : {}),
      ...(info?.publish_key ? { 'X-Publish-Key': info.publish_key } : {}),
    },
    body: {
      scene_code: sceneCode,
      message: text,
      ...(Object.keys(ext).length ? { ext } : {}),
      ...(sessionId.value ? { session_id: sessionId.value } : {}),
      ...(visitorId.value ? { visitor_id: visitorId.value } : {}),
    },
    handlers: {
      session: (d: any) => {
        sessionId.value = d?.session_id || ''
      },
      status: (d: any) => {
        reply.status = d?.text || ''
      },
      text: (d: any) => {
        reply.content += d?.content || ''
        reply.status = ''
        scrollBottom()
      },
      handover: () => {
        reply.status = ''
        if (!reply.content) reply.content = '已为您转接人工客服，请稍候。'
      },
      done: () => finishStream(),
      error: (d: any) => {
        reply.status = ''
        const fallback =
          d?.code === 401 ? '会话已失效，请刷新页面后重试' : d?.message || '对话出错，请稍后重试'
        if (!reply.content) reply.content = fallback
      },
    },
    onClose: () => {
      if (controller !== ctrl) return
      controller = null
      finishStream()
    },
  })
  controller = ctrl
}

function abort() {
  const ctrl = controller
  controller = null
  ctrl?.abort()
  finishStream()
}

onMounted(init)

onBeforeUnmount(() => {
  controller?.abort()
  controller = null
})
</script>

<style scoped>
.embed-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: #fff;
  color: #1f2329;
}

.center {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  padding: 24px;
  text-align: center;
}

.muted {
  color: #8a919f;
  font-size: 13px;
}

.error-box {
  flex-direction: column;
  gap: 10px;
}

.error-title {
  font-weight: 600;
}

.error-text {
  color: #8a919f;
  font-size: 13px;
  line-height: 1.6;
}

.head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  color: #fff;
  background: var(--brand);
  flex: none;
}

.head-main {
  min-width: 0;
  flex: 1;
}

.head-title {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.head-sub {
  margin-top: 2px;
  font-size: 12px;
  opacity: 0.85;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.icon-btn {
  border: 0;
  background: transparent;
  color: #fff;
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 6px;
}

.icon-btn:hover {
  background: rgba(255, 255, 255, 0.16);
}

.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 12px;
  background: #f5f7fa;
}

.bubble {
  max-width: 82%;
  width: fit-content;
  margin-bottom: 10px;
  padding: 9px 12px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.bubble.assistant {
  background: #fff;
  color: #1f2329;
  border-top-left-radius: 4px;
  box-shadow: 0 1px 2px rgba(31, 35, 41, 0.08);
}

.bubble.user {
  margin-left: auto;
  background: var(--brand);
  color: #fff;
  border-top-right-radius: 4px;
}

.bubble.welcome {
  margin-bottom: 14px;
}

.thinking {
  color: #8a919f;
}

.thinking.inline {
  margin-left: 6px;
  font-size: 12px;
}

.foot {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  border-top: 1px solid #ebeef5;
  background: #fff;
  flex: none;
}

.input {
  flex: 1;
  min-width: 0;
  height: 38px;
  padding: 0 12px;
  border: 1px solid #dcdfe6;
  border-radius: 8px;
  font-size: 14px;
  outline: none;
}

.input:focus {
  border-color: var(--brand);
}

.primary,
.stop {
  height: 38px;
  padding: 0 16px;
  border: 0;
  border-radius: 8px;
  font-size: 14px;
  color: #fff;
  background: var(--brand);
  cursor: pointer;
  white-space: nowrap;
}

.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.stop {
  background: #e6a23c;
}

.brand {
  padding: 0 12px 8px;
  text-align: center;
  font-size: 11px;
  color: #b1b6c0;
  background: #fff;
  flex: none;
}

@media (max-width: 640px) {
  .head {
    padding: 14px;
  }

  .body {
    padding: 12px 10px;
  }
}
</style>
