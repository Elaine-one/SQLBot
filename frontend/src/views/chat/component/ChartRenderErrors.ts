/**
 * ChartRenderErrors — 对标 Metabase 的 errors.ts
 *
 * checkRenderable() 抛出具体错误类型，调用方 catch 后展示原因给用户。
 * 替代原来『_initOk = false + 通用灰色提示』的粗糙方案。
 */

import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import { classifyColumn, isDimensionChannel } from '@/views/chat/component/charts/utils'

// ═══════════════════════════════════════════════════════════════════════════════
//  错误类 — 对标 Metabase MinRowsError / ChartSettingsError
// ═══════════════════════════════════════════════════════════════════════════════

const TYPE_LABEL: Record<string, string> = {
  column: '柱状图', bar: '条形图', line: '折线图', pie: '饼图',
}

export class ChartRenderError extends Error {
  name = 'ChartRenderError'
  /** 建议操作，如 "切换到明细表" */
  suggestion: string
  constructor(message: string, suggestion = '请切换到明细表查看原始数据。') {
    super(message)
    this.suggestion = suggestion
  }
}

export class NoDimensionError extends ChartRenderError {
  constructor(chartType: string) {
    const label = TYPE_LABEL[chartType] || chartType
    const msg =
      `${label}需要一个分类/时间维度（如平台、日期、地区）作为 X 轴，` +
      `当前数据找不到合适的维度字段。`
    super(msg, `建议切换到明细表，或确认查询结果包含分类字段。`)
  }
}

export class NoMetricError extends ChartRenderError {
  constructor(chartType: string) {
    const label = TYPE_LABEL[chartType] || chartType
    const msg =
      `${label}需要一个数值指标（如金额、数量、比率）作为 Y 轴，` +
      `当前数据找不到合适的数值字段。`
    super(msg, `建议切换到明细表，或确认查询结果包含数值字段。`)
  }
}

export class PieRatioError extends ChartRenderError {
  constructor(fieldName: string) {
    super(
      `饼图的扇区大小表示绝对占比，比率字段"${fieldName}"会导致误读。` +
      `例如 22.5% 会被当作 22.5 的占比而非整体的 22.5%。`,
      `建议切换到柱状图或条形图。`
    )
  }
}

export class ColumnTemporalXError extends ChartRenderError {
  constructor(chartType: string) {
    super(
      `${TYPE_LABEL[chartType]}的 X 轴不应使用时间维度——` +
      `${TYPE_LABEL[chartType]}用于分类对比，时间趋势应使用折线图。`,
      `建议切换到折线图。`
    )
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  checkRenderable — 对标 Metabase 的 checkRenderable 硬门禁
// ═══════════════════════════════════════════════════════════════════════════════

export function checkRenderable(
  chartType: string,
  axis: ChartAxis[],
  data: ChartData[],
): void {
  // 1. 数据为空
  if (!data || data.length === 0) {
    throw new ChartRenderError('查询结果为空，没有数据可以渲染图表。')
  }

  // 2. 无列
  if (!axis || axis.length === 0) {
    throw new ChartRenderError('没有可用的列信息，无法确定图表的 X/Y 轴。')
  }

  // 3. 分析每列类型
  const visible = axis.filter((a) => a.value && !a.hidden)
  const channels = visible.map((a) => ({
    name: a.name || a.value,
    value: a.value,
    channel: classifyColumn(data, a.value),
    uniqueCount: new Set(data.map((d) => String(d[a.value] ?? ''))).size,
    isRatio: /率|%|percent|ratio|占比|份额|百分比/i.test(a.name || a.value),
  }))

  const dimensions = channels.filter((c) => isDimensionChannel(c.channel))
  const metrics = channels.filter((c) => c.channel === 'metric')

  // 4. 饼图
  if (chartType === 'pie') {
    const metric = channels.find((c) => c.channel === 'metric')
    if (!metric) throw new NoMetricError('pie')
    if (dimensions.length === 0) throw new NoDimensionError('pie')
    return
  }

  // 5. 笛卡尔
  if (dimensions.length === 0) throw new NoDimensionError(chartType)
  if (metrics.length === 0) throw new NoMetricError(chartType)

  // 6. column/bar x 轴不能用时间
  if ((chartType === 'column' || chartType === 'bar') && dimensions[0]?.channel === 'temporal') {
    throw new ColumnTemporalXError(chartType)
  }
}
