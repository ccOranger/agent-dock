<template>
  <el-card>
    <template #header>
      <div class="card-header">
        <b>固定话术</b>
        <div class="header-ops">
          <el-button size="small" @click="load">刷新</el-button>
          <el-button type="primary" size="small" @click="openCreate">新增话术</el-button>
        </div>
      </div>
    </template>

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
          </template>
          <el-tag v-else size="small" type="info">未命中，本轮交给路由与大模型</el-tag>
        </div>
        <pre v-if="matchResult.reply_text" class="reply-pre">{{ matchResult.reply_text }}</pre>
      </div>
    </el-card>

    <el-table :data="scripts" v-loading="loading">
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-box">
            <div class="expand-title">应答正文（逐字下发，支持 Markdown）</div>
            <pre class="reply-pre">{{ row.reply_text }}</pre>
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

  <el-dialog v-model="dialogVisible" :title="editing ? `编辑话术：${form.key}` : '新增话术'" width="640px">
    <el-form :model="form" label-width="110px">
      <el-form-item label="标识" required>
        <el-input v-model="form.key" :disabled="editing" placeholder="唯一标识，小写字母/数字/下划线，如 business_hours" />
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
      <el-form-item label="应答正文" required>
        <el-input
          v-model="form.reply_text"
          type="textarea"
          :rows="8"
          placeholder="命中后逐字下发给客户的内容，不经过大模型改写"
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
  apiListCannedScripts,
  apiToggleCannedScript,
  apiUpdateCannedScript,
} from '@/api'
import type { CannedMatchResult, CannedScript, CannedScriptCategory, CannedScriptPayload } from '@/types'
import { CANNED_CATEGORY_LABELS } from '@/types'

const scripts = ref<CannedScript[]>([])
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

// 表单里 title 用空串表示“未填”，提交时再转成 null，避免可空字段到处判空
type ScriptForm = Omit<CannedScriptPayload, 'title'> & { title: string }

const form = reactive<ScriptForm>({
  key: '',
  title: '',
  category: 'business',
  patterns: [],
  keywords: [],
  reply_text: '',
  priority: 100,
  enabled: true,
})

function categoryLabel(category?: CannedScriptCategory | null) {
  return (category && CANNED_CATEGORY_LABELS[category]) || category || '—'
}

const enabledCount = computed(() => scripts.value.filter((s) => s.enabled).length)

function oneLine(text: string) {
  return (text || '').replace(/\s+/g, ' ').trim().slice(0, 60)
}

async function load() {
  loading.value = true
  try {
    scripts.value = await apiListCannedScripts()
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
    matchResult.value = await apiCannedScriptMatch(query)
  } catch (e: any) {
    ElMessage.error(e?.message || '命中测试失败')
  } finally {
    matching.value = false
  }
}

function resetForm() {
  form.key = ''
  form.title = ''
  form.category = 'business'
  form.patterns = []
  form.keywords = []
  form.reply_text = ''
  form.priority = 100
  form.enabled = true
}

function openCreate() {
  editing.value = false
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: CannedScript) {
  editing.value = true
  form.key = row.key
  form.title = row.title || ''
  form.category = row.category
  form.patterns = [...row.patterns]
  form.keywords = [...row.keywords]
  form.reply_text = row.reply_text
  form.priority = row.priority
  form.enabled = row.enabled
  dialogVisible.value = true
}

async function submit() {
  if (!form.key.trim() || !form.reply_text.trim()) {
    ElMessage.warning('标识与应答正文都要填写')
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
      title: form.title.trim() || null,
      category: form.category,
      patterns: form.patterns,
      keywords: form.keywords,
      reply_text: form.reply_text,
      priority: form.priority,
      enabled: form.enabled,
    }
    if (editing.value) {
      await apiUpdateCannedScript(form.key, payload)
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
    await apiToggleCannedScript(row.key, row.enabled)
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
    await apiDeleteCannedScript(row.key)
    ElMessage.success('已删除')
    await load()
  } catch (e: any) {
    if (e !== 'cancel' && e?.message) ElMessage.error(e.message)
  }
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
.match-card {
  margin-bottom: 12px;
  background: #f8f9fb;
}
.match-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.match-label {
  font-weight: 600;
  white-space: nowrap;
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
  color: #303133;
}
.tag-wrap {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.reply-pre {
  margin: 0;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.builtin-tag {
  margin-left: 6px;
}
.muted {
  color: #909399;
}
.tip {
  margin-left: 10px;
  color: #909399;
  font-size: 12px;
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
.rule-tip {
  margin-top: 2px;
  color: #909399;
  font-size: 12px;
  line-height: 1.4;
}
</style>
