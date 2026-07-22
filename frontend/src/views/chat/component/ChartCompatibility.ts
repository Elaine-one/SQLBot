/**
 * ChartCompatibility — 对标 Metabase getSensibleDisplays + isSensible
 *
 * 三层设计：
 *   Layer 1 (已实现): buildCartesianModel / recommendAxes → 尽量适配数据
 *   Layer 2 (已实现): checkRenderable → 不能渲染的给兜底提示
 *   Layer 3 (本文件): scoreChartCompatibility → 按兼容性评分排列图表类型
 *
 * 评分规则：
 *   +50  基础：至少有 1 指标 + 1 维度
 *   +20  折线图有 temporal/ordinal x 轴
 *   +15  柱/条 x 轴不是 temporal
 *   +10  饼图指标为正数非比率
 *   +5   饼图分类 ≤10
 *   -20  缺少必需通道
 *   -10  分类基数过高
 */

import type { ChartData } from '@/views/chat/component/BaseChart'
import type { ChartTypes } from '@/views/chat/component/BaseChart'
import {
  classifyColumn,
  isDimensionChannel,
  isTimeField,
} from '@/views/chat/component/charts/utils'

export interface CompatibilityScore {
  type: ChartTypes
  score: number       // 0-100，越高越适配
  label: string       // 中文名
  reasons: string[]   // 为什么适配 / 不适配
  isCompatible: boolean // 是否能渲染（score > 0）
}

const TYPE_LABELS: Record<string, string> = {
  table: '明细表', column: '柱状图', bar: '条形图', line: '折线图', pie: '饼图',
}

const ALL_TYPES: ChartTypes[] = ['table', 'column', 'bar', 'line', 'pie']

export function scoreChartCompatibility(
  data: ChartData[],
  columns: Array<{ name: string; value: string }>,
): CompatibilityScore[] {
  if (!data || data.length === 0 || !columns || columns.length === 0) {
    return ALL_TYPES.map((t) => ({
      type: t, score: t === 'table' ? 50 : 0,
      label: TYPE_LABELS[t] || t,
      reasons: t === 'table' ? ['明细表不依赖数据形状'] : ['数据为空'],
      isCompatible: t === 'table',
    }))
  }

  // 分析列特征
  const colMeta = columns.map((c) => ({
    name: c.name,
    value: c.value,
    channel: classifyColumn(data, c.value),
    uniqueCount: new Set(data.map((d) => String(d[c.value] ?? ''))).size,
    isRatio: /率|%|percent|ratio|占比|份额|百分比/i.test(c.name),
    hasNegative: data.slice(0, 50).some(
      (d) => Number(d[c.value]) < 0
    ),
    isNumeric: false, // 由 classifyColumn 决定
  }))
  colMeta.forEach((m) => { m.isNumeric = m.channel === 'metric' })

  const dimensions = colMeta.filter((m) => isDimensionChannel(m.channel))
  const metrics = colMeta.filter((m) => m.channel === 'metric')
  const temporalCols = colMeta.filter((m) => m.channel === 'temporal')
  const ordinalCols = colMeta.filter((m) => m.channel === 'ordinal')

  const results: CompatibilityScore[] = []

  for (const type of ALL_TYPES) {
    let score = 0
    const reasons: string[] = []

    // ── table 总是 100 分 ──
    if (type === 'table') {
      score = 100
      reasons.push('明细表兼容所有数据')
      results.push({ type, score, label: TYPE_LABELS[type], reasons, isCompatible: true })
      continue
    }

    // ── 基础检查 ──
    if (dimensions.length === 0) {
      reasons.push('缺少维度字段（分类/时间）')
      score -= 20
    } else {
      score += 30
    }
    if (metrics.length === 0) {
      reasons.push('缺少数值指标字段')
      score -= 20
    } else {
      score += 30
    }

    // ── 类型特定评分 ──
    if (type === 'line') {
      if (temporalCols.length > 0 || ordinalCols.length > 0) {
        score += 25
        reasons.push('有适合折线图的时间/有序维度')
      } else if (dimensions.length > 0) {
        score += 10
        reasons.push('可以用分类维度但非最优（建议有日期字段）')
      }
      if (dimensions[0]?.uniqueCount > 500) {
        score -= 10
        reasons.push(`X 轴有 ${dimensions[0].uniqueCount} 个唯一值，折线图可能拥挤`)
      }
    }

    if (type === 'column' || type === 'bar') {
      if (temporalCols.length > 0 && dimensions.filter(
        (d) => !temporalCols.find((t) => t.value === d.value)
      ).length === 0) {
        // 只有时间维度，没有非时间维度
        score -= 10
        reasons.push('只有时间维度，柱/条图更适合分类对比（建议有分类字段）')
      } else if (dimensions.length > 0) {
        score += 20
        reasons.push('有适合柱/条图的分类维度')
      }
      const catDim = dimensions.find((d) => d.channel === 'categorical')
      if (catDim && catDim.uniqueCount > 8) {
        score += (type === 'bar' ? 5 : -5)
        if (type === 'bar') {
          reasons.push('分类较多，条形图比柱状图更适合')
        } else {
          reasons.push(`分类有 ${catDim.uniqueCount} 个，建议用条形图`)
        }
      }
    }

    if (type === 'pie') {
      const metric = metrics[0]
      if (metric) {
        if (metric.isRatio) {
          score -= 5
          reasons.push('指标是比率字段，饼图需注意解读')
        } else if (!metric.hasNegative) {
          score += 10
          reasons.push('指标为正数，适合饼图')
        }
        if (metric.hasNegative) {
          score -= 10
          reasons.push('指标含负值，不适合饼图')
        }
      }
      const catDim = dimensions[0]
      if (catDim) {
        if (catDim.uniqueCount >= 2 && catDim.uniqueCount <= 10) {
          score += 10
          reasons.push(`分类 ${catDim.uniqueCount} 个，饼图扇区数合适`)
        } else if (catDim.uniqueCount > 10) {
          score -= 15
          reasons.push(`分类 ${catDim.uniqueCount} 个超过 10，饼图扇区太多`)
        } else if (catDim.uniqueCount < 2) {
          score -= 10
          reasons.push('只有 1 个分类，饼图无意义')
        }
      }
    }

    // ── 确保评分在 0-100 ──
    score = Math.max(0, Math.min(100, score))
    const isCompatible = score >= 20  // 低于 20 分认为基本不可用

    if (reasons.length === 0) {
      reasons.push(isCompatible ? '数据适合此图表类型' : '数据不太适合此图表类型')
    }

    results.push({ type, score, label: TYPE_LABELS[type], reasons, isCompatible })
  }

  // ── 按评分降序排列（对标 Metabase getSensibleDisplays）──
  results.sort((a, b) => b.score - a.score)
  return results
}
