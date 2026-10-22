<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <b>业务场景</b>
        <div class="header-ops">
          <el-button size="small" @click="load">刷新</el-button>
          <el-button type="primary" size="small" @click="openCreate">新建场景</el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="一套代码、一个聊天入口：请求带 scene_code 就落到对应场景，不带就是默认场景"
      description="场景只做一件事——把「谁能回答」圈定在绑定的 Agent 里。路由不是独立实体：auto 下 1 个 Agent 直连、2 个及以上才调用路由 LLM；llm 强制路由；direct 强制直连。默认场景固定直连 default_assistant，避免通用提问被业务 Agent 抢答。"
      style="margin-bottom: 12px"
    />

    <el-table :data="scenes" v-loading="loading">
      <el-table-column label="场景" min-width="200">
        <template #default="{ row }">
          <div class="scene-name">
            <span class="name">{{ row.display_name }}</span>
            <el-tag v-if="row.is_default" size="small" type="success">默认</el-tag>
            <el-tag v-if="row.status === 'paused'" size="small" type="warning">已停用</el-tag>
            <el-tag v-if="row.status === 'archived'" size="small" type="info">已归档</el-tag>
          </div>
          <div class="code-line">
            <code>{{ row.code }}</code>
            <el-tooltip content="复制场景标识" placement="top">
              <el-button link size="small" @click="copyCode(row.code)">复制</el-button>
            </el-tooltip>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="路由模式" width="170">
        <template #default="{ row }">
          <el-tag size="small" :type="modeTagType(row.router_mode)">
            {{ modeLabel(row.router_mode) }}
          </el-tag>
          <div class="muted small">{{ modeHint(row) }}</div>
        </template>
      </el-table-column>
      <el-table-column label="绑定 Agent" width="110">
        <template #default="{ row }">
          <span :class="{ muted: !row.agent_count }">{{ row.agent_count }} 个</span>
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="220" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.description || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="启用" width="70">
        <template #default="{ row }">
          <el-switch
            v-model="row.status"
            active-value="active"
            inactive-value="paused"
            :disabled="row.is_default"
            @change="() => onToggle(row)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="300" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
          <el-button
            link
            type="primary"
            size="small"
            :disabled="row.is_default"
            @click="openBind(row)"
          >绑定 Agent</el-button>
          <el-button link type="primary" size="small" @click="openEmbed(row)">接入</el-button>
          <el-button link type="primary" size="small" @click="openChat(row)">试聊</el-button>
          <el-button
            v-if="!row.is_default"
            link
            type="danger"
            size="small"
            @click="remove(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-dialog
    v-model="dialogVisible"
    :title="editing ? `编辑场景：${form.code}` : '新建场景'"
    width="640px"
  >
    <el-form :model="form" label-width="110px">
      <el-form-item label="场景标识" required>
        <el-input
          v-model="form.code"
          :disabled="editing"
          placeholder="对外唯一标识，小写字母开头，如 mall_order"
        />
        <div class="rule-tip">
          对接方在 chat 请求里传这个 code 区分场景；创建后不可修改。
        </div>
      </el-form-item>
      <el-form-item label="展示名" required>
        <el-input v-model="form.display_name" maxlength="64" placeholder="如：商城订单咨询" />
      </el-form-item>
      <el-form-item label="说明">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          maxlength="500"
          placeholder="这个场景服务哪类业务、面向谁"
        />
      </el-form-item>
      <el-form-item>
        <template #label>
          路由模式
          <el-tooltip
            placement="top"
            content="auto：1 个 Agent 直连、2 个及以上调用路由 LLM；llm：始终由路由 LLM 选 Agent；direct：始终直连第一个绑定的 Agent。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-radio-group v-model="form.router_mode" :disabled="isDefaultEditing">
          <el-radio value="auto">自动（按 Agent 数）</el-radio>
          <el-radio value="llm">始终路由</el-radio>
          <el-radio value="direct">始终直连</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item>
        <template #label>
          兜底话术
          <el-tooltip
            placement="top"
            content="路由判定「没有可用 Agent」时下发给客户的话；留空则使用系统默认的转人工提示。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input
          v-model="form.fallback_text"
          type="textarea"
          :rows="3"
          :disabled="isDefaultEditing"
          maxlength="1000"
          placeholder="例如：这个问题我先帮您转给人工客服，请稍等。"
        />
      </el-form-item>
      <el-form-item v-if="editing && !isDefaultEditing" label="状态">
        <el-select v-model="form.status" style="width: 100%">
          <el-option label="启用" value="active" />
          <el-option label="停用（拒绝新对话）" value="paused" />
          <el-option label="归档（保留数据）" value="archived" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="isDefaultEditing">
        <span class="muted small">
          默认场景承担「无 scene_code」的全部请求，只允许修改展示名与说明；路由模式、绑定 Agent、启停由平台固定。
        </span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>

  <el-drawer v-model="bindVisible" :title="bindTitle" size="720px">
    <div v-loading="bindLoading" class="bind-body">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="绑定顺序即路由候选顺序：排在前面的是同分时的优先候选，也是 direct 模式实际使用的 Agent"
        style="margin-bottom: 12px"
      />
      <div class="bind-columns">
        <div class="bind-col">
          <div class="bind-col-title">可绑定 Agent（{{ availableAgents.length }}）</div>
          <div class="bind-list">
            <div v-for="agent in availableAgents" :key="agent.id" class="bind-item">
              <div class="bind-info">
                <span>{{ agent.display_name }}</span>
                <el-tag size="small" :type="agent.source === 'code' ? 'info' : 'success'">
                  {{ agent.source === 'code' ? '代码' : '动态' }}
                </el-tag>
                <el-tag v-if="!agent.enabled" size="small" type="danger">已停用</el-tag>
              </div>
              <el-button size="small" @click="pickAgent(agent.id)">添加</el-button>
            </div>
            <span v-if="!availableAgents.length" class="muted">没有可添加的 Agent</span>
          </div>
        </div>
        <div class="bind-col">
          <div class="bind-col-title">已绑定（{{ pickedAgents.length }}）</div>
          <div class="bind-list">
            <div v-for="(agent, index) in pickedAgents" :key="agent.id" class="bind-item">
              <div class="bind-info">
                <span class="order">{{ index + 1 }}</span>
                <span>{{ agent.display_name }}</span>
                <el-tag size="small" :type="agent.source === 'code' ? 'info' : 'success'">
                  {{ agent.source === 'code' ? '代码' : '动态' }}
                </el-tag>
              </div>
              <div class="bind-ops">
                <el-button link size="small" :disabled="index === 0" @click="moveAgent(index, -1)">上移</el-button>
                <el-button link size="small" :disabled="index === pickedAgents.length - 1" @click="moveAgent(index, 1)">下移</el-button>
                <el-button link type="danger" size="small" @click="unpickAgent(index)">移除</el-button>
              </div>
            </div>
            <span v-if="!pickedAgents.length" class="muted">还没绑定 Agent，这个场景会拒绝新对话（3005）</span>
          </div>
        </div>
      </div>
    </div>
    <template #footer>
      <el-button @click="bindVisible = false">取消</el-button>
      <el-button type="primary" :loading="bindSaving" @click="saveBind">保存绑定</el-button>
    </template>
  </el-drawer>

  <el-drawer
    v-model="embedVisible"
    :title="embedTitle"
    size="760px"
    @closed="rotatedKey = ''"
  >
    <div v-loading="embedLoading" class="embed-body">
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="接入配置决定观众看到什么：气泡标题、欢迎语、主题色与位置都从这里下发，改完刷新宿主页即生效"
        description="开放匿名后，未登录访客凭访客标识即可聊天；第三方站点必须把自身来源逐行列入「允许来源」，否则浏览器跨域会被拒绝（留空只有平台自有前端可用）。"
        style="margin-bottom: 12px"
      />

      <el-form label-width="110px">
        <el-form-item label="气泡标题">
          <el-input v-model="embedForm.title" maxlength="64" placeholder="默认使用场景展示名" />
        </el-form-item>
        <el-form-item label="欢迎语">
          <el-input
            v-model="embedForm.welcome"
            type="textarea"
            :rows="2"
            maxlength="500"
            placeholder="打开组件后由助手先发的第一句话"
          />
        </el-form-item>
        <el-form-item label="输入框提示">
          <el-input v-model="embedForm.placeholder" maxlength="100" placeholder="请输入您的问题" />
        </el-form-item>
        <el-form-item label="主题色">
          <el-color-picker v-model="embedForm.theme_color" />
          <code class="embed-inline">{{ embedForm.theme_color || embedDefaults.theme_color }}</code>
        </el-form-item>
        <el-form-item label="气泡位置">
          <el-radio-group v-model="embedForm.position">
            <el-radio value="right">右下角</el-radio>
            <el-radio value="left">左下角</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="品牌标识">
          <el-switch v-model="embedForm.show_branding" />
          <span class="rule-tip embed-inline">关闭后隐藏对话页底部的 AgentDock 标识</span>
        </el-form-item>
        <el-form-item label="允许来源">
          <el-input
            v-model="embedForm.originsText"
            type="textarea"
            :rows="3"
            placeholder="每行一个来源，如 https://www.example.com"
          />
          <div class="rule-tip">
            只写 scheme://host[:port]，不带路径；保存时自动去重。第三方宿主必须列入，否则跨域预检直接 403。
          </div>
        </el-form-item>
        <el-form-item label="开放匿名">
          <el-switch v-model="embedForm.allow_anonymous" />
          <span class="rule-tip embed-inline">
            关闭后访客无法自助接入，只能带客户 token 或服务端 API key 调用
          </span>
        </el-form-item>
        <el-form-item v-if="embedForm.publish_key" label="发布标识">
          <code class="embed-inline">{{ embedForm.publish_key }}</code>
          <el-button link size="small" @click="copyText(embedForm.publish_key)">复制</el-button>
          <div class="rule-tip">公开标识，会下发给嵌入组件；真正防滥用靠来源白名单与限流。</div>
        </el-form-item>
        <el-form-item label="服务端 Key">
          <el-tag size="small" :type="embedForm.api_key_set ? 'success' : 'info'">
            {{ embedForm.api_key_set ? '已配置' : '未配置' }}
          </el-tag>
          <el-button
            link
            type="warning"
            size="small"
            style="margin-left: 8px"
            :loading="rotating"
            @click="rotateKey"
          >换发 Key</el-button>
          <div v-if="rotatedKey" class="rotate-result">
            <code>{{ rotatedKey }}</code>
            <el-button link size="small" @click="copyText(rotatedKey)">复制</el-button>
            <div class="rule-tip">明文只显示这一次，换发后旧 Key 立即失效。</div>
          </div>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">嵌入代码</el-divider>
      <div class="embed-item">
        <div class="embed-item-title">悬浮气泡（推荐）</div>
        <div class="rule-tip">
          整站生效：在左下 / 右下悬浮气泡，点击展开对话窗；位置跟随上方「气泡位置」。
        </div>
        <div class="embed-code">
          <pre>{{ embedSnippet }}</pre>
          <el-button size="small" @click="copyText(embedSnippet)">复制 script 代码</el-button>
        </div>
      </div>
      <div class="embed-item">
        <div class="embed-item-title">直接内嵌聊天窗（不悬浮）</div>
        <div class="rule-tip">适合独立客服页面：固定尺寸内嵌对话窗，不出现气泡。</div>
        <pre class="embed-pre">{{ iframeSnippet }}</pre>
      </div>

      <el-divider content-position="left">效果预览</el-divider>
      <div class="rule-tip" style="margin-bottom: 8px">
        模拟宿主页：气泡悬浮在{{ embedForm.position === 'left' ? '左下角' : '右下角' }}，点开即可对话。预览读取已保存配置，改完先保存再看。
      </div>
      <iframe :src="bubblePreviewUrl" class="embed-preview" title="嵌入效果预览" />
    </div>
    <template #footer>
      <el-button @click="embedVisible = false">关闭</el-button>
      <el-button type="primary" :loading="embedSaving" @click="saveEmbed">保存接入配置</el-button>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCreateScene,
  apiDeleteScene,
  apiGetScene,
  apiListScenes,
  apiRotateSceneKey,
  apiSceneAgentOptions,
  apiSetSceneAgents,
  apiUpdateScene,
  apiUpdateSceneWidget,
} from '@/api'
import type {
  Scene,
  SceneAgentOption,
  SceneCreatePayload,
  SceneRouterMode,
  SceneStatus,
  SceneUpdatePayload,
} from '@/types'

const ROUTER_MODE_LABELS: Record<SceneRouterMode, string> = {
  auto: '自动',
  llm: '始终路由',
  direct: '始终直连',
}

const scenes = ref<Scene[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const submitting = ref(false)

interface SceneForm {
  id: string
  code: string
  display_name: string
  description: string
  status: SceneStatus
  router_mode: SceneRouterMode
  fallback_text: string
}

const form = reactive<SceneForm>({
  id: '',
  code: '',
  display_name: '',
  description: '',
  status: 'active',
  router_mode: 'auto',
  fallback_text: '',
})

const isDefaultEditing = computed(() => {
  if (!editing.value) return false
  return scenes.value.find((s) => s.id === form.id)?.is_default === true
})

function modeLabel(mode: SceneRouterMode) {
  return ROUTER_MODE_LABELS[mode] || mode
}

function modeTagType(mode: SceneRouterMode) {
  if (mode === 'direct') return 'success'
  if (mode === 'llm') return 'warning'
  return 'info'
}

function modeHint(row: Scene) {
  if (row.is_default) return '固定直连默认助手'
  if (row.router_mode === 'direct') return '使用第 1 个绑定 Agent'
  if (row.router_mode === 'llm') return '调用路由 LLM'
  return row.agent_count <= 1 ? '1 个 Agent 直连' : `${row.agent_count} 个 Agent 供路由选择`
}

async function load() {
  loading.value = true
  try {
    scenes.value = await apiListScenes()
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.id = ''
  form.code = ''
  form.display_name = ''
  form.description = ''
  form.status = 'active'
  form.router_mode = 'auto'
  form.fallback_text = ''
}

function openCreate() {
  editing.value = false
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: Scene) {
  editing.value = true
  form.id = row.id
  form.code = row.code
  form.display_name = row.display_name
  form.description = row.description || ''
  form.status = row.status
  form.router_mode = row.router_mode
  form.fallback_text = row.fallback_text || ''
  dialogVisible.value = true
}

async function submit() {
  if (!form.code.trim() || !form.display_name.trim()) {
    ElMessage.warning('场景标识与展示名都要填写')
    return
  }
  submitting.value = true
  try {
    const description = form.description.trim() || null
    const fallbackText = form.fallback_text.trim() || null
    if (editing.value) {
      const payload: SceneUpdatePayload = isDefaultEditing.value
        ? { display_name: form.display_name.trim(), description }
        : {
            display_name: form.display_name.trim(),
            description,
            status: form.status,
            router_mode: form.router_mode,
            fallback_text: fallbackText,
          }
      await apiUpdateScene(form.id, payload)
    } else {
      const payload: SceneCreatePayload = {
        code: form.code.trim(),
        display_name: form.display_name.trim(),
        description,
        router_mode: form.router_mode,
        fallback_text: fallbackText,
      }
      await apiCreateScene(payload)
    }
    ElMessage.success('已保存')
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存失败')
  } finally {
    submitting.value = false
  }
}

async function onToggle(row: Scene) {
  try {
    await apiUpdateScene(row.id, { status: row.status })
    ElMessage.success(`${row.display_name} 已${row.status === 'active' ? '启用' : '停用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await load()
  }
}

async function remove(row: Scene) {
  try {
    await ElMessageBox.confirm(
      `删除后该场景的配置不可恢复；仍有关联会话时请改为停用。确认删除「${row.display_name}」？`,
      '删除场景',
      { type: 'warning' },
    )
    await apiDeleteScene(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

function copyCode(code: string) {
  navigator.clipboard?.writeText(code)
  ElMessage.success(`已复制：${code}`)
}

function openChat(row: Scene) {
  const base = (import.meta.env.VITE_PUBLIC_CHAT_URL as string) || 'http://localhost:5173'
  window.open(`${base}/?scene=${encodeURIComponent(row.code)}`, '_blank', 'noopener')
}

// ===== 绑定 Agent =====
const bindVisible = ref(false)
const bindLoading = ref(false)
const bindSaving = ref(false)
const bindScene = ref<Scene | null>(null)
const bindOptions = ref<SceneAgentOption[]>([])
// 已选顺序就是绑定顺序，后端按它生成 sort_order
const pickedIds = ref<string[]>([])

const bindTitle = computed(() =>
  bindScene.value ? `绑定 Agent：${bindScene.value.display_name}` : '绑定 Agent',
)
const availableAgents = computed(() =>
  bindOptions.value.filter((a) => !pickedIds.value.includes(a.id)),
)
const pickedAgents = computed(() =>
  pickedIds.value
    .map((id) => bindOptions.value.find((a) => a.id === id))
    .filter((a): a is SceneAgentOption => Boolean(a)),
)

async function openBind(row: Scene) {
  bindScene.value = row
  bindVisible.value = true
  bindLoading.value = true
  try {
    bindOptions.value = await apiSceneAgentOptions(row.id)
    pickedIds.value = bindOptions.value
      .filter((a) => a.bound)
      .sort((a, b) => a.sort_order - b.sort_order)
      .map((a) => a.id)
  } catch (e: any) {
    ElMessage.error(e?.message || '加载可绑定 Agent 失败')
  } finally {
    bindLoading.value = false
  }
}

function pickAgent(id: string) {
  pickedIds.value.push(id)
}

function unpickAgent(index: number) {
  pickedIds.value.splice(index, 1)
}

function moveAgent(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= pickedIds.value.length) return
  const next = [...pickedIds.value]
  const moving = next[index]
  next[index] = next[target]
  next[target] = moving
  pickedIds.value = next
}

async function saveBind() {
  if (!bindScene.value) return
  bindSaving.value = true
  try {
    await apiSetSceneAgents(bindScene.value.id, pickedIds.value)
    ElMessage.success('绑定已保存')
    bindVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存绑定失败')
  } finally {
    bindSaving.value = false
  }
}

// ===== 接入（嵌入 / 公开接入）=====
interface EmbedForm {
  sceneId: string
  sceneCode: string
  title: string
  welcome: string
  placeholder: string
  theme_color: string | null
  position: 'left' | 'right'
  show_branding: boolean
  originsText: string
  allow_anonymous: boolean
  publish_key: string
  api_key_set: boolean
}

// 与后端 DEFAULT_WIDGET_CONFIG 对齐：只在场景没有覆盖值时使用
const embedDefaults = {
  welcome: '您好，请问有什么可以帮您？',
  placeholder: '请输入您的问题',
  theme_color: '#2b5aa0',
}

const embedVisible = ref(false)
const embedLoading = ref(false)
const embedSaving = ref(false)
const rotating = ref(false)
const embedScene = ref<Scene | null>(null)
const rotatedKey = ref('')

const embedForm = reactive<EmbedForm>({
  sceneId: '',
  sceneCode: '',
  title: '',
  welcome: embedDefaults.welcome,
  placeholder: embedDefaults.placeholder,
  theme_color: embedDefaults.theme_color,
  position: 'right',
  show_branding: true,
  originsText: '',
  allow_anonymous: false,
  publish_key: '',
  api_key_set: false,
})

const embedTitle = computed(() =>
  embedScene.value ? `接入：${embedScene.value.display_name}` : '接入配置',
)

// 嵌入页与后端同源部署；本地联调默认指向前台开发服务
const embedBase = computed(() => {
  const env = import.meta.env as Record<string, string | undefined>
  return (env.VITE_EMBED_URL || env.VITE_PUBLIC_CHAT_URL || 'http://localhost:5173').replace(
    /\/$/,
    '',
  )
})

const previewUrl = computed(() =>
  embedForm.sceneCode
    ? `${embedBase.value}/embed.html?scene=${encodeURIComponent(embedForm.sceneCode)}`
    : 'about:blank',
)

// 预览用模拟宿主页：真实加载 widget.js，能直接看到气泡悬浮在哪一侧
const bubblePreviewUrl = computed(() =>
  embedForm.sceneCode
    ? `${embedBase.value}/embed-preview.html?scene=${encodeURIComponent(embedForm.sceneCode)}`
    : 'about:blank',
)

const embedSnippet = computed(() => {
  // 拼接结束标签，避免 SFC 解析器把它当成 script 块结尾
  const close = '<' + '/script>'
  return `<script src="${embedBase.value}/widget.js" data-scene="${embedForm.sceneCode}">${close}`
})

const iframeSnippet = computed(
  () =>
    `<iframe src="${previewUrl.value}" style="width:380px;height:620px;border:0" title="在线客服"></iframe>`,
)

function applyEmbedScene(scene: Scene) {
  embedScene.value = scene
  const cfg = (scene.widget_config || {}) as Record<string, unknown>
  embedForm.sceneId = scene.id
  embedForm.sceneCode = scene.code
  embedForm.title = typeof cfg.title === 'string' ? cfg.title : ''
  embedForm.welcome = typeof cfg.welcome === 'string' ? cfg.welcome : embedDefaults.welcome
  embedForm.placeholder =
    typeof cfg.placeholder === 'string' ? cfg.placeholder : embedDefaults.placeholder
  embedForm.theme_color = typeof cfg.theme_color === 'string' ? cfg.theme_color : null
  embedForm.position = cfg.position === 'left' ? 'left' : 'right'
  embedForm.show_branding = cfg.show_branding !== false
  embedForm.originsText = (scene.allowed_origins || []).join('\n')
  embedForm.allow_anonymous = Boolean(scene.publish_key)
  embedForm.publish_key = scene.publish_key || ''
  embedForm.api_key_set = Boolean(scene.api_key_set)
}

async function openEmbed(row: Scene) {
  rotatedKey.value = ''
  // 列表字段可能被精简：先用行数据顶住，再用详情校准
  applyEmbedScene(row)
  embedVisible.value = true
  embedLoading.value = true
  try {
    applyEmbedScene(await apiGetScene(row.id))
  } catch (e: any) {
    ElMessage.error(e?.message || '读取接入配置失败')
  } finally {
    embedLoading.value = false
  }
}

async function saveEmbed() {
  if (!embedForm.sceneId) return
  const origins = embedForm.originsText
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
  const bad = origins.find((o) => !/^https?:\/\/[^/\s]+$/i.test(o))
  if (bad) {
    ElMessage.warning(`来源格式不正确：${bad}（只写 scheme://host[:port]）`)
    return
  }
  embedSaving.value = true
  try {
    const scene = await apiUpdateSceneWidget(embedForm.sceneId, {
      widget_config: {
        title: embedForm.title.trim(),
        welcome: embedForm.welcome.trim(),
        placeholder: embedForm.placeholder.trim(),
        theme_color: embedForm.theme_color || embedDefaults.theme_color,
        position: embedForm.position,
        show_branding: embedForm.show_branding,
      },
      allowed_origins: origins,
      allow_anonymous: embedForm.allow_anonymous,
    })
    // 回填后端归一化后的结果：非法键/来源在这一步被纠正
    applyEmbedScene(scene)
    ElMessage.success('接入配置已保存')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '保存接入配置失败')
  } finally {
    embedSaving.value = false
  }
}

async function rotateKey() {
  if (!embedScene.value) return
  try {
    await ElMessageBox.confirm(
      '换发后旧 API Key 立即失效，仍在使用旧 Key 的服务端调用会返回 401。确认换发？',
      '换发 API Key',
      { type: 'warning' },
    )
  } catch {
    return
  }
  rotating.value = true
  try {
    const result = await apiRotateSceneKey(embedScene.value.id)
    rotatedKey.value = result.api_key
    embedForm.api_key_set = true
    const row = scenes.value.find((s) => s.id === embedForm.sceneId)
    if (row) row.api_key_set = true
    ElMessage.success('已换发，请立即保存新 Key')
  } catch (e: any) {
    ElMessage.error(e?.message || '换发失败')
  } finally {
    rotating.value = false
  }
}

function copyText(text: string) {
  navigator.clipboard?.writeText(text)
  ElMessage.success('已复制')
}

onMounted(load)
</script>

<style scoped>
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.header-ops {
  display: flex;
  gap: 8px;
}
.scene-name {
  display: flex;
  align-items: center;
  gap: 6px;
}
.scene-name .name {
  font-weight: 600;
}
.code-line {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 2px;
}
.code-line code {
  color: #606266;
  font-size: 12px;
}
.muted {
  color: #909399;
}
.small {
  font-size: 12px;
}
.rule-tip {
  margin-top: 2px;
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
.q-help {
  display: inline-block;
  margin-left: 4px;
  width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
  border-radius: 50%;
  background: #c0c4cc;
  color: #fff;
  font-size: 11px;
  cursor: help;
}
.bind-body {
  min-height: 200px;
}
.bind-columns {
  display: flex;
  gap: 12px;
}
.bind-col {
  flex: 1;
  min-width: 0;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 8px;
}
.bind-col-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.bind-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.bind-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 8px;
  background: #f8f9fb;
  border-radius: 4px;
}
.bind-info {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}
.bind-info .order {
  display: inline-block;
  width: 18px;
  height: 18px;
  line-height: 18px;
  text-align: center;
  border-radius: 50%;
  background: #2b5aa0;
  color: #fff;
  font-size: 11px;
}
.bind-ops {
  display: flex;
  gap: 2px;
}
.embed-body {
  min-height: 200px;
}
.embed-inline {
  margin-left: 8px;
}
.embed-code {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 8px;
}
.embed-item {
  margin-bottom: 12px;
}
.embed-item-title {
  margin-bottom: 4px;
  font-weight: 600;
  font-size: 13px;
}
.embed-code pre,
.embed-pre {
  width: 100%;
  box-sizing: border-box;
  margin: 0;
  padding: 10px 12px;
  background: #0f172a;
  color: #e2e8f0;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
}
.rotate-result {
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px;
  background: #fdf6ec;
  border-radius: 6px;
}
.rotate-result code {
  word-break: break-all;
}
.embed-preview {
  width: 100%;
  height: 560px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #f5f7fa;
}
</style>
