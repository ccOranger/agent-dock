<template>
  <div class="page">
    <PageHeader
      title="自定义工具"
      :count="tools.length ? `${tools.length} 个` : undefined"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新目录</el-button>
        <el-button type="primary" @click="openCreate">新建动态工具</el-button>
      </template>
    </PageHeader>

    <el-card>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        title="两种来源：①「代码」工具实现位于 app/tools/custom/，需在代码目录开发并重启后端，此处只做登记与开关；②「动态」工具直接在本页在线编写 Python 源码，保存后下一轮对话即生效，适合按客户业务快速接入。"
        style="margin-bottom: 12px"
      />

      <el-table :data="tools" v-loading="loading">
        <el-table-column type="expand">
          <template #default="{ row }">
            <pre class="text-block mono">{{ JSON.stringify(row.parameters, null, 2) }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="name" label="名称" width="180" />
        <el-table-column label="描述" show-overflow-tooltip min-width="200">
          <template #default="{ row }">{{ row.display_description || row.description }}</template>
        </el-table-column>
        <el-table-column label="参数" show-overflow-tooltip min-width="140">
          <template #default="{ row }">
            <span class="muted">{{ paramNames(row.parameters) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="96">
          <template #default="{ row }">
            <el-tag :type="row.source === 'dynamic' ? 'warning' : 'info'" size="small">
              {{ row.source === 'dynamic' ? '动态' : '代码' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="登记状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.registered ? 'success' : 'info'" size="small">
              {{ row.registered ? '已登记' : '未登记' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用" width="80">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" :disabled="!row.registered" @change="() => onToggle(row)" />
          </template>
        </el-table-column>
        <el-table-column label="源码更新" width="168">
          <template #default="{ row }">
            <span class="muted">{{ row.code_updated_at ? fmt(row.code_updated_at) : '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="168" fixed="right">
          <template #default="{ row }">
            <el-button v-if="row.source === 'code' && !row.registered" link type="primary" size="small" @click="openRegister(row)">
              登记
            </el-button>
            <template v-else-if="row.source === 'dynamic'">
              <el-button link type="primary" size="small" @click="openEditor(row)">编辑代码</el-button>
              <el-button link type="danger" size="small" @click="unregister(row)">删除</el-button>
            </template>
            <el-button v-else link type="danger" size="small" @click="unregister(row)">取消登记</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <!-- 代码目录工具登记 -->
  <el-dialog v-model="dialogVisible" title="登记自定义工具" width="480px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="名称">
        <el-input :model-value="form.name" disabled />
      </el-form-item>
      <el-form-item label="代码描述">
        <div class="desc-text">{{ currentDescription }}</div>
      </el-form-item>
      <el-form-item label="覆盖显示描述">
        <el-input
          v-model="form.description"
          type="textarea"
          :rows="2"
          placeholder="可选，仅用于后台展示；留空则显示代码描述"
        />
        <div class="muted hint">仅影响后台列表展示，模型看到的工具描述始终以代码里的 description 为准。</div>
      </el-form-item>
      <el-form-item label="登记后启用">
        <el-switch v-model="form.enabled" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submitRegister">登记</el-button>
    </template>
  </el-dialog>

  <!-- 动态工具源码编辑 -->
  <el-dialog
    v-model="editorVisible"
    :title="editorMode === 'create' ? '新建动态工具' : `编辑源码：${editorName}`"
    width="960px"
    top="6vh"
    :close-on-click-modal="false"
  >
    <el-form :model="editor" label-width="110px">
      <el-form-item label="工具名称" required>
        <el-input v-model="editor.name" :disabled="editorMode === 'edit'" placeholder="小写字母开头，仅 a-z 0-9 _，例如 query_membership" />
        <div class="muted hint">
          需与源码里 class 的 name 类属性完全一致；名称全局唯一，且不能与代码目录工具或 MCP 工具重名。
        </div>
      </el-form-item>
      <el-form-item label="覆盖显示描述">
        <el-input v-model="editor.description" type="textarea" :rows="2" placeholder="可选，仅用于后台列表展示" />
      </el-form-item>
      <el-form-item v-if="editorMode === 'create'" label="创建后启用">
        <el-switch v-model="editor.enabled" />
      </el-form-item>
      <el-form-item label="Python 源码" required>
        <el-input
          v-model="editor.code"
          type="textarea"
          :rows="22"
          resize="vertical"
          class="code-input"
          spellcheck="false"
          placeholder="class XxxTool(BaseTool): ..."
          @keydown="onCodeKeydown"
        />
        <div class="muted hint">
          写法：定义 BaseTool 子类并声明 name / description / parameters（JSON Schema），实现
          <code>async def run(self, ...)</code>。BaseTool 由系统注入，无需 import；若要接收会话身份，在
          parameters 里声明 user_id / session_id，执行时由系统自动注入（模型无需填写）。
        </div>
        <div class="muted hint">
          安全限制：只允许 json / datetime / re / math / collections / httpx 等白名单库（见
          <code>app/tools/tool_security.py</code>），禁止文件读写、os/subprocess、eval/exec 与反射逃逸；
          源码上限 400 行，执行默认 30s 超时（可用类属性 timeout 调整，上限 120s）。
        </div>
        <el-button link type="primary" size="small" @click="useTemplate">填入示例模板</el-button>
      </el-form-item>
    </el-form>

    <el-alert v-if="validateResult && !validateResult.ok" type="error" :closable="false" show-icon class="result-alert">
      <template #title>校验未通过</template>
      <ul class="err-list">
        <li v-for="(e, i) in validateResult.errors" :key="i">{{ e }}</li>
      </ul>
    </el-alert>
    <el-alert v-else-if="validateResult && validateResult.ok" type="success" :closable="false" show-icon class="result-alert">
      <template #title>
        校验通过：{{ validateResult.name }} — {{ validateResult.description }}（参数：{{ paramNames(validateResult.parameters || {}) }}）
      </template>
    </el-alert>
    <div v-if="testResult !== null" class="result-block">
      <div class="result-title">试运行{{ testResult.ok ? '成功' : '失败' }}</div>
      <pre class="text-block mono">{{ testResultText }}</pre>
    </div>

    <template #footer>
      <div class="footer-row">
        <el-button :loading="validating" @click="doValidate">校验</el-button>
        <el-button :loading="testing" @click="openTestRun">试运行…</el-button>
        <span class="spacer" />
        <el-button @click="editorVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitEditor">
          {{ editorMode === 'create' ? '创建并登记' : '保存源码' }}
        </el-button>
      </div>
    </template>
  </el-dialog>

  <!-- 试运行入参 -->
  <el-dialog v-model="testDialogVisible" title="试运行入参（JSON）" width="520px" append-to-body>
    <el-input v-model="testArgs" type="textarea" :rows="8" class="code-input" spellcheck="false" />
    <div class="muted hint">
      按工具 parameters 的字段填写；会产生真实副作用（建单/改单类工具会写数据），执行身份为
      <code>admin-test:&lt;当前操作员&gt;</code>。
    </div>
    <template #footer>
      <el-button @click="testDialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="testing" @click="doTestRun">执行</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  apiCreateDynamicCustomTool,
  apiDeleteCustomTool,
  apiGetCustomToolCode,
  apiListCustomTools,
  apiRegisterCustomTool,
  apiTestRunCustomTool,
  apiToggleCustomTool,
  apiUpdateCustomToolCode,
  apiValidateCustomToolCode,
} from '@/api'
import type { CustomToolSpec, CustomToolTestRunResult, CustomToolValidateResult } from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const tools = ref<CustomToolSpec[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const submitting = ref(false)

const form = reactive<{ name: string; description: string; enabled: boolean }>({
  name: '',
  description: '',
  enabled: true,
})
const currentDescription = ref('')

// ---- 动态工具编辑 ----
const editorVisible = ref(false)
const editorMode = ref<'create' | 'edit'>('create')
const editorName = computed(() => editor.name)
const saving = ref(false)
const validating = ref(false)
const testing = ref(false)
const validateResult = ref<CustomToolValidateResult | null>(null)
const testResult = ref<CustomToolTestRunResult | null>(null)
const testDialogVisible = ref(false)
const testArgs = ref('{}')

const editor = reactive<{ name: string; description: string; code: string; enabled: boolean }>({
  name: '',
  description: '',
  code: '',
  enabled: true,
})

const CODE_TEMPLATE = `class my_tool(BaseTool):
    # name 必须与左侧「工具名称」一致；description 是模型决定是否调用的唯一依据
    name = "my_tool"
    description = "根据关键词查询会员等级。仅在客户询问会员等级/权益时调用。"
    parameters = {
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "会员号或手机号"},
        },
        "required": ["keyword"],
        "additionalProperties": False,
    }
    # 可选：覆盖默认 30s 执行超时（上限 120s）
    # timeout = 10

    async def run(self, keyword: str):
        # 这里写业务逻辑：可用 json / datetime / re / math / httpx 等白名单库
        table = {"13800000000": "金卡"}
        return {"ok": True, "data": {"keyword": keyword, "level": table.get(keyword, "普通卡")}}
`

function fmt(v?: string | null) {
  return v ? dayjs(v).format('YYYY-MM-DD HH:mm:ss') : '—'
}

function paramNames(params: Record<string, any>): string {
  const props = params?.properties
  if (!props) return '—'
  const names = Object.keys(props)
  return names.length ? names.join('、') : '无参数'
}

const testResultText = computed(() => {
  if (!testResult.value) return ''
  const r = testResult.value
  return r.ok ? JSON.stringify(r.result, null, 2) : r.error || '执行失败'
})

async function load() {
  loading.value = true
  try {
    tools.value = await apiListCustomTools()
  } finally {
    loading.value = false
  }
}

function openRegister(row: CustomToolSpec) {
  form.name = row.name
  form.description = ''
  form.enabled = true
  currentDescription.value = row.description
  dialogVisible.value = true
}

async function submitRegister() {
  submitting.value = true
  try {
    await apiRegisterCustomTool({
      name: form.name,
      description: form.description || undefined,
      enabled: form.enabled,
    })
    ElMessage.success(`已登记：${form.name}`)
    dialogVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.message || '登记失败')
  } finally {
    submitting.value = false
  }
}

async function onToggle(row: CustomToolSpec) {
  try {
    await apiToggleCustomTool(row.name, row.enabled)
    ElMessage.success(`${row.name} 已${row.enabled ? '启用' : '禁用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await load()
  }
}

async function unregister(row: CustomToolSpec) {
  const isDynamic = row.source === 'dynamic'
  const tip = isDynamic
    ? `确认删除动态工具「${row.name}」？源码一并删除，引用它的 Agent 需要重新绑定。`
    : `确认取消登记「${row.name}」？`
  try {
    await ElMessageBox.confirm(tip, isDynamic ? '删除动态工具' : '取消登记', { type: 'warning' })
    await apiDeleteCustomTool(row.name)
    ElMessage.success(isDynamic ? '已删除' : '已取消登记')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

// ---- 动态工具：编辑器 ----
function resetEditor(mode: 'create' | 'edit', name = '', code = '', description = '') {
  editorMode.value = mode
  editor.name = name
  editor.code = code
  editor.description = description
  editor.enabled = true
  validateResult.value = null
  testResult.value = null
  testArgs.value = '{}'
  editorVisible.value = true
}

function openCreate() {
  resetEditor('create', '', '', '')
  editor.name = 'query_'
  editor.code = CODE_TEMPLATE
}

async function openEditor(row: CustomToolSpec) {
  try {
    const detail = await apiGetCustomToolCode(row.name)
    resetEditor('edit', detail.name, detail.code, detail.description || '')
  } catch (e: any) {
    ElMessage.error(e?.message || '读取源码失败')
  }
}

function useTemplate() {
  editor.code = CODE_TEMPLATE
  validateResult.value = null
}

/** textarea 里 Tab 键补 4 空格：Python 缩进用真实 Tab 会与空格混用而报 IndentationError */
function onCodeKeydown(e: KeyboardEvent) {
  if (e.key !== 'Tab') return
  e.preventDefault()
  const el = e.target as HTMLTextAreaElement
  const { selectionStart: start, selectionEnd: end, value } = el
  editor.code = value.slice(0, start) + '    ' + value.slice(end)
  requestAnimationFrame(() => {
    el.selectionStart = el.selectionEnd = start + 4
  })
}

async function doValidate() {
  if (!requireNameAndCode()) return
  validating.value = true
  try {
    validateResult.value = await apiValidateCustomToolCode({ code: editor.code, name: editor.name })
    if (validateResult.value.ok) ElMessage.success('校验通过，可以保存')
  } catch (e: any) {
    ElMessage.error(e?.message || '校验请求失败')
  } finally {
    validating.value = false
  }
}

function requireNameAndCode() {
  if (!editor.name.trim()) {
    ElMessage.warning('请先填写工具名称')
    return false
  }
  if (!editor.code.trim()) {
    ElMessage.warning('请先填写源码')
    return false
  }
  return true
}

function openTestRun() {
  if (!requireNameAndCode()) return
  testDialogVisible.value = true
}

async function doTestRun() {
  testing.value = true
  let args: Record<string, unknown> = {}
  try {
    args = testArgs.value.trim() ? JSON.parse(testArgs.value) : {}
  } catch {
    ElMessage.error('入参不是合法 JSON')
    testing.value = false
    return
  }
  try {
    testResult.value = await apiTestRunCustomTool({ name: editor.name, code: editor.code, args })
    testDialogVisible.value = false
    validateResult.value = null
  } catch (e: any) {
    ElMessage.error(e?.message || '试运行请求失败')
  } finally {
    testing.value = false
  }
}

async function submitEditor() {
  if (!requireNameAndCode()) return
  saving.value = true
  try {
    if (editorMode.value === 'create') {
      await apiCreateDynamicCustomTool({
        name: editor.name.trim(),
        code: editor.code,
        description: editor.description || undefined,
        enabled: editor.enabled,
      })
      ElMessage.success(`已创建动态工具：${editor.name}，可在动态 Agent 中绑定`)
    } else {
      await apiUpdateCustomToolCode(editor.name, { code: editor.code, description: editor.description })
      ElMessage.success('源码已保存，下一轮对话即生效')
    }
    editorVisible.value = false
    await load()
  } catch (e: any) {
    // 后端把安全/编译错误明细放在 detail.message 里，需要展开成多行才看得清
    if (e?.message) ElMessage.error(e.message)
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.desc-text {
  color: var(--cs-text-2);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
}

/* 参数 schema 展开行：等宽字体 + 柔和底 */
.text-block.mono {
  font-size: 12px;
  line-height: 1.55;
  word-break: break-all;
}

/* 源码/JSON 输入：等宽字体，Python 缩进依赖字体对齐 */
.code-input :deep(textarea) {
  font-family: var(--cs-font-mono, ui-monospace, 'JetBrains Mono', Consolas, monospace);
  font-size: 12.5px;
  line-height: 1.6;
  tab-size: 4;
}

.result-alert {
  margin-top: 4px;
}

.err-list {
  margin: 4px 0 0;
  padding-left: 18px;
  font-size: 12.5px;
  line-height: 1.7;
}

.result-block {
  margin-top: 10px;
}

.result-title {
  font-size: 13px;
  color: var(--cs-text-2);
  margin-bottom: 4px;
}

.footer-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.footer-row .spacer {
  flex: 1;
}
</style>
