<template>
  <div class="page">
    <PageHeader
      title="路由档案"
      :count="scenes.length ? `${scenes.length} 个场景` : undefined"
    >
      <template #actions>
        <el-button :loading="listLoading" @click="loadScenes(true)">刷新</el-button>
      </template>
    </PageHeader>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="标「继承」表示取全局默认（backend/.env），标「覆盖」表示该场景单独配置"
    />

    <div class="layout">
      <!-- 左：场景列表 -->
      <el-card shadow="never" class="scene-list" v-loading="listLoading">
        <template #header>
          <div class="card-head">
            <span class="card-head-title">场景路由</span>
            <el-button size="small" text @click="loadScenes(true)">刷新</el-button>
          </div>
        </template>
        <el-empty v-if="!scenes.length" description="还没有场景" :image-size="60" />
        <div
          v-for="s in scenes"
          :key="s.scene_code"
          class="scene-item"
          :class="{ active: s.scene_code === selectedCode }"
          @click="selectScene(s.scene_code)"
        >
          <div class="scene-row">
            <span class="scene-name">{{ s.scene_name }}</span>
            <el-tag v-if="s.is_default" size="small" type="info">默认</el-tag>
            <el-tag v-if="s.scene_status !== 'active'" size="small" type="danger">停用</el-tag>
          </div>
          <div class="scene-meta">{{ s.scene_code }} · 候选 {{ s.candidate_count }} 个</div>
          <div class="scene-tags">
            <el-tag size="small" :type="modeTag(s.mode)">{{ modeLabel(s.mode) }}</el-tag>
            <el-tag v-if="s.warnings.length" size="small" type="warning">
              {{ s.warnings.length }} 项提醒
            </el-tag>
          </div>
        </div>
      </el-card>

      <!-- 右：档案详情 -->
      <div class="detail" v-loading="detailLoading">
        <el-empty v-if="!detail" description="选择左侧场景查看路由档案" />

        <template v-else>
          <el-card shadow="never" class="block">
            <template #header>
              <div class="card-head wrap">
                <div class="title-line">
                  <span class="card-head-title">{{ detail.scene_name }}</span>
                  <span class="muted code">{{ detail.scene_code }}</span>
                  <el-tag v-if="detail.is_default" size="small" type="info">默认场景</el-tag>
                  <el-tag v-if="detail.scene_status !== 'active'" size="small" type="danger">已停用</el-tag>
                  <el-tag size="small" :type="modeTag(detail.mode)">{{ modeLabel(detail.mode) }}</el-tag>
                </div>
                <div class="muted hash">提示词指纹 <code>{{ detail.prompt_hash }}</code></div>
              </div>
            </template>

            <el-alert v-if="detail.warnings.length" type="warning" :closable="false" show-icon class="warn">
              <template #title>{{ detail.warnings.length }} 项路由提醒</template>
              <div v-for="w in detail.warnings" :key="w" class="warn-line">· {{ warningLabel(w) }}</div>
            </el-alert>

            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="生效方式">
                <span class="value">{{ modeLabel(detail.mode) }}</span>
                <span class="muted">（场景配置：{{ modeLabel(detail.configured_mode) }}）</span>
              </el-descriptions-item>
              <el-descriptions-item label="直连目标">
                <span class="value">{{ detail.direct_agent || '—' }}</span>
                <span v-if="detail.mode === 'direct'" class="muted">（跳过路由模型）</span>
              </el-descriptions-item>
              <el-descriptions-item label="路由模型">
                <span class="value">{{ detail.model }}</span>
                <el-tag size="small" :type="sourceTag(detail.model_source)" class="src">
                  {{ detail.model_source === 'scene' ? '覆盖' : '继承' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="置信度阈值">
                <span class="value">{{ detail.confidence_threshold }}</span>
                <el-tag size="small" :type="sourceTag(detail.threshold_source)" class="src">
                  {{ detail.threshold_source === 'scene' ? '覆盖' : '继承' }}
                </el-tag>
                <div class="muted hint">主意图低于该值整轮转人工；次要意图不达标只丢弃该分支</div>
              </el-descriptions-item>
              <el-descriptions-item label="单条消息最多意图数">
                <span class="value">{{ detail.max_intents }}</span>
                <el-tag size="small" :type="sourceTag(detail.max_intents_source)" class="src">
                  {{ detail.max_intents_source === 'scene' ? '覆盖' : '继承' }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="兜底 Agent">
                <span class="value">{{ detail.fallback_agent || '—' }}</span>
                <el-tag size="small" type="info" class="src">{{ fallbackLabel(detail.fallback_source) }}</el-tag>
                <div class="muted hint">兜底必须在本场景候选池内；配置失效时会自动降级并在此说明</div>
              </el-descriptions-item>
              <el-descriptions-item label="兜底话术" :span="2">
                <span>{{ detail.fallback_text }}</span>
                <el-tag size="small" :type="detail.fallback_text_source === 'scene' ? 'success' : 'info'" class="src">
                  {{ detail.fallback_text_source === 'scene' ? '场景配置' : '全局默认' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </el-card>

          <el-card shadow="never" class="block">
            <el-tabs v-model="tab">
              <el-tab-pane label="提示词" name="prompt">
                <div class="toolbar">
                  <span class="muted">
                    候选 {{ promptInfo?.candidate_count ?? 0 }} 个 · {{ promptInfo?.prompt_chars ?? 0 }} 字 ·
                    指纹 {{ promptInfo?.prompt_hash || '—' }}
                  </span>
                  <el-button size="small" :disabled="!promptInfo" @click="copyPrompt">复制</el-button>
                </div>
                <div class="muted hint">
                  这是不含对话上下文的基准提示词；真实每轮还会注入最近对话、上一轮承接 Agent 与当前消息。
                  修改候选线索或场景规则后，指纹会变化，可据此对比链路里的路由配置。
                </div>
                <pre class="text-block prompt-pre">{{ promptInfo?.prompt || '加载中…' }}</pre>
                <div class="muted hint">场景约束段（注入到提示词开头）</div>
                <pre class="text-block prompt-pre small">{{ promptInfo?.scene_block || '—' }}</pre>
              </el-tab-pane>

              <el-tab-pane label="候选 Agent" name="candidates">
                <div class="muted hint">
                  这张表就是本场景路由能看到的“花名册”：停用的 Agent 与其它场景专属的 Agent 不会出现；
                  线索完整度为「仅描述」的 Agent 在提示词里只有一行适用范围，抢注意愿天然更弱。
                </div>
                <el-table :data="detail.candidates || []" size="small" border>
                  <el-table-column label="Agent" min-width="168">
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
                        :content="`仅前 ${row.keywords_injected.length} 个注入提示词，其余 ${row.keywords_total - row.keywords_injected.length} 个未进入`"
                      >
                        <el-tag size="small" type="warning" class="kw">
                          +{{ row.keywords_total - row.keywords_injected.length }}
                        </el-tag>
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
                      <el-tag size="small" :type="clueTag(row.clue_level)">{{ clueLabel(row.clue_level) }}</el-tag>
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>

              <el-tab-pane label="试运行" name="test">
                <div class="test-bar">
                  <el-input
                    v-model="testMessage"
                    placeholder="输入客户原话，例如：我的订单三天了还没到"
                    clearable
                    @keyup.enter="runTest"
                  />
                  <el-button type="primary" :loading="testLoading" @click="runTest">试运行</el-button>
                </div>
                <div class="muted hint">
                  直连场景不调用模型；llm 模式会消耗一次路由模型调用。试运行不写会话、不写链路。
                </div>

                <template v-if="testResult">
                  <el-descriptions :column="2" border size="small" class="result">
                    <el-descriptions-item label="生效方式">
                      <el-tag size="small" :type="modeTag(testResult.mode)">{{ modeLabel(testResult.mode) }}</el-tag>
                      <span v-if="testResult.router_skipped" class="muted">（跳过路由模型）</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="耗时">{{ testResult.elapsed_ms }} ms</el-descriptions-item>
                    <el-descriptions-item label="主意图">
                      <span class="value">{{ testResult.agent_name || '—' }}</span>
                      <span class="muted">置信度 {{ testResult.confidence ?? '—' }}</span>
                    </el-descriptions-item>
                    <el-descriptions-item label="兜底 Agent">{{ testResult.fallback_agent || '—' }}</el-descriptions-item>
                    <el-descriptions-item label="提示词指纹" :span="2">
                      <code>{{ testResult.prompt_hash }}</code>
                    </el-descriptions-item>
                  </el-descriptions>
                  <el-table :data="testResult.intents" size="small" border class="result">
                    <el-table-column prop="agent_name" label="Agent" width="160" />
                    <el-table-column prop="confidence" label="置信度" width="96" />
                    <el-table-column label="依据" min-width="220">
                      <template #default="{ row }">
                        <div>{{ row.reason || '—' }}</div>
                        <div v-if="row.matched_keywords?.length" class="muted">
                          命中关键词：{{ row.matched_keywords.join('、') }}
                        </div>
                        <div v-else-if="row.clue_basis" class="muted">
                          关键词依据：{{ clueBasisLabel(row.clue_basis) }}
                        </div>
                      </template>
                    </el-table-column>
                    <el-table-column label="兜底" width="80">
                      <template #default="{ row }">
                        <el-tag v-if="row.is_fallback" size="small" type="warning">兜底</el-tag>
                        <span v-else class="muted">—</span>
                      </template>
                    </el-table-column>
                  </el-table>
                </template>
              </el-tab-pane>

              <el-tab-pane label="24h 统计" name="stats">
                <div class="muted hint">
                  只统计完整走完并写入了链路的请求；直连场景的“直连率”应为 100%。
                </div>
                <div class="stats">
                  <div class="stat">
                    <div class="stat-value">{{ stats?.requests ?? 0 }}</div>
                    <div class="muted">请求数</div>
                  </div>
                  <div class="stat">
                    <div class="stat-value">{{ pct(stats?.skip_rate) }}</div>
                    <div class="muted">直连率</div>
                  </div>
                  <div class="stat">
                    <div class="stat-value">{{ pct(stats?.fallback_rate) }}</div>
                    <div class="muted">兜底率</div>
                  </div>
                  <div class="stat">
                    <div class="stat-value">{{ pct(stats?.handover_rate) }}</div>
                    <div class="muted">转人工率</div>
                  </div>
                  <div class="stat">
                    <div class="stat-value">{{ stats?.avg_router_ms ?? '—' }}</div>
                    <div class="muted">平均路由耗时(ms)</div>
                  </div>
                  <div class="stat">
                    <div class="stat-value">{{ stats?.avg_total_latency_ms ?? '—' }}</div>
                    <div class="muted">平均总耗时(ms)</div>
                  </div>
                </div>
                <div class="muted hint">统计窗口：近 {{ stats?.hours ?? 24 }} 小时</div>
              </el-tab-pane>

              <el-tab-pane label="全局模板" name="template">
                <div class="muted hint">
                  模板由代码维护（{{ template?.definition_files.prompt || '—' }}），占位符每轮填入实时数据；
                  规则段不硬编码任何具体 Agent 名称，新增业务 Agent 只需配好线索即可被路由到。
                </div>
                <el-table v-if="template" :data="template.placeholders" size="small" border class="ph-table">
                  <el-table-column prop="name" label="占位符" width="160" />
                  <el-table-column prop="desc" label="含义" />
                </el-table>
                <pre class="text-block prompt-pre">{{ template?.system_prompt_template || '加载中…' }}</pre>
                <div class="muted hint">全局默认（backend/.env）</div>
                <div v-if="template" class="env-list">
                  <el-tag v-for="(key, name) in template.env_keys" :key="name" size="small" type="info" class="kw">
                    {{ name }} → {{ key }}
                  </el-tag>
                </div>
              </el-tab-pane>
            </el-tabs>
          </el-card>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  apiGetRouterScene,
  apiGetRouterScenePrompt,
  apiGetRouterSceneStats,
  apiGetRouterTemplate,
  apiListRouterScenes,
  apiTestRouterScene,
} from '@/api'
import type {
  RouterAgentInfo,
  RouterClueLevel,
  RouterEffectiveMode,
  RouterFallbackSource,
  RouterSceneDetail,
  RouterScenePrompt,
  RouterSceneStats,
  RouterSceneSummary,
  RouterSceneTestResult,
  RouterWarningCode,
} from '@/types'
import PageHeader from '@/components/PageHeader.vue'

const scenes = ref<RouterSceneSummary[]>([])
const selectedCode = ref('')
const detail = ref<RouterSceneDetail | null>(null)
const promptInfo = ref<RouterScenePrompt | null>(null)
const stats = ref<RouterSceneStats | null>(null)
const template = ref<RouterAgentInfo | null>(null)
const listLoading = ref(false)
const detailLoading = ref(false)
const tab = ref('prompt')
const testMessage = ref('')
const testLoading = ref(false)
const testResult = ref<RouterSceneTestResult | null>(null)

// 场景切换的请求令牌：快速点击时丢弃旧响应，避免详情串台
let detailToken = 0

const MODE_LABELS: Record<RouterEffectiveMode, string> = {
  direct: '直连',
  llm: 'LLM 路由',
  unconfigured: '未配置',
}
const MODE_TAGS: Record<RouterEffectiveMode, 'success' | 'warning' | 'danger'> = {
  direct: 'success',
  llm: 'warning',
  unconfigured: 'danger',
}
const WARNING_LABELS: Record<RouterWarningCode, string> = {
  no_agents: '未配置可用 Agent，本场景暂不可对话',
  single_agent_direct: '只有 1 个 Agent：直连，不走向路由模型',
  fallback_not_in_pool: '配置的兜底 Agent 不在候选池，已自动降级',
  agent_description_only: '部分 Agent 只有描述、没有关键词/典型问法，路由命中率会偏低',
  no_knowledge_base: '暂无可检索知识库',
  no_identity_script: '未配置本场景身份话术，当前继承全局文案',
  scene_disabled: '场景已停用，当前不接收对话',
}
const FALLBACK_LABELS: Record<RouterFallbackSource, string> = {
  scene: '场景配置',
  configured_missing: '配置失效·已降级',
  preferred: '池内优先候选',
  pool_first: '池内首项',
  empty: '池为空',
}
const CLUE_LABELS: Record<RouterClueLevel, string> = {
  full: '完整',
  keywords_only: '仅关键词',
  examples_only: '仅问法',
  description_only: '仅描述',
}
const CLUE_TAGS: Record<RouterClueLevel, 'success' | 'warning' | 'info'> = {
  full: 'success',
  keywords_only: 'warning',
  examples_only: 'warning',
  description_only: 'info',
}

function modeLabel(mode: string): string {
  return MODE_LABELS[mode as RouterEffectiveMode] || mode
}
function modeTag(mode: string): 'success' | 'warning' | 'danger' {
  return MODE_TAGS[mode as RouterEffectiveMode] || 'info'
}
function warningLabel(code: string): string {
  return WARNING_LABELS[code as RouterWarningCode] || code
}
function fallbackLabel(source: string): string {
  return FALLBACK_LABELS[source as RouterFallbackSource] || source
}
function clueLabel(level: string): string {
  return CLUE_LABELS[level as RouterClueLevel] || level
}
function clueTag(level: string): 'success' | 'warning' | 'info' {
  return CLUE_TAGS[level as RouterClueLevel] || 'info'
}
function sourceTag(source: string): 'success' | 'info' {
  return source === 'scene' ? 'success' : 'info'
}
function clueBasisLabel(basis: string): string {
  const labels: Record<string, string> = {
    keyword_hit: '命中配置关键词',
    keyword_miss: '已配关键词但原句未命中，按语义判定',
    no_keywords: '未配置关键词，仅依描述判定',
  }
  return labels[basis] || basis
}
function pct(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(1)}%`
}

async function loadScenes(keepSelection = false) {
  listLoading.value = true
  try {
    const list = await apiListRouterScenes()
    scenes.value = list
    const current = scenes.value.find((s) => s.scene_code === selectedCode.value)
    if (!keepSelection || !current) {
      const preferred = scenes.value.find((s) => s.is_default) || scenes.value[0]
      if (preferred) await selectScene(preferred.scene_code)
    }
  } catch (e: any) {
    ElMessage.error(e?.message || '加载场景路由失败')
  } finally {
    listLoading.value = false
  }
}

async function selectScene(code: string) {
  selectedCode.value = code
  const token = ++detailToken
  detailLoading.value = true
  testResult.value = null
  try {
    const [d, p, s] = await Promise.all([
      apiGetRouterScene(code),
      apiGetRouterScenePrompt(code),
      apiGetRouterSceneStats(code),
    ])
    if (token !== detailToken) return
    detail.value = d
    promptInfo.value = p
    stats.value = s
  } catch (e: any) {
    if (token === detailToken) {
      detail.value = null
      promptInfo.value = null
      stats.value = null
      ElMessage.error(e?.message || '加载路由档案失败')
    }
  } finally {
    if (token === detailToken) detailLoading.value = false
  }
}

async function runTest() {
  const message = testMessage.value.trim()
  if (!message) {
    ElMessage.warning('请输入一句客户原话')
    return
  }
  testLoading.value = true
  try {
    testResult.value = await apiTestRouterScene(selectedCode.value, message)
  } catch (e: any) {
    ElMessage.error(e?.message || '试运行失败')
  } finally {
    testLoading.value = false
  }
}

async function copyPrompt() {
  const text = promptInfo.value?.prompt || ''
  if (!text) return
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制本场景提示词')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中')
  }
}

onMounted(async () => {
  try {
    template.value = await apiGetRouterTemplate()
  } catch {
    // 模板加载失败不阻塞场景列表
  }
  await loadScenes()
})
</script>

<style scoped>
.layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}

/* 左侧场景列表：粘性定位，详情滚动时保持可见 */
.scene-list {
  width: 280px;
  flex: 0 0 280px;
  position: sticky;
  top: 12px;
}

.scene-item {
  padding: 10px 12px;
  margin-bottom: 8px;
  border: 1px solid transparent;
  border-radius: var(--cs-radius-sm);
  cursor: pointer;
}

.scene-item:hover {
  background: var(--cs-brand-soft);
}

.scene-item.active {
  background: var(--cs-brand-soft);
  border-color: var(--cs-brand-line);
  box-shadow: inset 3px 0 0 var(--cs-brand);
}

.scene-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
}

.scene-name {
  font-weight: 600;
  color: var(--cs-text-1);
}

.scene-meta {
  margin-bottom: 6px;
  font-size: 12px;
  color: var(--cs-text-3);
}

.scene-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.detail {
  flex: 1;
  min-width: 0;
}

.block {
  margin-bottom: 16px;
}

.card-head.wrap {
  flex-wrap: wrap;
  gap: 8px;
}

.title-line {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.code {
  font-size: 12px;
}

.hash {
  font-size: 12px;
}

.warn {
  margin-bottom: 12px;
}

.warn-line {
  font-size: 12px;
  line-height: 1.7;
}

.src {
  margin-left: 6px;
}

.value {
  font-weight: 600;
}

.hint {
  font-size: 12px;
  line-height: 1.6;
  margin: 4px 0;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.clamp {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
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

/* 长文本块（提示词 / 模板）：可滚动，与全局 .text-block 叠加使用 */
.prompt-pre {
  margin-top: 8px;
  max-height: 420px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.6;
}

.prompt-pre.small {
  max-height: 180px;
}

.test-bar {
  display: flex;
  gap: 8px;
  max-width: 640px;
}

.result {
  margin-top: 12px;
}

.stats {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 12px 0;
}

.stat {
  min-width: 130px;
  padding: 12px 16px;
  border: 1px solid var(--cs-border-light);
  border-radius: var(--cs-radius-sm);
  background: var(--cs-surface-sub);
  text-align: center;
}

.stat-value {
  font-size: 20px;
  font-weight: 700;
  line-height: 1.4;
  color: var(--cs-text-1);
}

.env-list {
  margin-top: 8px;
}

@media (max-width: 900px) {
  .layout {
    flex-direction: column;
  }
  .scene-list {
    width: 100%;
    flex: none;
    position: static;
  }
}
</style>
