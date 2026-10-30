<template>
  <div class="page">
    <PageHeader
      title="固定话术"
      description="命中规则的话术会在路由与大模型之前逐字接住；支持问法、关键词两种匹配，也可挂知识库做检索拼接。"
      :count="`${enabledCount} / ${scripts.length} 启用`"
    >
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新增话术</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="load" @reset="resetSceneFilter">
        <el-select v-model="sceneFilter" placeholder="全部场景" clearable style="width: 180px" @change="load">
          <el-option label="全部场景" value="" />
          <el-option
            v-for="scene in scenes"
            :key="scene.code"
            :label="scene.display_name"
            :value="scene.code"
          />
        </el-select>
      </FilterBar>

      <el-alert
        type="warning"
        :closable="false"
        show-icon
        title="命中的客户提问会在路由与大模型之前被这句话逐字接住"
        description="匹配只开放两种规则：问法（归一化后整句相同才算命中，不会吞掉带后半句的业务问法）与关键词（句中出现即命中，判定更宽），不开放正则。话术全部存在这张表里（代码里没有兜底规则）：停用或删除某条，这类提问就正常交给路由与大模型。新增关键词请先用下方命中测试验证，误吞的代价是把真实业务诉求用一句模板打发掉。"
        style="margin-bottom: 12px"
      />

      <el-alert
        v-if="!loading && !enabledCount"
        type="info"
        :closable="false"
        show-icon
        title="当前没有任何启用的话术，固定话术能力等于关闭（所有提问都走路由与大模型）"
        style="margin-bottom: 12px"
      />

      <el-card shadow="never" class="match-card">
        <div class="match-row">
          <span class="match-label">命中测试</span>
          <el-input
            v-model="matchQuery"
            placeholder="输入一句客户真实会问的话，例如：请问你是谁呀？"
            clearable
            @keyup.enter="runMatch"
          />
          <el-button type="primary" :loading="matching" @click="runMatch">测试</el-button>
        </div>
        <div v-if="matchResult" class="match-result">
          <div class="match-line">
            <span class="muted">归一化主干：</span>
            <code>{{ matchResult.normalized || '（空）' }}</code>
            <template v-if="matchResult.matched">
              <el-tag size="small" type="success">{{ matchResult.title || matchResult.key }}</el-tag>
              <el-tag size="small" type="info">{{ categoryLabel(matchResult.category) }}</el-tag>
              <el-tag v-if="matchResult.answer_source === 'kb'" size="small" type="warning">
                知识库：{{ (matchResult.kb_namespaces || []).join('、') || '未选库' }}
              </el-tag>
            </template>
            <el-tag v-else size="small" type="info">未命中，本轮交给路由与大模型</el-tag>
          </div>
          <pre v-if="matchResult.reply_text" class="text-block reply-pre">{{ matchResult.reply_text }}</pre>
        </div>
      </el-card>

      <el-table :data="scripts" v-loading="loading">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="expand-box">
              <div class="expand-title">应答正文（逐字下发，支持 Markdown）</div>
              <pre class="text-block reply-pre">{{ row.reply_text }}</pre>
              <div class="expand-title">回答来源</div>
              <div class="tag-wrap">
                <template v-if="row.answer_source === 'kb'">
                  <el-tag size="small" type="warning">知识库检索拼接（不经模型改写）</el-tag>
                  <el-tag v-for="ns in row.kb_namespaces" :key="ns" size="small" type="info">{{ ns }}</el-tag>
                  <span v-if="!row.kb_namespaces.length" class="muted">未选择知识库</span>
                </template>
                <span v-else class="muted">文本逐字下发</span>
              </div>
              <div class="expand-title">问法（{{ row.patterns.length }}）</div>
              <div class="tag-wrap">
                <el-tag v-for="p in row.patterns" :key="p" size="small" type="info">{{ p }}</el-tag>
                <span v-if="!row.patterns.length" class="muted">—</span>
              </div>
              <div class="expand-title">关键词（{{ row.keywords.length }}）</div>
              <div class="tag-wrap">
                <el-tag v-for="k in row.keywords" :key="k" size="small" type="warning">{{ k }}</el-tag>
                <span v-if="!row.keywords.length" class="muted">—</span>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="priority" label="优先级" width="80" sortable />
        <el-table-column label="归属" width="120">
          <template #default="{ row }">
            <el-tag v-if="row.scene_id" size="small" type="success">{{ sceneName(row) }}</el-tag>
            <el-tag v-else size="small" type="info">全局</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="标识" width="150">
          <template #default="{ row }">
            <span>{{ row.key }}</span>
            <el-tooltip
              v-if="row.is_builtin"
              placement="top"
              content="建库时写入的种子话术：可改文案、可停用，不可删除（删掉探听拒答会把系统提示词类提问重新交回模型发挥）"
            >
              <el-tag size="small" type="info" class="builtin-tag">种子</el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column label="分组" width="100">
          <template #default="{ row }">
            <el-tag size="small" :type="row.category === 'probing' ? 'danger' : ''">
              {{ categoryLabel(row.category) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="回答来源" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.answer_source === 'kb'" size="small" type="warning">知识库</el-tag>
            <el-tag v-else size="small" type="info">文本</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="title" label="名称" width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.title || '—' }}</template>
        </el-table-column>
        <el-table-column label="规则" min-width="220">
          <template #default="{ row }">
            <span class="muted">问法 {{ row.patterns.length }} 条 · 关键词 {{ row.keywords.length }} 个</span>
          </template>
        </el-table-column>
        <el-table-column label="正文" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ oneLine(row.reply_text) }}</template>
        </el-table-column>
        <el-table-column label="启用" width="70">
          <template #default="{ row }">
            <el-switch v-model="row.enabled" @change="() => onToggle(row)" />
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button
              v-if="!row.is_builtin"
              link
              type="danger"
              size="small"
              @click="remove(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>

  <el-dialog v-model="dialogVisible" :title="editing ? `编辑话术：${form.key}` : '新增话术'" width="640px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="标识" required>
        <el-input v-model="form.key" :disabled="editing" placeholder="唯一标识，小写字母/数字/下划线，如 business_hours" />
      </el-form-item>
      <el-form-item label="归属场景">
        <el-select v-model="form.scene_code" :disabled="editing" clearable placeholder="全局" style="width: 100%">
          <el-option label="全局（所有场景共用）" value="" />
          <el-option
            v-for="scene in scenes"
            :key="scene.code"
            :label="scene.display_name"
            :value="scene.code"
          />
        </el-select>
        <div class="rule-tip">
          全局话术（如探听拒答）对所有场景生效；场景话术只在绑定场景内生效，且同标识会覆盖全局行。归属创建后不可修改。
        </div>
      </el-form-item>
      <el-form-item label="名称">
        <el-input v-model="form.title" placeholder="可选，后台展示用，如 营业时间" />
      </el-form-item>
      <el-form-item label="分组">
        <el-select v-model="form.category" style="width: 100%">
          <el-option v-for="opt in categoryOptions" :key="opt" :label="opt.label" :value="opt.value" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <template #label>
          问法
          <el-tooltip
            placement="top"
            content="客户会说的完整句子主干。去掉标点和「请问/呀/吗」等前后缀后与整句完全相同才命中，所以「你是谁？我订单三天了还没到」不会被「你是谁」吞掉。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input-tag
          v-model="form.patterns"
          :max="100"
          :token-separators="[',', '，']"
          clearable
          placeholder="输入一句问法后回车添加，如：营业时间"
          style="width: 100%"
        />
        <div class="rule-tip">当前 {{ form.patterns.length }} 条（上限 100）</div>
      </el-form-item>
      <el-form-item>
        <template #label>
          关键词
          <el-tooltip
            placement="top"
            content="整句里出现即命中，判定比问法宽。只用「提示词」「内部指令」这类强特征词；单字关键词会被拒绝，太宽的词请先用命中测试验一遍。上限 60 条，种子里的探听类已经占 32 条。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input-tag
          v-model="form.keywords"
          :max="60"
          :token-separators="[',', '，']"
          clearable
          placeholder="输入关键词后回车添加，如：提示词"
          style="width: 100%"
        />
        <div class="rule-tip">当前 {{ form.keywords.length }} 条（上限 60）</div>
      </el-form-item>
      <el-form-item label="回答来源">
        <el-radio-group v-model="form.answer_source">
          <el-radio value="text">文本（逐字下发）</el-radio>
          <el-radio value="kb">知识库（检索拼接）</el-radio>
        </el-radio-group>
        <div class="rule-tip">
          知识库模式按固定格式拼接检索结果，不经过模型改写，适合“公司介绍/服务范围”这类会变的问答；
          高频且必须逐字一致的“你是谁”仍建议用文本。
        </div>
      </el-form-item>
      <el-form-item v-if="form.answer_source === 'kb'" label="知识库" required>
        <el-select
          v-model="form.kb_namespaces"
          multiple
          clearable
          collapse-tags
          placeholder="选择检索范围（必选）"
          style="width: 100%"
        >
          <el-option
            v-for="kb in activeBases"
            :key="kb.namespace"
            :label="`${kb.display_name}（${kb.namespace}）`"
            :value="kb.namespace"
          />
        </el-select>
        <div class="rule-tip">只能选启用中的库，最多 10 个；留空会被后端拒绝保存。</div>
      </el-form-item>
      <el-form-item
        :label="form.answer_source === 'kb' ? '引导语' : '应答正文'"
        :required="form.answer_source === 'text'"
      >
        <el-input
          v-model="form.reply_text"
          type="textarea"
          :rows="form.answer_source === 'kb' ? 3 : 8"
          :placeholder="
            form.answer_source === 'kb'
              ? '可选：命中后先发这句话，再接检索结果；留空则直接下发检索内容'
              : '命中后逐字下发给客户的内容，不经过大模型改写'
          "
          maxlength="2000"
          show-word-limit
        />
      </el-form-item>
      <el-form-item>
        <template #label>
          优先级
          <el-tooltip
            placement="top"
            content="数字小者优先。多条规则同时命中时，由它决定哪一句应答（建库种子的默认值：探听拒答 10 < 身份 20 < 能力 30 < 业务 100）。"
          >
            <span class="q-help">?</span>
          </el-tooltip>
        </template>
        <el-input-number v-model="form.priority" :min="0" :max="1000" :step="5" />
      </el-form-item>
      <el-form-item label="启用">
        <el-switch v-model="form.enabled" />
        <span class="tip">种子话术可停用、可改文案，但不可删除；停用后这类提问就交给路由与大模型</span>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  apiCannedScriptMatch,
  apiCreateCannedScript,
  apiDeleteCannedScript,
  apiListBases,
  apiListCannedScripts,
  apiSceneOptions,
  apiToggleCannedScript,
  apiUpdateCannedScript,
} from '@/api'
import type {
  CannedAnswerSource,
  CannedMatchResult,
  CannedScript,
  CannedScriptCategory,
  CannedScriptPayload,
  KnowledgeBase,
  SceneOption,
} from '@/types'
import { CANNED_CATEGORY_LABELS } from '@/types'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'

const scripts = ref<CannedScript[]>([])
const scenes = ref<SceneOption[]>([])
// 知识库选择器只要启用中的库：停用库后端会直接拒掉
const activeBases = ref<KnowledgeBase[]>([])
const sceneFilter = ref('')
const loading = ref(false)
const dialogVisible = ref(false)
const editing = ref(false)
const submitting = ref(false)

const matchQuery = ref('')
const matching = ref(false)
const matchResult = ref<CannedMatchResult | null>(null)

const categoryOptions = (Object.keys(CANNED_CATEGORY_LABELS) as CannedScriptCategory[]).map((value) => ({
  value,
  label: CANNED_CATEGORY_LABELS[value],
}))

// 表单里 title 用空串表示“未填”，scene_code 用空串表示“全局”，提交时再转成 null
type ScriptForm = Omit<
  CannedScriptPayload,
  'title' | 'scene_code' | 'answer_source' | 'kb_namespaces'
> & {
  title: string
  scene_code: string
  answer_source: CannedAnswerSource
  kb_namespaces: string[]
}

const form = reactive<ScriptForm>({
  key: '',
  scene_code: '',
  title: '',
  category: 'business',
  patterns: [],
  keywords: [],
  reply_text: '',
  answer_source: 'text',
  kb_namespaces: [],
  priority: 100,
  enabled: true,
})

function categoryLabel(category?: CannedScriptCategory | null) {
  return (category && CANNED_CATEGORY_LABELS[category]) || category || '—'
}

const enabledCount = computed(() => scripts.value.filter((s) => s.enabled).length)

const sceneById = computed(() => new Map(scenes.value.map((s) => [s.id, s])))

function sceneName(row: CannedScript) {
  if (!row.scene_id) return '全局'
  const scene = sceneById.value.get(row.scene_id)
  if (scene) return scene.display_name
  // 停用/归档场景不在启用列表里，用服务端下发的场景码兜底展示
  return row.scene_code ? `${row.scene_code}（已停用）` : '未知场景'
}

function sceneCodeOf(row: CannedScript) {
  // 优先用服务端下发的 scene_code：场景停用/归档后不在 /scenes/options 里，反查会落空
  if (row.scene_code) return row.scene_code
  if (!row.scene_id) return null
  return sceneById.value.get(row.scene_id)?.code || null
}

/** 场景行必须能解析出 scene_code：解析不到就当全局行操作会串场景，直接中止 */
function requireSceneCode(row: CannedScript): string | null {
  if (!row.scene_id) return null
  const code = sceneCodeOf(row)
  if (!code) {
    throw new Error('该场景已停用或归档，无法定位这条话术；请先在「业务场景」启用该场景')
  }
  return code
}

function oneLine(text: string) {
  return (text || '').replace(/\s+/g, ' ').trim().slice(0, 60)
}

function resetSceneFilter() {
  sceneFilter.value = ''
  load()
}

async function load() {
  loading.value = true
  try {
    scripts.value = await apiListCannedScripts(sceneFilter.value || undefined)
  } finally {
    loading.value = false
  }
}

async function runMatch() {
  const query = matchQuery.value.trim()
  if (!query) {
    ElMessage.warning('请先输入一句测试问法')
    return
  }
  matching.value = true
  try {
    matchResult.value = await apiCannedScriptMatch(query, sceneFilter.value || null)
  } catch (e: any) {
    ElMessage.error(e?.message || '命中测试失败')
  } finally {
    matching.value = false
  }
}

function resetForm() {
  form.key = ''
  form.scene_code = sceneFilter.value || ''
  form.title = ''
  form.category = 'business'
  form.patterns = []
  form.keywords = []
  form.reply_text = ''
  form.answer_source = 'text'
  form.kb_namespaces = []
  form.priority = 100
  form.enabled = true
}

function openCreate() {
  editing.value = false
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: CannedScript) {
  let sceneCode: string | null
  try {
    sceneCode = requireSceneCode(row)
  } catch (e: any) {
    ElMessage.error(e?.message || '无法定位话术所属场景')
    return
  }
  editing.value = true
  form.key = row.key
  form.scene_code = sceneCode || ''
  form.title = row.title || ''
  form.category = row.category
  form.patterns = [...row.patterns]
  form.keywords = [...row.keywords]
  form.reply_text = row.reply_text
  form.answer_source = row.answer_source || 'text'
  form.kb_namespaces = [...(row.kb_namespaces || [])]
  form.priority = row.priority
  form.enabled = row.enabled
  dialogVisible.value = true
}

async function submit() {
  if (!form.key.trim()) {
    ElMessage.warning('标识要填写')
    return
  }
  if (form.answer_source === 'text' && !form.reply_text.trim()) {
    ElMessage.warning('文本模式必须填写应答正文')
    return
  }
  if (form.answer_source === 'kb' && !form.kb_namespaces.length) {
    ElMessage.warning('知识库模式至少选择一个知识库')
    return
  }
  if (!form.patterns.length && !form.keywords.length) {
    ElMessage.warning('问法与关键词至少填一项，否则这条话术永远不会命中')
    return
  }
  submitting.value = true
  try {
    const payload: CannedScriptPayload = {
      key: form.key.trim(),
      scene_code: form.scene_code || null,
      title: form.title.trim() || null,
      category: form.category,
      patterns: form.patterns,
      keywords: form.keywords,
      reply_text: form.reply_text,
      answer_source: form.answer_source,
      // 切回文本时保留已选库（后端也允许），便于来回切换
      kb_namespaces: form.kb_namespaces,
      priority: form.priority,
      enabled: form.enabled,
    }
    if (editing.value) {
      await apiUpdateCannedScript(form.key, payload, form.scene_code || null)
    } else {
      await apiCreateCannedScript(payload)
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

async function onToggle(row: CannedScript) {
  try {
    await apiToggleCannedScript(row.key, row.enabled, requireSceneCode(row))
    ElMessage.success(`${row.title || row.key} 已${row.enabled ? '启用' : '停用'}`)
  } catch (e: any) {
    ElMessage.error(e?.message || '操作失败')
    await load()
  }
}

async function remove(row: CannedScript) {
  try {
    await ElMessageBox.confirm(
      `这句话术删掉后，对应的客户提问就交回路由与大模型回答，确认删除「${row.title || row.key}」？`,
      '删除话术',
      { type: 'warning' },
    )
    await apiDeleteCannedScript(row.key, requireSceneCode(row))
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
}

onMounted(async () => {
  scenes.value = await apiSceneOptions()
  await load()
  try {
    const bases = await apiListBases()
    activeBases.value = bases.filter((b) => b.is_active)
  } catch {
    activeBases.value = []
  }
})
</script>

<style scoped>
.match-card {
  margin-bottom: 12px;
  background: var(--cs-surface-sub);
}

.match-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.match-label {
  font-weight: 600;
  white-space: nowrap;
  color: var(--cs-text-1);
}

.match-result {
  margin-top: 10px;
}

.match-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.expand-box {
  padding: 8px 16px;
}

.expand-title {
  margin: 8px 0 4px;
  font-weight: 600;
  color: var(--cs-text-1);
}

.tag-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* 应答正文 / 检索片段：与全局 .text-block 一致，仅缩小字号适配表格展开行 */
.reply-pre {
  font-size: 12px;
}

.builtin-tag {
  margin-left: 6px;
}

.tip {
  margin-left: 10px;
  color: var(--cs-text-3);
  font-size: 12px;
}

/* 表单里的 ? 帮助标记 */
.q-help {
  display: inline-block;
  margin-left: 4px;
  width: 14px;
  height: 14px;
  line-height: 14px;
  text-align: center;
  border-radius: 50%;
  background: var(--cs-info);
  color: #fff;
  font-size: 11px;
  cursor: help;
}

.rule-tip {
  margin-top: 2px;
  color: var(--cs-text-3);
  font-size: 12px;
  line-height: 1.5;
}
</style>
