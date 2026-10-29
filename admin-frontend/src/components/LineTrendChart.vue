<template>
  <div ref="el" class="line-chart" />
</template>

<script setup lang="ts">
/**
 * 请求量趋势折线图（ECharts 按需引入，不引全量包）。
 *
 * - rows 由调用方按区间补 0 后传入：日聚合表没行 = 当天 0 次请求，缺日直连会失真；
 * - > 62 天启用滚轮/手势缩放（inside），避免 366 个点挤成一条线；
 * - 尺寸变化用 ResizeObserver 跟随卡片宽度，卸载时 dispose，防止路由切走泄漏实例。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'
import type { AnalyticsDailyRow } from '@/types'

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer])

const props = defineProps<{ rows: AnalyticsDailyRow[] }>()

const el = ref<HTMLDivElement>()
let chart: ReturnType<typeof echarts.init> | null = null
let observer: ResizeObserver | null = null

function buildOption(): EChartsCoreOption {
  const rows = props.rows
  return {
    animationDuration: 300,
    grid: { left: 8, right: 18, top: 18, bottom: 6, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line', lineStyle: { color: '#c0c4cc', type: 'dashed' } },
      backgroundColor: 'rgba(48, 49, 51, 0.9)',
      borderWidth: 0,
      padding: [6, 10],
      textStyle: { color: '#fff', fontSize: 12 },
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? params : [params]
        const first = list[0] as { dataIndex?: number } | undefined
        const row = first?.dataIndex === undefined ? undefined : rows[first.dataIndex]
        if (!row) return ''
        return `${row.day}<br/>请求 <b>${row.requests}</b>　转人工 ${row.handoffs}　异常 ${row.errors}`
      },
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: rows.map((r) => r.day.slice(5)),
      axisLine: { lineStyle: { color: '#e4e7ed' } },
      axisTick: { show: false },
      axisLabel: { color: '#909399', fontSize: 12, hideOverlap: true, margin: 10 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: '#909399', fontSize: 12 },
      splitLine: { lineStyle: { color: '#f0f2f5', type: 'dashed' } },
    },
    // 长区间用滚轮缩放，不占额外高度（slider 会压掉图表本身）
    dataZoom: rows.length > 62 ? [{ type: 'inside', throttle: 60 }] : [],
    series: [
      {
        name: '请求数',
        type: 'line',
        data: rows.map((r) => r.requests),
        smooth: 0.15,
        showSymbol: rows.length <= 31,
        symbol: 'circle',
        symbolSize: 6,
        lineStyle: { width: 2, color: '#409eff' },
        itemStyle: { color: '#409eff', borderColor: '#fff', borderWidth: 2 },
        areaStyle: { color: 'rgba(64, 158, 255, 0.14)' },
        emphasis: { focus: 'series' },
      },
    ],
  }
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  chart.setOption(buildOption(), true)
}

onMounted(() => {
  render()
  observer = new ResizeObserver(() => chart?.resize())
  if (el.value) observer.observe(el.value)
})

// rows 每次查询都是新数组，直接重绘（数据量小，merge 反而要处理残留系列）
watch(() => props.rows, render, { deep: true })

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.line-chart {
  width: 100%;
  height: 260px;
}
</style>
