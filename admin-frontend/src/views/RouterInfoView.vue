<template>
  <div class="router-view">
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="路由分发器由代码与 backend/.env 定义，此处仅供查看"
      description="它不做业务应答，因此不在 Agent 管理列表中，也没有后台修改入口。要调整模型、意图上限或置信度阈值，请改 backend/.env 后重启后端。"
      class="notice"
    />

    <el-card shadow="never" class="block" v-loading="loading">
      <template #header>
        <div class="card-header">
          <span>基本信息</span>
          <el-button size="small" text @click="load">刷新</el-button>
        </div>
      </template>
      <el-descriptions v-if="info" :column="2" border>
        <el-descriptions-item label="名称">{{ info.name }}</el-descriptions-item>
        <el-descriptions-item label="定义来源">
          <el-tag size="small" type="info">代码</el-tag>
          <span class="muted path">{{ info.definition_files.agent }}</span>
        </el-descriptions-item>
        <el-descriptions-item label="职责" :span="2">{{ info.responsibility }}</el-descriptions-item>
        <el-descriptions-item label="使用模型">
          <span class="value">{{ info.model }}</span>
          <span class="muted env">（环境变量 {{ info.env_keys.model }}）</span>
          <div class="muted hint">路由模型通常用轻量模型以压低首轮延迟，需与 LLM provider 匹配</div>
        </el-descriptions-item>
        <el-descriptions-item label="单条消息最多意图数">
          <span class="value">{{ info.max_intents }}</span>
          <span class="muted env">（环境变量 {{ info.env_keys.max_intents }}）</span>
          <div class="muted hint">多意图串行执行上限，设为 1 即关闭多意图分发</div>
        </el-descriptions-item>
        <el-descriptions-item label="置信度阈值" :span="2">
          <span class="value">{{ info.confidence_threshold }}</span>
          <span class="muted env">（环境变量 {{ info.env_keys.confidence_threshold }}）</span>
          <div class="muted hint">{{ info.confidence_threshold_effect }}</div>
        </el-descriptions-item>
        <el-descriptions-item label="兜底策略" :span="2">
          优先候选 <span class="value">{{ info.fallback_candidates.join('、') || '—' }}</span>
          <span class="muted">·</span>
          当前实际兜底
          <el-tag size="small" :type="preview && !preview.fallback_from_candidates ? 'warning' : 'success'">
            {{ preview?.fallback_agent || '—' }}
          </el-tag>
          <span class="muted">·</span>
          兜底意图（置信度 {{ info.fallback_confidence }}）
          <el-tag size="small" :type="info.fallback_answer ? 'success' : 'warning'">
            {{ info.fallback_answer ? '由兜底 Agent 应答' : '转人工' }}
          </el-tag>
          <span class="muted env">（环境变量 {{ info.env_keys.fallback_answer }}）</span>
          <div class="muted hint">{{ info.fallback_answer_effect }}</div>
          <div class="muted hint">
            兜底 Agent 由当前生效清单实时派生：优先候选被后台禁用时自动降级到清单首个 Agent，
            因此提示词里的兜底名不会指向不可用的 Agent
          </div>
        </el-descriptions-item>
        <el-descriptions-item label="对话上下文注入">
          <span class="value">最近 {{ info.history_turns }} 轮 · 单条截断 {{ info.history_chars }} 字</span>
          <div class="muted hint">用于“那什么时候到”这类追问的续接判定</div>
        </el-descriptions-item>
        <el-descriptions-item label="路由线索注入上限">
          <span class="value">关键词 {{ info.keywords_limit }} 个 · 典型问法 {{ info.examples_limit }} 条</span>
          <div class="muted hint">每个 Agent 只注入前若干条，Agent 变多时提示词体积不会线性膨胀</div>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <el-card shadow="never" class="block">
      <el-tabs v-model="tab">
        <el-tab-pane label="当前生效提示词" name="prompt">
          <div class="toolbar">
            <span class="muted">
              候选 Agent {{ preview?.candidate_count ?? 0 }} 个 · 提示词 {{ preview?.prompt_chars ?? 0 }} 字
            </span>
            <el-button size="small" :disabled="!preview" @click="copyPrompt">复制</el-button>
          </div>
          <div class="muted hint">
            以下是按当前库中 Agent 渲染的结果（对话上下文与上一轮承接 Agent 均为空）；
            真实每轮请求还会把当轮的最近对话、上一轮 Agent 与当前用户消息一并送入。
          </div>
          <pre class="prompt-pre">{{ preview?.prompt || '加载中…' }}</pre>
        </el-tab-pane>

        <el-tab-pane label="候选 Agent 清单" name="candidates">
          <div class="muted hint">
            这张表就是路由能看到的“花名册”：被禁用的代码 Agent 与未启用的动态 Agent 不会出现在这里；
            线索完整度为「仅描述」的 Agent 在提示词里只有一行适用范围，抢注意愿天然更弱。
          </div>
          <el-table :data="preview?.candidates || []" size="small" border>
            <el-table-column label="Agent" min-width="170">
              <template #default="{ row }">
                <div class="agent-name">{{ row.name }}</div>
                <el-tag size="small" :type="row.source === 'code' ? 'info' : 'success'">
                  {{ row.source === 'code' ? '代码' : '动态' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="适用范围（描述）" min-width="220">
              <template #default="{ row }">
                <span class="clamp">{{ row.description }}</span>
              </template>
            </el-table-column>
            <el-table-column label="关键词" min-width="220">
              <template #default="{ row }">
                <el-tag v-for="k in row.keywords_injected" :key="k" size="small" class="kw">{{ k }}</el-tag>
                <el-tooltip
                  v-if="row.keywords_total > row.keywords_injected.length"
                  :content="`仅前 ${info?.keywords_limit ?? 12} 个注入提示词，其余 ${row.keywords_total - row.keywords_injected.length} 个未进入`"
                >
                  <el-tag size="small" type="warning" class="kw">+{{ row.keywords_total - row.keywords_injected.length }}</el-tag>
                </el-tooltip>
                <span v-if="!row.keywords_total" class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="典型问法" min-width="220">
              <template #default="{ row }">
                <div v-for="e in row.examples_injected" :key="e" class="example">{{ e }}</div>
                <span v-if="!row.examples_total" class="muted">—</span>
              </template>
            </el-table-column>
            <el-table-column label="线索完整度" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="CLUE_TAG[row.clue_level as RouterClueLevel]">
                  {{ CLUE_LABELS[row.clue_level as RouterClueLevel] }}
                </el-tag>
              </template>
            </el-table-column>
          </el-table>
        </el-tab-pane>

        <el-tab-pane label="提示词模板与占位符" name="template">
          <div class="muted hint">
            模板由代码维护（{{ info?.definition_files.prompt || '—' }}），其中的占位符每轮由系统填入实时数据；
            规则段不硬编码任何具体 Agent 名称，新增业务 Agent 只需配好线索即可被路由到。
          </div>
          <el-table v-if="info" :data="info.placeholders" size="small" border class="ph-table">
            <el-table-column prop="name" label="占位符" width="160" />
            <el-table-column prop="desc" label="含义" />
          </el-table>
          <pre class="prompt-pre">{{ info?.system_prompt_template || '加载中…' }}</pre>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { apiGetRouterInfo, apiGetRouterPrompt } from '@/api'
import type { RouterAgentInfo, RouterClueLevel, RouterPromptPreview } from '@/types'

const info = ref<RouterAgentInfo | null>(null)
const preview = ref<RouterPromptPreview | null>(null)
const loading = ref(false)
const tab = ref('prompt')

const CLUE_LABELS: Record<RouterClueLevel, string> = {
  full: '完整',
  keywords_only: '仅关键词',
  examples_only: '仅问法',
  description_only: '仅描述',
}
const CLUE_TAG: Record<RouterClueLevel, 'success' | 'warning' | 'info'> = {
  full: 'success',
  keywords_only: 'warning',
  examples_only: 'warning',
  description_only: 'info',
}

async function load() {
  loading.value = true
  try {
    const [i, p] = await Promise.all([apiGetRouterInfo(), apiGetRouterPrompt()])
    info.value = i
    preview.value = p
  } catch (e: any) {
    ElMessage.error(e?.message || '加载路由信息失败')
  } finally {
    loading.value = false
  }
}

async function copyPrompt() {
  const text = preview.value?.prompt || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制当前生效提示词')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中')
  }
}

onMounted(load)
</script>

<style scoped>
.notice {
  margin-bottom: 12px;
}
.block {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.muted {
  color: #909399;
}
.hint {
  font-size: 12px;
  line-height: 1.5;
  margin: 4px 0;
}
.env {
  font-size: 12px;
  margin-left: 4px;
}
.path {
  font-size: 12px;
}
.clamp {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.value {
  font-weight: 600;
}
.agent-name {
  font-weight: 600;
  margin-bottom: 4px;
}
.kw {
  margin: 0 4px 4px 0;
}
.example {
  font-size: 12px;
  line-height: 1.6;
}
.ph-table {
  margin-bottom: 12px;
}
.prompt-pre {
  margin: 8px 0 0;
  max-height: 420px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  line-height: 1.6;
  background: #f8f9fb;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 12px;
}
</style>
