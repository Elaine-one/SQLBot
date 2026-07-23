/**
 * CartesianChartModel — 统一笛卡尔图表数据模型
 *
 * 对标 Metabase 的 getCartesianChartModel()。
 * 把 Column/Bar/Line 三种图表的 init() 中重复的数据处理逻辑提取到此处。
 *
 * 职责:
 *   1. 通道分类: 分析每列的语义类型 (temporal/ordinal/categorical/metric)
 *   2. 通道映射: 根据 chartType 的通道要求，将列映射到 x/y/color
 *   3. 数据变换: 排序、多指标展开(multi-quota)、百分比检测
 *   4. 生成 G2 options: model.toG2Option() → 可直接传给 chart.options()
 */

import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import type { ChartTypes } from '@/views/chat/component/BaseChart'
import {
  checkIsPercent,
  formatNumber,
  getAxesWithFilter,
  fallbackXYAxes,
  isTimeField,
  sortDataByTimeField,
  processMultiQuotaData,
  buildAxisTitle,
  inferAxisUnit,
  classifyColumn,
  isDimensionChannel as isDimensionType,
} from '@/views/chat/component/charts/utils'

// ═══════════════════════════════════════════════════════════════════════════════
//  类型定义
// ═══════════════════════════════════════════════════════════════════════════════

export type ChannelRole = 'x' | 'y' | 'color'
export type ChannelType = 'temporal' | 'ordinal' | 'categorical' | 'metric' | 'unknown'

export interface ChannelDef {
  role: ChannelRole
  type: ChannelType
  field: string       // data key
  name: string        // display name
}

export interface CartesianChartConfig {
  type: ChartTypes       // 'column' | 'bar' | 'line'
  x: ChannelDef
  y: ChannelDef
  color: ChannelDef | null
  data: ChartData[]
  isPercent: boolean
  numberFormat: 'full' | 'abbreviated' | 'percent'
  showLabel: boolean
  sortOrder: 'asc' | 'desc' | 'none'
  /** settings from ChartBlock (color_palette, grid, stack, smooth, etc.) */
  activeSettings: Record<string, any>
}

// ═══════════════════════════════════════════════════════════════════════════════
//  通道分类 — 复用 utils.ts 的 classifyColumn / isDimensionChannel
// ═══════════════════════════════════════════════════════════════════════════════

const RATIO_NAME_PATTERN = /率|%|percent|ratio|占比|份额|百分比/i

// ═══════════════════════════════════════════════════════════════════════════════
//  通道映射规则 — 对标 Metabase 的 getDefaultDimensions / getDefaultMetrics
// ═══════════════════════════════════════════════════════════════════════════════

export interface ChannelRequirement {
  role: ChannelRole
  required: boolean
  dimensionOnly: boolean
  forbiddenTypes: ChannelType[]
  preferredTypes: ChannelType[]
  maxCardinality: number
  ratioForbidden: boolean
}

/** 兜底通道规则 — 与 chart_registry.py 同步。运行时优先使用后端 API 下发。 */
export const FALLBACK_CHANNEL_REQUIREMENTS: Record<string, ChannelRequirement[]> = {
  column: [
    { role: 'x', required: true, dimensionOnly: true, forbiddenTypes: ['temporal'], preferredTypes: ['categorical', 'ordinal'], maxCardinality: 60, ratioForbidden: false },
    { role: 'y', required: true, dimensionOnly: false, forbiddenTypes: [], preferredTypes: ['metric'], maxCardinality: Infinity, ratioForbidden: false },
    { role: 'color', required: false, dimensionOnly: true, forbiddenTypes: ['temporal'], preferredTypes: ['categorical'], maxCardinality: 10, ratioForbidden: false },
  ],
  bar: [
    { role: 'x', required: true, dimensionOnly: true, forbiddenTypes: ['temporal'], preferredTypes: ['categorical', 'ordinal'], maxCardinality: 60, ratioForbidden: false },
    { role: 'y', required: true, dimensionOnly: false, forbiddenTypes: [], preferredTypes: ['metric'], maxCardinality: Infinity, ratioForbidden: false },
    { role: 'color', required: false, dimensionOnly: true, forbiddenTypes: ['temporal'], preferredTypes: ['categorical'], maxCardinality: 10, ratioForbidden: false },
  ],
  line: [
    { role: 'x', required: true, dimensionOnly: true, forbiddenTypes: [], preferredTypes: ['temporal', 'ordinal'], maxCardinality: 500, ratioForbidden: false },
    { role: 'y', required: true, dimensionOnly: false, forbiddenTypes: [], preferredTypes: ['metric'], maxCardinality: Infinity, ratioForbidden: false },
    { role: 'color', required: false, dimensionOnly: true, forbiddenTypes: [], preferredTypes: ['categorical', 'ordinal'], maxCardinality: 20, ratioForbidden: false },
  ],
}

// ═══════════════════════════════════════════════════════════════════════════════
//  工具：将后端 chart_registry.py 的 required_channels 转为内部 ChannelRequirement[]
// ═══════════════════════════════════════════════════════════════════════════════

const CHANNEL_ROLE_MAP: Record<string, ChannelRole> = {
  x: 'x', y: 'y', theta: 'y', color: 'color', series: 'color',
}

function normalizeApiChannels(
  apiChannels: Record<string, any>,
  _chartType: string,
): ChannelRequirement[] {
  const result: ChannelRequirement[] = []
  for (const [key, spec] of Object.entries(apiChannels)) {
    if (!spec || typeof spec !== 'object') continue
    const role = CHANNEL_ROLE_MAP[key] || (key as ChannelRole)

    result.push({
      role,
      required: !spec.optional,
      dimensionOnly: spec.type === 'dimension',
      forbiddenTypes: (spec.forbidden || []) as ChannelType[],
      preferredTypes: (spec.preferred || []) as ChannelType[],
      maxCardinality: spec.maxCardinality ?? spec.max_cardinality ?? Infinity,
      ratioForbidden: !!(spec.ratioForbidden || spec.ratio_forbidden),
    })
  }
  return result
}

// ═══════════════════════════════════════════════════════════════════════════════
//  Model 构造函数
// ═══════════════════════════════════════════════════════════════════════════════

export function buildCartesianModel(
  chartType: ChartTypes,
  axis: ChartAxis[],
  data: ChartData[],
  activeSettings: Record<string, any>,
  showLabel: boolean,
  numberFormat: 'full' | 'abbreviated' | 'percent',
  sortOrder: 'asc' | 'desc' | 'none',
  /** 后端 API 下发的 required_channels，优先于 FALLBACK */
  apiRequiredChannels?: Record<string, any>,
): CartesianChartConfig | null {
  if (!data || data.length === 0) return null

  // 优先用 API 下发的通道规则，兜底用 FALLBACK_CHANNEL_REQUIREMENTS
  const requirements = apiRequiredChannels
    ? normalizeApiChannels(apiRequiredChannels, chartType)
    : FALLBACK_CHANNEL_REQUIREMENTS[chartType]
  if (!requirements) return null

  // Step 1: 分类每列
  const columnMetas = axis
    .filter((a) => a.value && !a.hidden)
    .map((a) => ({
      name: a.name,
      value: a.value,
      channel: classifyColumn(data, a.value),
      uniqueCount: new Set(data.map((d) => String(d[a.value] ?? ''))).size,
      isRatio: RATIO_NAME_PATTERN.test(a.name),
    }))

  // Step 2: 处理 getAxesWithFilter（保留 AI 标注的类型作为 hint）
  const filtered = getAxesWithFilter(axis)
  let xAxes = filtered.x
  let yAxes = filtered.y
  let seriesAxes = filtered.series
  let multiQuota = filtered.multiQuota
  const multiQuotaName = filtered.multiQuotaName

  // Step 3: 兜底 — 如果 axis 没有类型标注，从 data 推导
  if (xAxes.length === 0 || yAxes.length === 0) {
    const fallback = fallbackXYAxes(axis, data)
    if (fallback.x && xAxes.length === 0) xAxes = [{ ...fallback.x, type: 'x' }]
    if (fallback.y && yAxes.length === 0) yAxes = [{ ...fallback.y, type: 'y' }]
  }

  if (xAxes.length === 0 || yAxes.length === 0) return null

  // Step 4: 按 chartType 的通道要求筛选（对标 Metabase _isAxisValueCompatible）
  const xReq = requirements.find((r) => r.role === 'x')!
  const yReq = requirements.find((r) => r.role === 'y')!
  const colorReq = requirements.find((r) => r.role === 'color')!

  // 检查 x 是否合法
  const xChannel = columnMetas.find((m) => m.value === xAxes[0].value)
  // 检查1：x 在 forbiddenTypes 中
  if (xChannel && xReq.forbiddenTypes.includes(xChannel.channel)) {
    const alt = columnMetas.find(
      (m) =>
        m.value !== yAxes[0].value &&
        isDimensionType(m.channel) &&
        !xReq.forbiddenTypes.includes(m.channel)
    )
    if (alt) xAxes = [{ name: alt.name, value: alt.value, type: 'x' }]
  }
  // 检查2：x 必须是 dimension（dimensionOnly=true 时），否则与 y 交换
  if (xChannel && xReq.dimensionOnly && !isDimensionType(xChannel.channel)) {
    const yChannel = columnMetas.find((m) => m.value === yAxes[0].value)
    // 如果 y 是 dimension 而 x 是 metric，交换 x↔y
    if (yChannel && isDimensionType(yChannel.channel)) {
      console.warn(
        `[CartesianChartModel] x 通道 "${xAxes[0].value}" 是 ${xChannel.channel} 不是 dimension，` +
        `与 y 通道 "${yAxes[0].value}"(${yChannel.channel}) 交换纠正`
      )
      const tmp = xAxes[0]
      xAxes = [{ ...yAxes[0], type: 'x' }]
      yAxes = [{ ...tmp, type: 'y' }]
    }
  }

  // 检查 y 是否合法
  const yChannel = columnMetas.find((m) => m.value === yAxes[0].value)
  const yName = yAxes[0].name
  if (yChannel && yReq.ratioForbidden && yChannel.isRatio) {
    const alt = columnMetas.find(
      (m) => m.value !== xAxes[0].value && m.channel === 'metric' && !m.isRatio
    )
    if (alt) yAxes = [{ name: alt.name, value: alt.value, type: 'y' }]
  }

  // Step 5: 多指标展开
  let configData = data
  if (multiQuota.length > 0 && seriesAxes.length === 0) {
    const result = processMultiQuotaData(xAxes, yAxes, multiQuota, multiQuotaName, configData)
    configData = result.data
    yAxes = result.y
    seriesAxes = result.series
  }

  // Step 6: 百分比检测
  const percentResult = checkIsPercent(yAxes, configData)

  // Step 7: 排序
  const xField = xAxes[0].value
  const xIsTime = isTimeField(percentResult.data, xField)
  if (xIsTime) {
    percentResult.data = sortDataByTimeField(percentResult.data, xField)
  } else if (sortOrder !== 'none') {
    const yField = yAxes[0].value
    percentResult.data.sort((a, b) => {
      const va = Number(a[yField]) || 0
      const vb = Number(b[yField]) || 0
      return sortOrder === 'asc' ? va - vb : vb - va
    })
  }

  // Step 7½: line chart with sparse series → drop color channel.
  //   Each series needs ≥2 points to form a visible line segment.
  //   When avg points/series < 2 (e.g. 5 rows ÷ 5 unique categories = 1),
  //   drawing separate lines produces invisible single-dot "lines".
  let colorAxis = seriesAxes.length > 0 ? seriesAxes[0] : null
  if (chartType === 'line' && colorAxis) {
    const seriesCount = new Set(percentResult.data.map((d) => String(d[colorAxis!.value] ?? ''))).size
    if (seriesCount > 1 && percentResult.data.length / seriesCount < 2) {
      console.warn(
        `[CartesianChartModel] line chart series too sparse ` +
        `(${percentResult.data.length} pts ÷ ${seriesCount} series = ${(percentResult.data.length / seriesCount).toFixed(1)} avg) → ` +
        `dropping color channel for better readability`
      )
      colorAxis = null
    }
  }

  // Step 8: 组装 model
  const xMeta = columnMetas.find((m) => m.value === xField)!
  const yMeta = columnMetas.find((m) => m.value === yAxes[0].value)!
  const colorMeta = colorAxis ? columnMetas.find((m) => m.value === colorAxis!.value) || null : null

  return {
    type: chartType,
    x: { role: 'x', type: xMeta?.channel ?? 'categorical', field: xField, name: xAxes[0].name || xField },
    y: { role: 'y', type: yMeta?.channel ?? 'metric', field: yAxes[0].value, name: yAxes[0].name },
    color: colorMeta
      ? { role: 'color', type: colorMeta.channel, field: colorAxis!.value, name: colorAxis!.name }
      : null,
    data: percentResult.data,
    isPercent: percentResult.isPercent,
    numberFormat,
    showLabel,
    sortOrder,
    activeSettings,
  }
}
