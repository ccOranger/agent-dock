<template>
  <div>
    <el-card>
      <template #header>
        <div class="head">
          <b>用量报表</b>
          <span class="tip">口径：走完整对话链路的一轮 = 1 次请求；Token 由提示词与回答估算，非供应商账单</span>
        </div>
      </template>

      <div class="filters">
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
        <el-radio-group v-model="groupBy" @change="load">
          <el-radio-button value="day">按天</el-radio-button>
          <el-radio-button value="scene">按场景</el-radio-button>
          <el-radio-button value="integration">按接入方</el-radio-button>
        </el-radio-group>
        <el-select v-model="sceneCode" placeholder="全部场景" clearable style="width: 170px">
          <el-option v-for="s in scenes" :key="s.code" :label="s.display_name" :value="s.code" />
        </el-select>
        <el-button type="primary" @click="load">查询</el-button>
        <el-button @click="reset">近 30 天</el-button>
        <el-button :loading="exporting" @click="onExport">导出 CSV</el-button>
      </div>

      <el-alert v-if="summary?.tokens_estimated" type="info" show-icon :closable="false" class="tip-row">
        Token 为估算值（{{ fmtNum(summary.totals.estimated_requests) }} / {{ fmtNum(summary.totals.requests) }} 次请求），
        仅用于容量与授权核算参考，对账请以供应商账单为准。
      </el-alert>

      <el-row :gutter="16" class="cards">
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="请求数" :value="summary?.totals.requests ?? 0" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="Token（估算）" :value="summary?.totals.tokens ?? 0" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="转人工" :value="summary?.totals.handoffs ?? 0" />
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <el-statistic title="异常" :value="summary?.totals.errors ?? 0" />
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="groupBy === 'day'" shadow="never" class="chart" v-loading="loading">
        <template #header>
          <div class="head">
            <b>Token 趋势</b>
            <span class="tip">柱长按区间内最高值归一化{{ barsTruncated ? '，仅显示最近 31 天' : '' }}</span>
          </div>
        </template>
        <p v-if="!bars.length" class="muted">该区间暂无用量记录</p>
        <div v-else class="bar-list">
          <div v-for="bar in bars" :key="bar.label" class="bar-row">
            <span class="bar-label">{{ bar.label }}</span>
            <div class="bar-track"><div class="bar-fill" :style="{ width: bar.pct + '%' }" /></div>
            <span class="bar-count">{{ fmtNum(bar.tokens) }}</span>
          </div>
        </div>
      </el-card>

      <el-table :data="tableRows" v-loading="loading" style="margin-top: 12px">
        <el-table-column prop="label" :label="dimensionLabel" min-width="180" />
        <el-table-column prop="requests" label="请求数" width="110" sortable>
          <template #default="{ row }">{{ fmtNum(row.requests) }}</template>
        </el-table-column>
        <el-table-column prop="tokens" label="Token（估算）" width="150" sortable>
          <template #default="{ row }">{{ fmtNum(row.tokens) }}</template>
        </el-table-column>
        <el-table-column label="平均 Token/请求" width="150">
          <template #default="{ row }">{{ avgTokens(row) }}</template>
        </el-table-column>
        <el-table-column prop="handoffs" label="转人工" width="100" sortable />
        <el-table-column prop="errors" label="异常" width="100" sortable />
        <el-table-column label="估算占比" width="120">
          <template #default="{ row }">{{ ratio(row.estimated_requests, row.requests) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import dayjs from 'dayjs'
import { apiExportUsage, apiSceneOptions, apiUsageSummary } from '@/api'
import type { SceneOption, UsageGroupBy, UsageRow, UsageSummary } from '@/types'

const loading = ref(false)
const exporting = ref(false)
const summary = ref<UsageSummary | null>(null)
const scenes = ref<SceneOption[]>([])
const groupBy = ref<UsageGroupBy>('day')
const sceneCode = ref<string>('')

const defaultRange = (): [string, string] => [
  dayjs().subtract(29, 'day').format('YYYY-MM-DD'),
  dayjs().format('YYYY-MM-DD'),
]
const dateRange = ref<[string, string]>(defaultRange())

const dimensionLabel = computed(() =>
  groupBy.value === 'day' ? '日期' : groupBy.value === 'scene' ? '场景' : '接入方',
)

function fmtNum(v?: number | null) {
  return (v ?? 0).toLocaleString('zh-CN')
}

function ratio(part?: number, total?: number) {
  if (!total) return '-'
  return `${Math.round(((part ?? 0) / total) * 100)}%`
}

function avgTokens(row: { tokens: number; requests: number }) {
  if (!row.requests) return '-'
  return fmtNum(Math.round(row.tokens / row.requests))
}

// 维度键 → 展示文案：日期原样，场景用 code，接入方优先名称、零 UUID 显示未归属
function rowLabel(r: UsageRow): string {
  if ('day' in r) return r.day
  if ('scene_code' in r) return r.scene_code
  return r.unassigned ? '未归属（匿名/未带凭证）' : r.integration_name || r.integration_id
}

const tableRows = computed(() =>
  (summary.value?.rows ?? []).map((r) => ({ ...r, label: rowLabel(r) })),
)

const bars = computed(() => {
  const rows = (summary.value?.rows ?? []).filter((r) => 'day' in r)
  const recent = rows.slice(-31)
  const max = Math.max(...recent.map((r) => r.tokens), 1)
  return recent.map((r) => ({
    label: rowLabel(r),
    tokens: r.tokens,
    pct: Math.round((r.tokens / max) * 100),
  }))
})
const barsTruncated = computed(() => (summary.value?.rows.length ?? 0) > 31)

function buildParams() {
  const [from, to] = dateRange.value
  return {
    from,
    to,
    group_by: groupBy.value,
    ...(sceneCode.value ? { scene_code: sceneCode.value } : {}),
  }
}

async function load() {
  loading.value = true
  try {
    summary.value = await apiUsageSummary(buildParams())
  } catch (e: any) {
    ElMessage.error(e?.message || '用量加载失败')
  } finally {
    loading.value = false
  }
}

function reset() {
  dateRange.value = defaultRange()
  sceneCode.value = ''
  groupBy.value = 'day'
  load()
}

async function onExport() {
  exporting.value = true
  try {
    const [from, to] = dateRange.value
    const blob = await apiExportUsage({ from, to, ...(sceneCode.value ? { scene_code: sceneCode.value } : {}) })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `usage_${from}_${to}.csv`
    a.click()
    URL.revokeObjectURL(url)
    ElMessage.success('已导出')
  } catch (e: any) {
    ElMessage.error(e?.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  // 场景列表失败不应挡住用量查询（后端不依赖它）
  const [loadResult, scenesResult] = await Promise.allSettled([load(), apiSceneOptions()])
  if (scenesResult.status === 'fulfilled') scenes.value = scenesResult.value
  void loadResult
})
</script>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tip {
  font-size: 12px;
  color: #909399;
}
.tip-row {
  margin-top: 12px;
}
.filters {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: center;
}
.cards {
  margin-top: 16px;
}
.chart {
  margin-top: 16px;
}
.muted {
  color: #909399;
  font-size: 13px;
}
.bar-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.bar-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.bar-label {
  width: 110px;
  font-size: 13px;
  color: #606266;
}
.bar-track {
  flex: 1;
  height: 12px;
  background: #f0f2f5;
  border-radius: 6px;
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  background: #409eff;
  border-radius: 6px;
}
.bar-count {
  width: 90px;
  text-align: right;
  font-size: 13px;
  color: #606266;
}
</style>
