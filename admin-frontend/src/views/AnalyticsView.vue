<template>
  <div class="page">
    <PageHeader title="数据分析 · 成效指标">
      <template #actions>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </template>
    </PageHeader>

    <el-card>
      <FilterBar :loading="loading" @search="load" @reset="reset">
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          value-format="YYYY-MM-DD"
          :clearable="false"
          style="width: 260px"
        />
        <el-select v-model="sceneCode" placeholder="全部场景" clearable style="width: 170px">
          <el-option v-for="s in scenes" :key="s.code" :label="s.display_name" :value="s.code" />
        </el-select>
        <template #actions>
          <el-button type="primary" :icon="Search" :loading="loading" @click="load">查询</el-button>
          <el-button :icon="Refresh" @click="reset">近 7 天</el-button>
        </template>
      </FilterBar>

      <!-- 采样提示：链路三率可能低估，不能与全量三率混着解读 -->
      <el-alert
        v-if="outcomes?.meta.sampled"
        type="warning"
        show-icon
        :closable="false"
        class="tip-row"
      >
        链路三率基于采样链路（TRACE_SAMPLE_RATE={{ outcomes.meta.trace_sample_rate }}，区间内
        {{ fmtNum(outcomes.meta.traces_covered) }} 条）：采样率 &lt; 1 时工具成功率会被低估，
        兜底率与 RAG 命中率仅作比值参考；全量三率不受影响。
      </el-alert>

      <div class="group-title">
        <span class="group-label"><el-icon><DataLine /></el-icon>全量三率</span>
        <span class="muted">区间 {{ fmtNum(meta?.requests) }} 次请求</span>
      </div>
      <el-row :gutter="16">
        <el-col v-for="m in FULL_METRICS" :key="m.key" :span="8">
          <el-card shadow="never" v-loading="loading">
            <div class="metric-head">
              <span class="metric-label">
                <span class="metric-icon" :class="`is-${m.tone}`">
                  <el-icon><component :is="m.icon" /></el-icon>
                </span>
                <span class="metric-title">{{ m.title }}</span>
              </span>
              <el-tag size="small" type="success">全量</el-tag>
            </div>
            <el-statistic v-if="rate(m.key).value !== null" :value="rate(m.key).value! * 100" :precision="1">
              <template #suffix><span class="suffix">%</span></template>
            </el-statistic>
            <div v-else class="metric-empty">—</div>
            <div class="muted hint">{{ m.hint }} · {{ frac(m.key) }}</div>
          </el-card>
        </el-col>
      </el-row>

      <div class="group-title">
        <span class="group-label"><el-icon><Connection /></el-icon>链路三率</span>
        <span class="muted">采样链路 · 区间 {{ fmtNum(meta?.traces_covered) }} 条</span>
        <el-tooltip
          :content="
            meta?.sampled
              ? '基于采样链路，采样率 < 1 时工具成功率会被低估，比率仅作参考'
              : '当前采样率为 1，链路三率即全量口径'
          "
        >
          <el-tag size="small" :type="meta?.sampled ? 'warning' : 'info'" class="group-tag">
            {{ meta?.sampled ? '采样口径' : '全量链路' }}
          </el-tag>
        </el-tooltip>
      </div>
      <el-row :gutter="16">
        <el-col v-for="m in TRACE_METRICS" :key="m.key" :span="8">
          <el-card shadow="never" v-loading="loading">
            <div class="metric-head">
              <span class="metric-label">
                <span class="metric-icon" :class="`is-${m.tone}`">
                  <el-icon><component :is="m.icon" /></el-icon>
                </span>
                <span class="metric-title">{{ m.title }}</span>
              </span>
              <el-tag size="small" :type="meta?.sampled ? 'warning' : 'info'">
                {{ meta?.sampled ? '采样' : '链路' }}
              </el-tag>
            </div>
            <el-statistic v-if="rate(m.key).value !== null" :value="rate(m.key).value! * 100" :precision="1">
              <template #suffix><span class="suffix">%</span></template>
            </el-statistic>
            <div v-else class="metric-empty">—</div>
            <div class="muted hint">{{ m.hint }} · {{ frac(m.key) }}</div>
          </el-card>
        </el-col>
      </el-row>

      <div class="group-title">
        <span class="group-label"><el-icon><TrendCharts /></el-icon>每日请求量</span>
        <span class="muted">按天升序 · 共 {{ fmtNum(dailyRows.length) }} 天</span>
      </div>
      <el-card shadow="never" v-loading="loading">
        <p v-if="!dailyRows.length" class="muted empty-day">
          <el-icon><Calendar /></el-icon>该区间暂无请求记录
        </p>
        <template v-else>
          <div class="chart-meta muted">
            <span>区间合计 {{ fmtNum(meta?.requests) }} 次</span>
            <span v-if="peak">单日峰值 {{ fmtNum(peak.requests) }} 次（{{ peak.day }}）</span>
            <span>无请求的日期按 0 计入折线（日聚合无行 = 当天 0 次）</span>
          </div>
          <LineTrendChart :rows="chartRows" />
        </template>
      </el-card>

      <p class="muted footnote">
        问题解决率 = (请求数 − 转人工 − 错误) / 请求数，口径上是「机器人自主应答率」，不代表用户满意度；
        token 是估算值（框架不回传供应商 usage），本页不展示，避免被当成账单；
        幻觉率需 ground-truth，后续走离线评测，本期不展示。趋势与分布、运营时效将在本页后续扩展。
      </p>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { Component } from 'vue'
import { ElMessage } from 'element-plus'
import {
  Calendar,
  CircleCheck,
  CircleClose,
  Connection,
  DataLine,
  Refresh,
  RefreshLeft,
  Search,
  Service,
  Tools,
  TrendCharts,
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'
import { apiAnalyticsOutcomes, apiSceneOptions } from '@/api'
import LineTrendChart from '@/components/LineTrendChart.vue'
import PageHeader from '@/components/PageHeader.vue'
import FilterBar from '@/components/FilterBar.vue'
import type { AnalyticsDailyRow, AnalyticsOutcomes, AnalyticsRate, SceneOption } from '@/types'

type RateKey =
  | 'handover_rate'
  | 'resolution_rate'
  | 'error_rate'
  | 'tool_success_rate'
  | 'fallback_rate'
  | 'rag_hit_rate'

interface MetricMeta {
  key: RateKey
  title: string
  hint: string
  icon: Component
  /** 图标底色：成功给绿色、风险红/橙，中性指标用主色 */
  tone: 'primary' | 'success' | 'warning' | 'danger'
}

// 全量：日聚合，无采样偏差，可用于对账
const FULL_METRICS: MetricMeta[] = [
  { key: 'handover_rate', title: '转人工率', hint: '转人工轮次 / 全部请求', icon: Service, tone: 'warning' },
  { key: 'resolution_rate', title: '问题解决率', hint: '≈机器人自主应答率，非满意度', icon: CircleCheck, tone: 'success' },
  { key: 'error_rate', title: '错误率', hint: '异常轮次 / 全部请求', icon: CircleClose, tone: 'danger' },
]

// 链路：request_traces 聚合，受 TRACE_SAMPLE_RATE 采样影响
const TRACE_METRICS: MetricMeta[] = [
  { key: 'tool_success_rate', title: '工具调用成功率', hint: '成功调用 / 工具调用总数', icon: Tools, tone: 'success' },
  { key: 'fallback_rate', title: '兜底降级率', hint: '未贴合任何 Agent / 走完链路', icon: RefreshLeft, tone: 'warning' },
  { key: 'rag_hit_rate', title: 'RAG 命中率', hint: '检索到片段 / 走完链路', icon: Search, tone: 'primary' },
]

const EMPTY_RATE: AnalyticsRate = { value: null, numerator: 0, denominator: 0 }

const loading = ref(false)
const outcomes = ref<AnalyticsOutcomes | null>(null)
const scenes = ref<SceneOption[]>([])
const sceneCode = ref('')

const defaultRange = (): [string, string] => [
  dayjs().subtract(6, 'day').format('YYYY-MM-DD'),
  dayjs().format('YYYY-MM-DD'),
]
const dateRange = ref<[string, string]>(defaultRange())

const meta = computed(() => outcomes.value?.meta)
const dailyRows = computed(() => outcomes.value?.daily ?? [])

/**
 * 折线按完整区间铺开：日聚合表没有行 = 当天 0 次请求，补 0 才是真实时间轴
 * （缺日直接连线会把 3 天的量画成 1 天）。
 */
const chartRows = computed<AnalyticsDailyRow[]>(() => {
  const o = outcomes.value
  if (!o) return []
  const byDay = new Map(dailyRows.value.map((r) => [r.day, r]))
  const start = dayjs(o.from)
  const total = Math.max(dayjs(o.to).diff(start, 'day') + 1, 1)
  return Array.from({ length: total }, (_, i) => {
    const day = start.add(i, 'day').format('YYYY-MM-DD')
    return byDay.get(day) ?? { day, requests: 0, handoffs: 0, errors: 0 }
  })
})

/** 峰值取真实记录（补 0 不会成为峰值，除非整段都是 0） */
const peak = computed(() =>
  dailyRows.value.reduce<AnalyticsDailyRow | null>(
    (best, r) => (best === null || r.requests > best.requests ? r : best),
    null,
  ),
)

function rate(key: RateKey): AnalyticsRate {
  return outcomes.value?.[key] ?? EMPTY_RATE
}

function fmtNum(v?: number | null) {
  return (v ?? 0).toLocaleString('zh-CN')
}

/** 副文案「分子/分母」：分母为 0 时明确说没有样本，避免被读成 0% */
function frac(key: RateKey) {
  const r = rate(key)
  return r.denominator ? `${fmtNum(r.numerator)} / ${fmtNum(r.denominator)}` : '暂无样本'
}

async function load() {
  loading.value = true
  try {
    const [from, to] = dateRange.value
    outcomes.value = await apiAnalyticsOutcomes({
      from,
      to,
      ...(sceneCode.value ? { scene_code: sceneCode.value } : {}),
    })
  } catch (e: any) {
    ElMessage.error(e?.message || '成效指标加载失败')
  } finally {
    loading.value = false
  }
}

function reset() {
  dateRange.value = defaultRange()
  sceneCode.value = ''
  load()
}

onMounted(async () => {
  // 场景列表失败不应挡住指标查询（后端不依赖它）
  const [loadResult, scenesResult] = await Promise.allSettled([load(), apiSceneOptions()])
  if (scenesResult.status === 'fulfilled') scenes.value = scenesResult.value
  void loadResult
})
</script>

<style scoped>
.tip-row {
  margin-top: 12px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 20px 0 10px;
  font-size: 14px;
  font-weight: 600;
  color: var(--cs-text-1);
}

.group-tag {
  cursor: help;
}

.group-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.group-label .el-icon {
  color: var(--cs-text-3);
  font-size: 15px;
}

.metric-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.metric-label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

/* 图标底色：不靠颜色单独传递语义，标题文字仍在，色弱用户也能读 */
.metric-icon {
  display: inline-flex;
  width: 26px;
  height: 26px;
  align-items: center;
  justify-content: center;
  border-radius: var(--cs-radius-sm);
  font-size: 15px;
}

.metric-icon.is-success {
  background: var(--cs-success-soft);
  color: var(--cs-success);
}

.metric-icon.is-warning {
  background: var(--cs-warning-soft);
  color: var(--cs-warning-text);
}

.metric-icon.is-danger {
  background: var(--cs-danger-soft);
  color: var(--cs-danger);
}

.metric-icon.is-primary {
  background: var(--cs-brand-soft);
  color: var(--cs-brand);
}

.metric-title {
  font-size: 13px;
  color: var(--cs-text-2);
}

.metric-empty {
  font-size: 24px;
  line-height: 1.5;
  color: var(--cs-text-3);
}

.suffix {
  font-size: 14px;
  color: var(--cs-text-2);
}

.hint {
  margin-top: 6px;
}

.empty-day {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 0;
}

.chart-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
  margin-bottom: 14px;
}

.footnote {
  margin-top: 20px;
  line-height: 1.6;
}
</style>
