<template>
  <div class="trace-detail" v-loading="loading">
    <!-- 概要：一眼看清这轮走了哪些依赖、卡在哪 -->
    <el-descriptions v-if="hasDetail" :column="2" border size="small">
      <el-descriptions-item label="结局">
        <el-tag :type="outcomeTag(d.outcome).type" size="small">{{ outcomeTag(d.outcome).text }}</el-tag>
        <span v-if="d.handover_reason" class="hint">（{{ d.handover_reason }}）</span>
        <span v-if="d.error_code" class="err">code={{ d.error_code }}</span>
      </el-descriptions-item>
      <el-descriptions-item label="总耗时">{{ fmtMs(d.total_latency_ms) }}</el-descriptions-item>
      <el-descriptions-item label="首字延迟">{{ fmtMs(d.ttft_ms) }}</el-descriptions-item>
      <el-descriptions-item label="意图 / 置信">
        {{ d.intent_count }} 个 / {{ fmtConfidence(d.confidence) }}
      </el-descriptions-item>
      <el-descriptions-item label="业务 Agent">
        <el-tag v-for="n in d.agent_names" :key="n" size="small" class="chip">{{ n }}</el-tag>
        <span v-if="!d.agent_names.length" class="hint">未进入业务 Agent</span>
      </el-descriptions-item>
      <el-descriptions-item label="MCP 服务">
        <el-tag v-for="n in d.mcp_server_names" :key="n" size="small" type="warning" class="chip">{{ n }}</el-tag>
        <span v-if="!d.mcp_server_names.length" class="hint">无</span>
      </el-descriptions-item>
      <el-descriptions-item label="工具调用">
        <el-tag v-for="n in d.tool_names" :key="n" size="small" type="success" class="chip">{{ n }}</el-tag>
        <span v-if="d.tool_error_count" class="err">失败 {{ d.tool_error_count }} 次</span>
        <span v-if="!d.tool_names.length" class="hint">未调用工具</span>
      </el-descriptions-item>
      <el-descriptions-item label="模型 / RAG / 记忆">
        <span class="hint">模型</span>
        <el-tag v-for="n in d.llm_models" :key="n" size="small" class="chip">{{ n }}</el-tag>
        <span class="hint">RAG {{ d.rag_chunk_count }}（{{ d.rag_namespaces.join(',') || '-' }}）</span>
        <span class="hint">记忆 {{ d.memory_hit_count }}</span>
      </el-descriptions-item>
      <el-descriptions-item v-if="d.error_message" label="异常" :span="2">
        <span class="err">{{ d.error_message }}</span>
      </el-descriptions-item>
    </el-descriptions>

    <!-- 时间线：start_ms 相对请求起点，条宽按占全程比例画 -->
    <div class="tl-head">
      <b>执行时间线</b>
      <span class="hint">共 {{ sortedSpans.length }} 步 · 全程 {{ fmtMs(d.total_latency_ms) }}</span>
      <el-input v-model="keyword" placeholder="过滤步骤 / 工具名" clearable size="small" style="width: 200px" />
    </div>

    <div class="timeline">
      <div v-for="s in visibleSpans" :key="s.span_id" class="span" :class="statusClass(s.status)">
        <div class="row">
          <span class="type" :style="{ background: typeColor(s.type) }">{{ typeLabel(s.type) }}</span>
          <span class="name">{{ s.name || '-' }}</span>
          <span v-if="attrText(s, 'agent_source')" class="hint">{{ attrText(s, 'agent_source') }}</span>
          <span v-if="attrText(s, 'hit_count')" class="hint">命中 {{ attrText(s, 'hit_count') }}</span>
          <span v-if="attrText(s, 'chunk_count')" class="hint">片段 {{ attrText(s, 'chunk_count') }}</span>
          <span v-if="attrText(s, 'error')" class="err">{{ attrText(s, 'error') }}</span>
          <span class="spacer" />
          <span class="time">+{{ s.start_ms }}ms</span>
          <span class="dur">{{ fmtMs(s.duration_ms) }}</span>
        </div>
        <div class="bar-track">
          <div class="bar" :style="barStyle(s)" />
        </div>
        <!-- 路由意图：把“为什么判给这个 Agent”单独成块，不混在属性列表里 -->
        <div v-if="isIntentSpan(s)" class="evidence">
          <div class="ev-row">
            <el-tag size="small" :type="intentIndex(s) === 0 ? '' : 'info'">
              {{ intentIndex(s) === 0 ? '主意图' : `次要意图 #${intentIndex(s)}` }}
            </el-tag>
            <span class="ev-item">置信 <b>{{ fmtConfidence(numAttr(s, 'confidence')) }}</b></span>
            <el-tag v-if="attrText(s, 'is_fallback')" size="small" type="warning">兜底意图</el-tag>
            <template v-if="clueBasis(s)">
              <el-tag size="small" :type="clueBasisTag(clueBasis(s))">{{ clueBasisText(clueBasis(s)) }}</el-tag>
              <el-tag v-for="kw in matchedKeywords(s)" :key="kw" size="small" type="success" class="chip">
                {{ kw }}
              </el-tag>
            </template>
          </div>
          <div v-if="intentReason(s)" class="ev-reason">
            <span class="k">模型理由</span>
            <span class="v">{{ intentReason(s) }}</span>
          </div>
          <div v-if="clueBasis(s)" class="ev-note">
            命中词为本地子串匹配（路由实际由 LLM 语义判定，不走关键词匹配/分词），用于看出是哪个词把请求抢到了这个
            Agent，或线索与客户真实说法脱节
          </div>
        </div>
        <div v-if="!isIntentSpan(s) && hasDetailAttrs(s)" class="attrs">
          <div v-for="[k, v] in attrEntries(s)" :key="k" class="attr">
            <span class="k">{{ k }}</span>
            <span class="v">{{ fmtAttrValue(v) }}</span>
            <el-tag v-if="isTruncated(s, k)" size="small" type="warning">已截断</el-tag>
          </div>
        </div>
      </div>
      <el-empty v-if="!visibleSpans.length" :description="hasDetail ? '无步骤' : '加载中…'" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RequestTraceDetail, TraceSpan } from '@/types'

const props = defineProps<{ detail: RequestTraceDetail | null; loading?: boolean }>()

const EMPTY_DETAIL = {
  spans: [],
  agent_names: [],
  agent_sources: [],
  llm_models: [],
  mcp_server_names: [],
  tool_names: [],
  rag_namespaces: [],
  intent_count: 0,
  memory_hit_count: 0,
  rag_chunk_count: 0,
  tool_call_count: 0,
  tool_error_count: 0,
  response_chars: 0,
  span_count: 0,
} as unknown as RequestTraceDetail

// 模板统一走 d：detail 允许为 null（加载中），避免每处都写可选链
const d = computed(() => props.detail || EMPTY_DETAIL)
const hasDetail = computed(() => props.detail !== null)

const keyword = ref('')

const sortedSpans = computed<TraceSpan[]>(() => [...(d.value.spans || [])].sort((a, b) => a.start_ms - b.start_ms))

const visibleSpans = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return sortedSpans.value
  return sortedSpans.value.filter(
    (s) => s.type.toLowerCase().includes(kw) || (s.name || '').toLowerCase().includes(kw),
  )
})

// 条宽基准取「全程耗时」与「最后一个 span 结束时刻」的较大者，避免溢出刻度
const totalMs = computed(() => {
  const last = sortedSpans.value.reduce((acc, s) => Math.max(acc, s.start_ms + s.duration_ms), 0)
  return Math.max(d.value.total_latency_ms || 0, last, 1)
})

function barStyle(s: TraceSpan) {
  return {
    marginLeft: `${(s.start_ms / totalMs.value) * 100}%`,
    width: `${Math.max((s.duration_ms / totalMs.value) * 100, 0.6)}%`,
    background: typeColor(s.type),
  }
}

const TYPE_META: Record<string, { label: string; color: string }> = {
  'router.llm': { label: '路由', color: '#e6a23c' },
  'route.intent': { label: '意图', color: '#f0a020' },
  'context.memory': { label: '记忆检索', color: '#909399' },
  'context.rag': { label: 'RAG 检索', color: '#909399' },
  'context.budget': { label: '上下文预算', color: '#c0c4cc' },
  'agent.resolve': { label: '解析 Agent', color: '#409eff' },
  'agent.build': { label: '构建 Agent', color: '#66b1ff' },
  'agent.run': { label: '执行 Agent', color: '#67c23a' },
  'tool.call': { label: '工具调用', color: '#b88230' },
  'memory.persist': { label: '记忆归档', color: '#a0a4ab' },
}

const HIDDEN_KEYS = new Set(['intent_index'])

// 意图依据三态文案：只看字面命中，所以“配了却没中”与“压根没配”必须区分开
const CLUE_BASIS_TEXT: Record<string, string> = {
  keyword_hit: '命中已配置关键词',
  keyword_miss: '配了关键词但本句未出现（按语义判定）',
  no_keywords: '该 Agent 未配路由关键词（仅按适用范围判定）',
}
const CLUE_BASIS_TAG: Record<string, 'success' | 'warning' | 'info'> = {
  keyword_hit: 'success',
  keyword_miss: 'warning',
  no_keywords: 'info',
}

function typeLabel(t: string) {
  return TYPE_META[t]?.label || t
}
function typeColor(t: string) {
  return TYPE_META[t]?.color || '#909399'
}

function attrEntries(s: TraceSpan) {
  // *_truncated 不单独成行，折叠到对应字段的「已截断」标记上
  return Object.entries(s.attributes || {}).filter(([k]) => !HIDDEN_KEYS.has(k) && !k.endsWith('_truncated'))
}

function hasDetailAttrs(s: TraceSpan) {
  return attrEntries(s).length > 0
}

function attrText(s: TraceSpan, key: string) {
  const v = s.attributes?.[key]
  if (v === undefined || v === null || v === '') return ''
  if (Array.isArray(v)) return v.join(', ')
  return String(v)
}

function isIntentSpan(s: TraceSpan) {
  return s.type === 'route.intent'
}

function intentIndex(s: TraceSpan) {
  return Number(s.attributes?.intent_index ?? 0)
}

function numAttr(s: TraceSpan, key: string): number | null {
  const v = s.attributes?.[key]
  return typeof v === 'number' ? v : null
}

function matchedKeywords(s: TraceSpan): string[] {
  const v = s.attributes?.matched_keywords
  return Array.isArray(v) ? v.map((x) => String(x)) : []
}

function clueBasis(s: TraceSpan) {
  return String(s.attributes?.clue_basis || '')
}

function clueBasisText(basis: string) {
  return CLUE_BASIS_TEXT[basis] || basis
}

function clueBasisTag(basis: string) {
  return CLUE_BASIS_TAG[basis] || 'info'
}

function intentReason(s: TraceSpan) {
  return String(s.attributes?.reason || '')
}

function isTruncated(s: TraceSpan, key: string) {
  return s.attributes?.[`${key}_truncated`] === true
}

function fmtAttrValue(v: unknown) {
  if (v === null || v === undefined) return '-'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

function fmtMs(v?: number | null) {
  if (v === null || v === undefined) return '-'
  return v >= 1000 ? `${(v / 1000).toFixed(2)}s` : `${v}ms`
}

function fmtConfidence(v?: number | null) {
  return v === null || v === undefined ? '-' : v.toFixed(2)
}

function statusClass(status: string) {
  if (status === 'error') return 'is-error'
  if (status === 'skipped') return 'is-skipped'
  if (status === 'empty') return 'is-empty'
  return ''
}

function outcomeTag(outcome?: string | null) {
  if (outcome === 'handover') return { type: 'warning' as const, text: '转人工' }
  if (outcome === 'error') return { type: 'danger' as const, text: '异常' }
  if (outcome === 'answered') return { type: 'success' as const, text: '已应答' }
  return { type: 'info' as const, text: outcome || '未知' }
}
</script>

<style scoped>
.trace-detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.chip {
  margin-right: 4px;
}
.hint {
  color: #909399;
  font-size: 12px;
}
.err {
  color: #f56c6c;
  font-size: 12px;
  margin-left: 6px;
}
.tl-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.tl-head b {
  font-size: 14px;
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.span {
  border: 1px solid #ebeef5;
  border-radius: 6px;
  padding: 6px 10px;
  background: #fff;
}
.span.is-error {
  border-color: #f56c6c;
  background: #fef0f0;
}
.span.is-skipped,
.span.is-empty {
  background: #f7f8fa;
}
.row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.type {
  color: #fff;
  border-radius: 4px;
  padding: 1px 6px;
  font-size: 12px;
  flex: none;
}
.name {
  font-weight: 600;
  color: #303133;
}
.spacer {
  flex: 1;
}
.time,
.dur {
  color: #909399;
  font-size: 12px;
  font-family: Consolas, Menlo, monospace;
}
.dur {
  width: 70px;
  text-align: right;
}
.bar-track {
  height: 4px;
  background: #f0f2f5;
  border-radius: 2px;
  margin-top: 6px;
  overflow: hidden;
}
.bar {
  height: 100%;
  border-radius: 2px;
}
.attrs {
  margin-top: 6px;
  border-top: 1px dashed #ebeef5;
  padding-top: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
/* 意图依据块：背景与时间线行拉开，突出“这一轮为何这么判” */
.evidence {
  margin-top: 6px;
  padding: 6px 8px;
  border-radius: 4px;
  background: #fdf6ec;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.ev-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}
.ev-item {
  font-size: 12px;
  color: #606266;
}
.ev-reason {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
}
.ev-reason .k {
  color: #909399;
  flex: none;
}
.ev-reason .v {
  color: #303133;
  word-break: break-all;
  white-space: pre-wrap;
}
.ev-note {
  font-size: 11px;
  line-height: 1.5;
}
.attr {
  display: flex;
  gap: 8px;
  align-items: baseline;
  font-size: 12px;
}
.attr .k {
  color: #909399;
  flex: none;
  width: 120px;
}
.attr .v {
  color: #303133;
  word-break: break-all;
  white-space: pre-wrap;
  font-family: Consolas, Menlo, monospace;
}
</style>
