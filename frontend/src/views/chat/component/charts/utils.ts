import type { ChartAxis, ChartData, ChartTypes } from '@/views/chat/component/BaseChart.ts'
import { endsWith, filter, replace } from 'lodash-es'

/**
 * 为数值添加千分符/缩写/百分比，保持原有小数位数不变
 * 纯字符串处理，避免精度丢失
 * 支持：正负整数、小数、字符串格式的数值
 *
 * @param format - 格式化模式：full=完整千分位, abbreviated=自动缩写(万/亿), percent=百分比
 */
export function formatNumber(value: any, format?: 'full' | 'abbreviated' | 'percent'): string | number {
  if (value === null || value === undefined || value === '') {
    return value
  }

  let str: string
  if (typeof value === 'string') {
    str = value.trim()
  } else if (typeof value === 'number') {
    str = String(value)
  } else {
    return value
  }

  // 识别并剥离百分比后缀
  const hasPercentSuffix = str.endsWith('%')
  const numericStr = hasPercentSuffix ? str.slice(0, -1) : str

  const match = numericStr.match(/^([+-])?(\d+)(\.(\d+))?$/)
  if (!match) {
    return value
  }

  const sign = match[1] || ''
  const intPart = match[2]
  const decPart = match[3] || ''

  const num = Number(numericStr)
  if (Number.isNaN(num)) {
    return value
  }

  const finalFormat = format || 'abbreviated'

  if (finalFormat === 'percent' || hasPercentSuffix) {
    return sign + num.toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + '%'
  }

  if (finalFormat === 'full') {
    const formattedInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
    return sign + formattedInt + decPart
  }

  // abbreviated (default): 万/亿
  if (num >= 100000000) return (num / 100000000).toFixed(2) + '亿'
  if (num >= 10000) return (num / 10000).toFixed(2) + '万'
  const formattedInt = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return sign + formattedInt + decPart
}

interface CheckedData {
  isPercent: boolean
  data: Array<ChartData>
}

export function getAxesWithFilter(axes: ChartAxis[]): {
  x: ChartAxis[]
  y: ChartAxis[] // 过滤后的 y
  series: ChartAxis[]
  multiQuota: string[] // series 为空时返回 multi-quota 为 true 的 y 轴 value 列表
  multiQuotaName?: string
} {
  const groups = {
    x: [] as ChartAxis[],
    y: [] as ChartAxis[],
    series: [] as ChartAxis[],
    multiQuota: [] as string[],
    multiQuotaName: undefined as string | undefined,
  }

  // 分组
  axes.forEach((axis) => {
    if (axis.type === 'x') groups.x.push(axis)
    else if (axis.type === 'y') groups.y.push(axis)
    else if (axis.type === 'series') groups.series.push(axis)
    else if (axis.type === 'other-info') groups.multiQuotaName = axis.value
  })

  // 应用过滤规则
  if (groups.series.length > 0) {
    groups.y = groups.y.slice(0, 1)
  } else {
    const multiQuotaY = groups.y.filter((item) => item['multi-quota'] === true)
    groups.multiQuota = multiQuotaY.map((item) => item.value)
    if (multiQuotaY.length > 0) {
      groups.y = multiQuotaY
    }
  }

  return groups
}

/**
 * 当 axis 中缺少 x/y 类型标记时，从所有列中兜底推导 x（分类/时间）和 y（数值）。
 * 用于 AI 选择 table 后切换为可视化图表、或 axis 信息不完整时的安全降级。
 */
export function fallbackXYAxes(
  axis: Array<ChartAxis>,
  data: Array<ChartData>
): { x?: ChartAxis; y?: ChartAxis } {
  if (!axis || axis.length === 0 || !data || data.length === 0) return {}

  const sample = data.slice(0, 20)

  function isNumeric(field: string): boolean {
    const nonEmpty = sample.filter((d) => d[field] !== null && d[field] !== undefined && d[field] !== '')
    if (nonEmpty.length === 0) return false
    const numericCount = nonEmpty.filter((d) => {
      const s = String(d[field]).replace(/[,%]/g, '').trim()
      return s !== '' && !isNaN(Number(s))
    }).length
    return numericCount / nonEmpty.length >= 0.8
  }

  function findCandidate(preferNumeric: boolean, exclude?: string): ChartAxis | undefined {
    const cols = axis.filter((a) => !a.hidden && a.value !== exclude)
    if (preferNumeric) {
      return cols.find((a) => isNumeric(a.value)) || cols[0]
    }
    return cols.find((a) => !isNumeric(a.value) && !isTimeField(data, a.value)) || cols.find((a) => !isNumeric(a.value)) || cols[0]
  }

  let y = axis.find((a) => a.type === 'y')
  let x = axis.find((a) => a.type === 'x')

  if (!y) {
    y = findCandidate(true, x?.value)
  }
  if (!x || x.value === y?.value) {
    x = findCandidate(false, y?.value)
  }

  if (x && x.value === y?.value) {
    // 只有一列时无法同时作为 x 和 y，优先让 y 为空（渲染会给出更明确的兜底提示）
    y = undefined
  }

  return { x, y }
}

export function processMultiQuotaData(
  x: Array<ChartAxis>,
  y: Array<ChartAxis>,
  multiQuota: Array<string>,
  multiQuotaName: string = 'sqlbot_auto_series',
  data: Array<ChartData>
) {
  const _list: Array<ChartData> = []
  const _map: { [propName: string]: string } = {}
  y.forEach((axis) => {
    _map[axis.value] = axis.name
  })
  for (const datum of data) {
    multiQuota.forEach((quota) => {
      const _data: { [propName: string]: any } = {}
      for (const xAxis of x) {
        _data[xAxis.value] = datum[xAxis.value]
      }
      _data['sqlbot_auto_quota'] = datum[quota]
      _data['sqlbot_auto_series'] = _map[quota]
      _list.push(_data)
    })
  }

  return {
    data: _list,
    y: [{ name: 'sqlbot_auto_quota', value: 'sqlbot_auto_quota', type: 'y' } as ChartAxis],
    series: [{ name: multiQuotaName, value: 'sqlbot_auto_series', type: 'series' } as ChartAxis],
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
//  智能轴辅助函数：时间/分类检测、时间排序、TopN 聚合
// ═══════════════════════════════════════════════════════════════════════════════

const TIME_NAME_PATTERN = /时间|日期|date|time|年|月|日|day|month|year|timestamp|datetime/i
const DATE_VALUE_PATTERN = /^\d{4}[-/]\d{1,2}([-/]\d{1,2})?/
/** 明显不是时间字段的名称模式（比率/百分比/金额/计数等），避免 Date.parse 误判 */
const NON_TIME_NAME_PATTERN = /率|%|percent|ratio|占比|份额|百分比|金额|数量|个数|收入|利润|销售额|成本|gmv|revenue|profit|cost|amount|count|uv|pv/i

/**
 * 判断字段值是否像日期/时间
 */
function isTimeLikeValue(value: any): boolean {
  if (value === null || value === undefined || value === '') return false
  const s = String(value)
  // 必须是至少 7 个字符的类日期格式（如 2025-01-01），排除短数字如 2025、22.5
  if (s.length < 7) return false
  if (DATE_VALUE_PATTERN.test(s)) return true
  const d = Date.parse(s)
  return !isNaN(d)
}

/**
 * 判断字段是否为时间维度（列名暗示或多数值像日期）
 */
export function isTimeField(data: Array<ChartData>, field: string, sampleSize = 20): boolean {
  if (!data || data.length === 0 || !field) return false
  // 字段名明显不是时间（比率/金额/计数类）→ 直接返回 false，避免 Date.parse 误判
  if (NON_TIME_NAME_PATTERN.test(field)) return false
  if (TIME_NAME_PATTERN.test(field)) return true

  const sample = data.slice(0, sampleSize)
  const nonEmpty = sample.filter((d) => d[field] !== null && d[field] !== undefined && d[field] !== '')
  if (nonEmpty.length === 0) return false
  const timeLikeCount = nonEmpty.filter((d) => isTimeLikeValue(d[field])).length
  return timeLikeCount / nonEmpty.length >= 0.5
}

/**
 * 判断字段是否为分类维度（非数值且非时间）
 */
export function isCategoricalField(data: Array<ChartData>, field: string): boolean {
  if (!data || data.length === 0 || !field) return false
  if (isTimeField(data, field)) return false
  const sample = data.slice(0, 20)
  const nonEmpty = sample.filter((d) => d[field] !== null && d[field] !== undefined && d[field] !== '')
  if (nonEmpty.length === 0) return true
  const numericCount = nonEmpty.filter((d) => {
    const s = String(d[field]).replace(/[,%]/g, '').trim()
    return s !== '' && !isNaN(Number(s))
  }).length
  return numericCount / nonEmpty.length < 0.5
}

const ORDINAL_NAME_PATTERN = /阶段|序号|排名|位次|周次|轮次|期数|步骤|级别|年级|版本|迭代|次序|order|rank|stage|phase|step|level|round/i

/**
 * 判断字段是否为有序分类维度（非时间，但具有业务上的顺序语义）。
 * 仅通过列名判断，避免把“净利润率(%)”这种少量不同取值的数值指标误判为有序维度。
 */
export function isOrdinalField(data: Array<ChartData>, field: string): boolean {
  if (!data || data.length === 0 || !field) return false
  if (isTimeField(data, field)) return false
  return ORDINAL_NAME_PATTERN.test(field)
}

const UNIT_PATTERNS: Array<{ pattern: RegExp; unit: string }> = [
  { pattern: /(?:金额|销售额|收入|营收|利润|成本|gmv|price|revenue|profit|cost|amount)/i, unit: '元' },
  { pattern: /(?:人数|uv|pv|访问量|点击量|订单量|销量|数量|件|单|个|笔|次)/i, unit: '个/件' },
  { pattern: /(?:率|%|percent|ratio|占比|份额)/i, unit: '%' },
  { pattern: /(?:分钟|分|min)/i, unit: '分' },
  { pattern: /(?:小时|时|hour|h\b)/i, unit: '小时' },
  { pattern: /(?:秒|second|s\b)/i, unit: '秒' },
]

/**
 * 根据列名推断数值单位，用于生成坐标轴标题后缀。
 * 时间/日期列不返回单位。
 */
export function inferAxisUnit(name: string | undefined): string | undefined {
  if (!name) return undefined
  if (/时间|日期|date|time|timestamp|datetime|年|月|日/i.test(name)) return undefined
  for (const { pattern, unit } of UNIT_PATTERNS) {
    if (pattern.test(name)) return unit
  }
  return undefined
}

/**
 * 组合坐标轴标题：若原列名已包含单位则不再追加。
 */
export function buildAxisTitle(name: string | undefined, unit?: string): string {
  const cleaned = (name || '').trim()
  if (!cleaned) return ''
  if (!unit) return cleaned
  // 避免重复追加单位（支持“销售额（元）”或“销售额(元)”）
  const unitRegex = new RegExp(`[（(]?${unit.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}[）)]?`)
  if (unitRegex.test(cleaned)) return cleaned
  return `${cleaned}（${unit}）`
}

/**
 * 按时间字段对数据升序排序，非时间值排在末尾
 */
export function sortDataByTimeField(data: Array<ChartData>, field: string): Array<ChartData> {
  return [...data].sort((a, b) => {
    const av = a[field]
    const bv = b[field]
    const ad = Date.parse(String(av))
    const bd = Date.parse(String(bv))
    if (!isNaN(ad) && !isNaN(bd)) return ad - bd
    if (isNaN(ad) && isNaN(bd)) return String(av).localeCompare(String(bv))
    return isNaN(ad) ? 1 : -1
  })
}

interface AggregateResult {
  data: Array<ChartData>
  seriesValue: string
  yValue: string
}

/**
 * 将数据按 series 字段聚合为 Top N + "其他"
 * @param otherLabel 合并后的标签名
 */
export function aggregateToTopN(
  data: Array<ChartData>,
  seriesField: string,
  yField: string,
  topN = 9,
  otherLabel = '其他'
): AggregateResult {
  // 按 series 汇总
  const sumMap: Record<string, number> = {}
  for (const d of data) {
    const key = d[seriesField] == null ? '' : String(d[seriesField])
    const val = Number(d[yField]) || 0
    sumMap[key] = (sumMap[key] || 0) + val
  }

  const entries = Object.entries(sumMap).sort((a, b) => b[1] - a[1])
  const topEntries = entries.slice(0, topN)
  const otherEntries = entries.slice(topN)

  const result: Array<ChartData> = topEntries.map(([key, value]) => ({
    [seriesField]: key,
    [yField]: value,
  }))

  if (otherEntries.length > 0) {
    const otherSum = otherEntries.reduce((sum, [, value]) => sum + value, 0)
    result.push({
      [seriesField]: otherLabel,
      [yField]: otherSum,
    })
  }

  return { data: result, seriesValue: seriesField, yValue: yField }
}

const INTENT_PATTERNS: Record<string, RegExp[]> = {
  trend: [/趋势|走势|变化|随时间|增长|下降|time\s+trend|over\s+time|trend/i],
  comparison: [/对比|比较|排名|各平台|各店铺|各品类|差异|vs|top|compare|rank/i],
  proportion: [/占比|份额|构成|百分比|percent\s+of\s+total|proportion|share/i],
  detail: [/明细|详情|列表|具体数据|raw\s+data|detail|table/i],
}

export type UserIntent = 'trend' | 'comparison' | 'proportion' | 'detail' | 'unknown'

/**
 * 根据用户问题文本推断可视化意图。
 */
export function inferUserIntent(question: string | undefined | null): UserIntent {
  if (!question) return 'unknown'
  const text = String(question)
  for (const [intent, patterns] of Object.entries(INTENT_PATTERNS)) {
    if (patterns.some((p) => p.test(text))) {
      return intent as UserIntent
    }
  }
  return 'unknown'
}

/**
 * 结合用户问题意图与数据特征推荐图表类型。
 */
export function recommendChartType(
  question: string | undefined | null,
  data: Array<ChartData>,
  columns: Array<{ name: string; value: string }>
): 'table' | 'column' | 'bar' | 'line' | 'pie' | null {
  if (!data || data.length === 0 || !columns || columns.length === 0) return null

  const intent = inferUserIntent(question)

  // 分析列特征
  const SAMPLE_SIZE = 20
  const sampleRows = data.slice(0, SAMPLE_SIZE)
  const colTypes = columns.map((col) => {
    const vals = sampleRows
      .map((r) => r[col.value])
      .filter((v) => v !== null && v !== undefined && v !== '')
    const numericCount = vals.filter((v) =>
      !isNaN(Number(String(v).replace(/[,%]/g, '').trim()))
    ).length
    const isNumeric = vals.length > 0 && numericCount / vals.length >= 0.8
    const isTime = isTimeField(data, col.value)
    const uniqueVals = new Set(data.slice(0, 100).map((r) => String(r[col.value] ?? '')))
    return { col, isNumeric, isTime, uniqueCount: uniqueVals.size }
  })

  const numericCols = colTypes.filter((c) => c.isNumeric)
  const timeCols = colTypes.filter((c) => c.isTime)
  const categoricalCols = colTypes.filter((c) => !c.isNumeric && !c.isTime)

  // 明细优先
  if (intent === 'detail') return 'table'

  // 占比意图：且恰好 1 个分类 + 1 个数值，数值不是百分比率
  if (intent === 'proportion') {
    if (numericCols.length >= 1 && categoricalCols.length >= 1) {
      const yCol = numericCols[0].col
      const isRateCol = /率|%|percent|ratio/i.test(yCol.name)
      if (!isRateCol) return 'pie'
    }
    return 'column'
  }

  // 趋势意图：有时间列 + 数值列 → 折线
  if (intent === 'trend') {
    if (timeCols.length > 0 && numericCols.length > 0) return 'line'
    if (categoricalCols.length > 0 && numericCols.length > 0) return 'column'
  }

  // 对比意图：分类 + 数值 → 柱/条
  if (intent === 'comparison') {
    if (categoricalCols.length > 0 && numericCols.length > 0) {
      const catCol = categoricalCols[0]
      // 类别过多或标签较长时用条形图
      if (catCol.uniqueCount > 8 || catCol.col.name.length > 8) return 'bar'
      return 'column'
    }
  }

  // 无明确意图时按数据特征兜底
  if (timeCols.length > 0 && numericCols.length > 0) return 'line'
  if (categoricalCols.length > 0 && numericCols.length > 0) {
    const catCol = categoricalCols[0]
    if (catCol.uniqueCount > 8 || catCol.col.name.length > 8) return 'bar'
    return 'column'
  }

  // 多列无明确数值 → 表格
  return 'table'
}

interface ColumnTypeInfo {
  col: { name: string; value: string }
  isNumeric: boolean
  isTime: boolean
  uniqueCount: number
}

function analyzeColumnTypes(
  data: Array<ChartData>,
  columns: Array<{ name: string; value: string }>
): ColumnTypeInfo[] {
  const SAMPLE_SIZE = 20
  const sampleRows = data.slice(0, SAMPLE_SIZE)
  return columns.map((col) => {
    const vals = sampleRows
      .map((r) => r[col.value])
      .filter((v) => v !== null && v !== undefined && v !== '')
    const numericCount = vals.filter((v) =>
      !isNaN(Number(String(v).replace(/[,%]/g, '').trim()))
    ).length
    const isNumeric = vals.length > 0 && numericCount / vals.length >= 0.8
    const isTime = isTimeField(data, col.value)
    const uniqueVals = new Set(data.slice(0, 100).map((r) => String(r[col.value] ?? '')))
    return { col, isNumeric, isTime, uniqueCount: uniqueVals.size }
  })
}

// ═══════════════════════════════════════════════════════════════════════════════
//  通道语义模型：dimension / metric / temporal / ordinal / categorical
// ═══════════════════════════════════════════════════════════════════════════════

export type ColumnChannel = 'temporal' | 'ordinal' | 'categorical' | 'metric' | 'unknown'

export interface ColumnChannelMeta {
  name: string
  value: string
  channel: ColumnChannel
  uniqueCount: number
  isRatio: boolean
  hasNegative: boolean
  isNumeric: boolean
}

/**
 * 把单列归类为通道语义。
 * 优先级：时间 > 序数 > 数值(metric) > 分类。
 */
export function classifyColumn(data: Array<ChartData>, field: string): ColumnChannel {
  if (!data || data.length === 0 || !field) return 'unknown'
  if (isTimeField(data, field)) return 'temporal'

  // 先判断数值/分类（数据驱动），再判断序数名称（名称只是辅助信号）。
  // 否则像 "order_count" 这种纯数值字段会被 ORDINAL_NAME_PATTERN 中的 /order/ 误判为序数，
  // 导致 CartesianChartModel 把它当作合法的 x 维度，所有柱子挤在同一位置。
  const sample = data.slice(0, 20)
  const nonEmpty = sample.filter((d) => d[field] !== null && d[field] !== undefined && d[field] !== '')
  if (nonEmpty.length === 0) return 'categorical'

  const numericCount = nonEmpty.filter((d) => {
    const s = String(d[field]).replace(/[,%]/g, '').trim()
    return s !== '' && !isNaN(Number(s))
  }).length
  const isNumeric = numericCount / nonEmpty.length >= 0.8

  if (isNumeric) return 'metric'

  // 非数值字段才检查是否为序数维度（如 "排名"、"阶段" 等有业务顺序含义的文本/编码列）
  if (isOrdinalField(data, field)) return 'ordinal'

  return 'categorical'
}

export function isDimensionChannel(channel: ColumnChannel): boolean {
  return channel === 'temporal' || channel === 'ordinal' || channel === 'categorical'
}

/**
 * 分析所有列的通道类型及辅助元数据。
 */
export function analyzeColumnChannels(
  data: Array<ChartData>,
  columns: Array<{ name: string; value: string }>
): ColumnChannelMeta[] {
  return columns.map((col) => {
    const channel = classifyColumn(data, col.value)
    const uniqueVals = new Set(data.slice(0, 100).map((r) => String(r[col.value] ?? '')))
    const numericValues = data
      .slice(0, 100)
      .map((r) => Number(String(r[col.value]).replace(/[,%]/g, '').trim()))
      .filter((v) => !isNaN(v))

    return {
      name: col.name,
      value: col.value,
      channel,
      uniqueCount: uniqueVals.size,
      isRatio: /率|%|percent|ratio|占比|份额/i.test(col.name),
      hasNegative: numericValues.some((v) => v < 0),
      isNumeric: channel === 'metric',
    }
  })
}

interface ChannelSpec {
  type: 'dimension' | 'metric'
  optional?: boolean
  min?: number
  max?: number
  positive?: boolean
  ratioForbidden?: boolean
  maxCardinality?: number
  forbidden?: ColumnChannel[]
  preferred?: ColumnChannel[]
}

/**
 * 各图表类型的默认通道要求。
 * 后端 chart_registry.py 的 required_channels 为 SSOT；此处作为 API 不可用时的兜底。
 */
const DEFAULT_REQUIRED_CHANNELS: Record<string, Record<string, ChannelSpec>> = {
  column: {
    y: { type: 'metric' },
    x: { type: 'dimension', forbidden: ['temporal'], maxCardinality: 60 },
    color: { type: 'dimension', optional: true, maxCardinality: 10 },
  },
  bar: {
    y: { type: 'metric' },
    x: { type: 'dimension', forbidden: ['temporal'], maxCardinality: 60 },
    color: { type: 'dimension', optional: true, maxCardinality: 10 },
  },
  line: {
    y: { type: 'metric' },
    x: { type: 'dimension', preferred: ['temporal', 'ordinal'], maxCardinality: 500 },
    color: { type: 'dimension', optional: true, maxCardinality: 20 },
  },
  pie: {
    color: { type: 'dimension', maxCardinality: 10 },
    theta: { type: 'metric', positive: true, ratioForbidden: true },
  },
}

const CHANNEL_PRIORITY = ['y', 'theta', 'x', 'color']

function channelPriority(name: string): number {
  const idx = CHANNEL_PRIORITY.indexOf(name)
  return idx >= 0 ? idx : 999
}

/**
 * 按问题文本给列名打分，用于在同等条件下优先选择用户提到的列。
 */
function scoreColumnsByQuestion(
  columns: Array<{ name: string; value: string }>,
  question: string | undefined | null
): Map<string, number> {
  const scores = new Map<string, number>()
  if (!question) return scores
  const q = question.toLowerCase()
  for (const col of columns) {
    if (!col.name) continue
    const name = col.name.toLowerCase()
    if (name.length >= 2 && q.includes(name)) {
      scores.set(col.value, 2)
    }
  }
  return scores
}

function filterCandidatesBySpec(
  metaList: ColumnChannelMeta[],
  spec: ChannelSpec,
  qScores: Map<string, number>
): ColumnChannelMeta[] {
  let pool = metaList.filter((m) => {
    if (spec.type === 'metric') return m.isNumeric
    return isDimensionChannel(m.channel)
  })

  if (spec.forbidden && spec.forbidden.length > 0) {
    pool = pool.filter((m) => !spec.forbidden!.includes(m.channel))
  }
  if (spec.positive) {
    pool = pool.filter((m) => !m.hasNegative)
  }
  if (spec.ratioForbidden) {
    pool = pool.filter((m) => !m.isRatio)
  }
  if (spec.maxCardinality && spec.maxCardinality > 0) {
    pool = pool.filter((m) => m.uniqueCount <= spec.maxCardinality!)
  }

  // 优先 preferred 通道类型，再按问题提及分排序
  if (spec.preferred && spec.preferred.length > 0) {
    const preferredOrder = spec.preferred
    pool.sort((a, b) => {
      const ai = preferredOrder.indexOf(a.channel)
      const bi = preferredOrder.indexOf(b.channel)
      const aPref = ai >= 0 ? 100 - ai : -1
      const bPref = bi >= 0 ? 100 - bi : -1
      if (aPref !== bPref) return bPref - aPref
      return (qScores.get(b.value) || 0) - (qScores.get(a.value) || 0)
    })
  } else {
    pool.sort((a, b) => (qScores.get(b.value) || 0) - (qScores.get(a.value) || 0))
  }

  return pool
}

function normalizeRequiredChannels(input?: Record<string, any>): Record<string, ChannelSpec> {
  if (!input) return {}
  const result: Record<string, ChannelSpec> = {}
  for (const [key, spec] of Object.entries(input)) {
    if (!spec || typeof spec !== 'object') continue
    result[key] = {
      type: spec.type || 'dimension',
      optional: !!spec.optional,
      min: spec.min,
      max: spec.max,
      positive: !!spec.positive,
      ratioForbidden: !!spec.ratioForbidden || !!spec.ratio_forbidden,
      maxCardinality: spec.maxCardinality || spec.max_cardinality,
      forbidden: spec.forbidden,
      preferred: spec.preferred,
    }
  }
  return result
}

/**
 * 结合用户问题意图与数据特征，为指定图表类型推荐 X/Y/Series 字段。
 *
 * 核心变化：基于图表类型的 required_channels 做通道映射，不再硬编码 X/Y/series。
 * 返回字段的 value（非 name），供 DisplayChartBlock 做最终轴映射。
 */
export function recommendAxes(
  question: string | undefined | null,
  chartType: ChartTypes,
  data: Array<ChartData>,
  columns: Array<{ name: string; value: string }>,
  requiredChannels?: Record<string, any>
): { x?: string; y?: string; series?: string } {
  if (!data || data.length === 0 || !columns || columns.length === 0) return {}
  if (chartType === 'table') return {}

  const channels = normalizeRequiredChannels(
    requiredChannels || DEFAULT_REQUIRED_CHANNELS[chartType] || {}
  )
  if (Object.keys(channels).length === 0) return {}

  const colMeta = analyzeColumnChannels(data, columns)
  const qScores = scoreColumnsByQuestion(columns, question)

  const picks: Record<string, string> = {}
  const usedValues = new Set<string>()

  const sortedEntries = Object.entries(channels).sort(
    (a, b) => channelPriority(a[0]) - channelPriority(b[0])
  )

  for (const [name, spec] of sortedEntries) {
    const pool = filterCandidatesBySpec(colMeta, spec, qScores).filter(
      (m) => !usedValues.has(m.value)
    )
    if (pool.length > 0) {
      picks[name] = pool[0].value
      usedValues.add(pool[0].value)
    } else if (!spec.optional) {
      // 必填通道无候选：尝试从全部候选中挑一个最接近的（保证至少能渲染）
      const fallback = filterCandidatesBySpec(colMeta, { ...spec, forbidden: undefined, positive: false, ratioForbidden: false }, qScores).filter(
        (m) => !usedValues.has(m.value)
      )[0]
      if (fallback) {
        picks[name] = fallback.value
        usedValues.add(fallback.value)
      }
    }
  }

  const result: { x?: string; y?: string; series?: string } = {}
  if (picks.x) result.x = picks.x
  if (picks.y) result.y = picks.y
  if (picks.theta) result.y = picks.theta
  if (picks.color) result.series = picks.color
  // pie 的 color 通道同时充当分类维度，x 与 series 保持一致
  if (chartType === 'pie' && picks.color && !result.x) result.x = picks.color

  console.debug('[recommendAxes]', { chartType, requiredChannels: channels, picks, result })
  return result
}

/**
 * 校验目标图表类型是否能被当前数据满足。
 * 用于类型切换前的兼容性检查或自动降级提示。
 */
export function validateChartTypeChannels(
  chartType: ChartTypes,
  data: Array<ChartData>,
  columns: Array<{ name: string; value: string }>,
  requiredChannels?: Record<string, any>
): { ok: boolean; reason?: string } {
  if (chartType === 'table') return { ok: true }
  const channels = normalizeRequiredChannels(
    requiredChannels || DEFAULT_REQUIRED_CHANNELS[chartType] || {}
  )
  if (Object.keys(channels).length === 0) return { ok: true }

  const colMeta = analyzeColumnChannels(data, columns)
  const emptyScores = new Map<string, number>()

  for (const [name, spec] of Object.entries(channels)) {
    if (spec.optional) continue
    const pool = filterCandidatesBySpec(colMeta, spec, emptyScores)
    if (pool.length === 0) {
      console.debug('[validateChartTypeChannels] 通道无候选', {
        chartType,
        channelName: name,
        spec,
        colMeta,
      })
      return { ok: false, reason: `当前数据缺少适合 ${chartType} 的 ${name} 通道字段` }
    }
  }

  // pie 额外校验：Y 字段不能是比率或含负值
  if (chartType === 'pie') {
    const recommended = recommendAxes('', chartType, data, columns, requiredChannels)
    const yMeta = colMeta.find((m) => m.value === recommended.y)
    if (yMeta?.isRatio) {
      return { ok: false, reason: '饼图不适合展示比率/百分比字段，已自动切换到柱状图' }
    }
    if (yMeta?.hasNegative) {
      return { ok: false, reason: '饼图角度通道不支持负值，已自动切换到柱状图' }
    }
  }

  return { ok: true }
}

export function checkIsPercent(valueAxes: Array<ChartAxis>, data: Array<ChartData>): CheckedData {
  const result: CheckedData = {
    isPercent: false,
    data: [],
  }

  // 深拷贝原始数据
  for (let i = 0; i < data.length; i++) {
    result.data.push({ ...data[i] })
  }

  // 检查是否有任何一个轴包含百分比数据
  for (const valueAxis of valueAxes) {
    const notEmptyData = filter(
      data,
      (d) =>
        d &&
        d[valueAxis.value] !== null &&
        d[valueAxis.value] !== undefined &&
        d[valueAxis.value] !== '' &&
        d[valueAxis.value] !== 0 &&
        d[valueAxis.value] !== '0'
    )

    if (notEmptyData.length > 0) {
      const v = notEmptyData[0][valueAxis.value] + ''
      if (endsWith(v.trim(), '%')) {
        result.isPercent = true
        break // 找到一个百分比轴就结束检查
      }
    }
  }

  // 如果发现任何百分比轴，处理所有轴的所有百分比数据
  if (result.isPercent) {
    for (let i = 0; i < data.length; i++) {
      for (const valueAxis of valueAxes) {
        const value = data[i][valueAxis.value]
        if (value !== null && value !== undefined && value !== '') {
          const strValue = String(value).trim()
          if (endsWith(strValue, '%')) {
            const formatValue = replace(strValue, '%', '')
            const numValue = Number(formatValue)
            result.data[i][valueAxis.value] = isNaN(numValue) ? 0 : numValue
          }
        }
      }
    }
  }

  return result
}
