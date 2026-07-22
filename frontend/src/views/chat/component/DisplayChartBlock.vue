<script setup lang="ts">
import ChartComponent from '@/views/chat/component/ChartComponent.vue'
import type { ChatMessage } from '@/api/chat.ts'
import { computed, nextTick, onUnmounted, reactive, ref } from 'vue'
import type { ChartAxis, ChartTypes } from '@/views/chat/component/BaseChart.ts'
import { useI18n } from 'vue-i18n'
import {
  classifyColumn,
  isDimensionChannel,
  recommendAxes,
  recommendChartType,
  validateChartTypeChannels,
} from '@/views/chat/component/charts/utils.ts'
import { useChartTypeConfig } from '@/views/chat/component/useChartTypeConfig.ts'

const props = defineProps<{
  id?: number | string
  chartType: ChartTypes
  message: ChatMessage
  data: Array<{ [key: string]: any }>
  loadingData?: boolean
  showLabel?: boolean
}>()

const { t } = useI18n()
const { config } = useChartTypeConfig()

// 当前图表类型在后端/配置中定义的通道要求（SSOT）。
// 后端 get_frontend_config 下发 snake_case 的 required_channels；FALLBACK_CONFIG 使用 camelCase 的 requiredChannels。
const currentTypeRequiredChannels = computed(() => {
  if (props.chartType === 'table') return undefined
  const dc = config.value?.chartTypes[props.chartType]?.dataConstraints
  return dc?.requiredChannels ?? dc?.required_channels ?? undefined
})

// 从 chart JSON 或 data 首行推导可用列。某些情况下 LLM 未生成 columns 或 value 与 data key 不匹配，
// 用 data[0] 的 keys 兜底，保证 recommendAxes / validateChartTypeChannels 有列可分析。
const effectiveColumns = computed(() => {
  const cols = chartObject.value?.columns ?? []
  if (cols.length > 0) return cols
  if (!props.data || props.data.length === 0) return []
  return Object.keys(props.data[0]).map((key) => ({ name: key, value: key }))
})

const chartObject = computed<{
  type: ChartTypes
  title: string
  axis: {
    x: { name: string; value: string }
    y: { name: string; value: string } | Array<{ name: string; value: string }>
    series: { name: string; value: string }
    'multi-quota': {
      name: string
      value: Array<string>
    }
  }
  columns: Array<{ name: string; value: string }>
}>(() => {
  if (props.message?.record?.chart) {
    return JSON.parse(props.message.record.chart)
  }
  return {}
})

/**
 * 判断 chartObject 中已有的轴字段是否适用于目标图表类型。
 * 基于通道语义：y 必须是 metric，x/series 必须是 dimension（对比类 x 不能是 temporal）。
 *
 * 注意：折线图的 x 轴优选 temporal/ordinal，但不强制禁止 categorical。
 * 通道优先级由 recommendAxes 按 preferred 语义处理，后端 API 的 required_channels
 * 仅对 line.x 设置 preferred:['temporal','ordinal'] 而无 forbidden。
 */
function _isAxisValueCompatible(
  value: string,
  target: 'x' | 'y' | 'series',
  chartType: ChartTypes
): boolean {
  const rows = props.data
  if (!rows || rows.length === 0) return true
  const col = effectiveColumns.value.find((c) => c.value === value)
  if (!col) return false

  const channel = classifyColumn(rows, value)

  if (target === 'y') {
    return channel === 'metric'
  }

  if (target === 'x') {
    if (chartType === 'column' || chartType === 'bar') {
      return channel !== 'temporal'
    }
    return isDimensionChannel(channel)
  }

  if (target === 'series') {
    return isDimensionChannel(channel)
  }

  return true
}

// 统一推荐结果（通道语义模型）
const recommendedAxes = computed(() => {
  if (props.chartType === 'table') return { x: undefined, y: undefined, series: undefined }
  const result = recommendAxes(
    props.message?.record?.question,
    props.chartType,
    props.data,
    effectiveColumns.value,
    currentTypeRequiredChannels.value
  )
  console.debug('[DisplayChartBlock] recommendedAxes', {
    chartType: props.chartType,
    dataLength: props.data?.length,
    columns: effectiveColumns.value,
    result,
  })
  return result
})

// 最终轴：AI 给出的 axis 若兼容则沿用，否则由 recommendAxes 按通道语义推荐
const xAxis = computed(() => {
  if (props.chartType === 'table') return []
  const axis = chartObject.value?.axis
  if (axis?.x && _isAxisValueCompatible(axis.x.value, 'x', props.chartType)) {
    const result = [{ name: axis.x.name, value: axis.x.value, type: 'x' }]
    console.log(`[DisplayChartBlock] xAxis (from AI): ${axis.x.value}`)
    return result
  }
  const value = recommendedAxes.value.x
  if (value) {
    const col = effectiveColumns.value.find((c) => c.value === value)
    if (col) {
      console.log(`[DisplayChartBlock] xAxis (from recommend): ${value}`)
      return [{ name: col.name, value: col.value, type: 'x' }]
    }
  }
  // 折线图兜底：优先选择时间字段作为 x 轴
  if (props.chartType === 'line') {
    const timeCol = effectiveColumns.value.find(
      (c) => classifyColumn(props.data, c.value) === 'temporal'
    )
    if (timeCol) {
      console.log(`[DisplayChartBlock] xAxis (line time fallback): ${timeCol.value}`)
      return [{ name: timeCol.name, value: timeCol.value, type: 'x' }]
    }
  }
  console.warn(`[DisplayChartBlock] xAxis: EMPTY | chartType=${props.chartType} | effectiveCols=`, effectiveColumns.value.map(c => `${c.value}(${classifyColumn(props.data, c.value)})`))
  return []
})

const yAxis = computed(() => {
  if (props.chartType === 'table') return []
  const axis = chartObject.value?.axis
  if (axis?.y) {
    const y = axis.y
    const multiQuotaValues = axis['multi-quota']?.value || []
    const yArray = Array.isArray(y) ? [...y] : [{ ...y }]
    // pie 比率字段和负值字段：打印警告但不拒绝，让用户自行判断
    if (props.chartType === 'pie' && yArray[0]) {
      const yName = yArray[0].name
      if (/率|%|percent|ratio|占比|份额|百分比/i.test(yName)) {
        console.warn(`[DisplayChartBlock] pie theta 字段 "${yName}" 是比率/百分比字段，饼图扇区大小≠该比率的占比，请注意`)
      }
      const yValue = yArray[0].value
      const hasNegative = (props.data || []).slice(0, 50).some(
        (d) => Number(d[yValue]) < 0
      )
      if (hasNegative) {
        console.warn(`[DisplayChartBlock] pie theta 字段 "${yName}" 含负值，饼图可能渲染异常`)
      }
    }
    return yArray.map((item) => ({
      ...item,
      type: 'y',
      'multi-quota': multiQuotaValues.includes(item.value),
    }))
  }
  const value = recommendedAxes.value.y
  if (value) {
    const col = effectiveColumns.value.find((c) => c.value === value)
    if (col) return [{ name: col.name, value: col.value, type: 'y' }]
  }
  return []
})

const series = computed(() => {
  if (props.chartType === 'table') return []
  const axis = chartObject.value?.axis
  if (axis?.series && _isAxisValueCompatible(axis.series.value, 'series', props.chartType)) {
    return [{ name: axis.series.name, value: axis.series.value, type: 'series' }]
  }
  const value = recommendedAxes.value.series
  if (value) {
    const col = effectiveColumns.value.find((c) => c.value === value)
    if (col) return [{ name: col.name, value: col.value, type: 'series' }]
  }
  return []
})

const multiQuotaName = computed(() => {
  return chartObject.value?.axis?.['multi-quota']?.name
})

const chartRef = ref()

function destroyChart() {
  chartRef.value?.destroyChart()
}

function onTypeChange(settings?: Record<string, any>) {
  // 不手动 destroy！_doRender 内部根据 _currentType 判断走原地切换还是 destroy+create。
  // 手动 destroy 会清掉 _currentType，导致笛卡尔原地切换逻辑被跳过。
  chartRef.value?.renderChart(settings)
}

/**
 * 校验目标图表类型是否能被当前数据满足。
 * 供 ChartBlock 在类型切换前做兼容性检查或自动降级提示。
 *
 * 注意：必须用「目标类型」的 requiredChannels，不能用 currentTypeRequiredChannels（那是当前类型的）。
 */
function canRenderChartType(chartType: ChartTypes): { ok: boolean; reason?: string } {
  if (chartType === 'table') return { ok: true }
  // 从 config 获取「目标类型」的通道要求，而非当前类型
  const targetTypeRequiredChannels = config.value?.chartTypes[chartType]?.dataConstraints?.requiredChannels
    ?? config.value?.chartTypes[chartType]?.dataConstraints?.required_channels
    ?? undefined
  const result = validateChartTypeChannels(
    chartType,
    props.data,
    effectiveColumns.value,
    targetTypeRequiredChannels  // ← 目标类型的通道，不是当前类型
  )
  console.debug('[DisplayChartBlock] canRenderChartType', {
    chartType,
    dataLength: props.data?.length,
    columns: effectiveColumns.value,
    result,
  })
  return result
}

function getViewInfo() {
  return {
    chart: {
      columns: effectiveColumns.value,
      type: props.chartType,
      xAxis: xAxis.value,
      yAxis: yAxis.value,
      series: series.value,
      title: chartObject.value.title,
    },
    data: { data: props.data },
  }
}
function getExcelData() {
  return chartRef.value?.getExcelData()
}

function getRecommendedChartType(): ChartTypes | null {
  return recommendChartType(
    props.message?.record?.question,
    props.data,
    effectiveColumns.value
  ) as ChartTypes | null
}

function applySettings(settings: Record<string, any>) {
  // 已删除 X/Y/series 手动选择入口；settings 中不再处理轴字段覆盖
  chartRef.value?.renderChart?.(settings)
}

defineExpose({
  onTypeChange,
  destroyChart,
  canRenderChartType,
  getViewInfo,
  getExcelData,
  getRecommendedChartType,
  applySettings,
})

onUnmounted(() => {
  chartRef.value?.destroyChart()
})
</script>

<template>
  <div v-if="message.record?.chart" class="chart-base-container">
    <ChartComponent
      v-if="message.record.id && data?.length > 0"
      :id="id ?? 'default_chat_id'"
      ref="chartRef"
      :type="chartType"
      :columns="effectiveColumns"
      :x="xAxis"
      :y="yAxis"
      :series="series"
      :data="data"
      :multi-quota-name="multiQuotaName"
      :show-label="showLabel"
    />
    <div v-else class="chart-empty-state">
      <svg width="48" height="48" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="4" y="4" width="40" height="32" rx="3" stroke="#c0c4cc" stroke-width="1.5" fill="none"/>
        <rect x="10" y="26" width="8" height="10" rx="0.5" fill="#e0e2e6"/>
        <rect x="20" y="20" width="8" height="16" rx="0.5" fill="#e0e2e6"/>
        <rect x="30" y="14" width="8" height="22" rx="0.5" fill="#e0e2e6"/>
      </svg>
      <div class="chart-empty-text">{{ loadingData ? t('chat.loading_data') : t('chat.no_data') }}</div>
    </div>
  </div>
</template>

<style scoped lang="less">
.chart-base-container {
  height: 100%;
  width: 100%;
  border-radius: 12px;
  background: rgba(224, 224, 226, 0.29);
}
.chart-empty-state {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: #8f959e;
  font-size: 14px;
  .chart-empty-text { opacity: 0.7; }
}
</style>
