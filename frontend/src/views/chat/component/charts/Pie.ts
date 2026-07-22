import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart.ts'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart.ts'
import type { G2Spec } from '@antv/g2'
import {
  aggregateToTopN,
  checkIsPercent,
  fallbackXYAxes,
  formatNumber,
  getAxesWithFilter,
  isCategoricalField,
  isTimeField,
} from '@/views/chat/component/charts/utils.ts'

export class Pie extends BaseG2Chart {
  /** 标签格式: name_value | name_percent | value_percent */
  labelFormat: 'name_value' | 'name_percent' | 'value_percent' = 'name_value'

  constructor(id: string) {
    super(id, 'pie')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    console.log(`[Pie] init START | axisLen=${axis?.length} | dataLen=${data?.length}`)
    super.init(axis, data)
    let { y, series } = getAxesWithFilter(this.axis)
    console.log(`[Pie] getAxesWithFilter → y=${y.length} series=${series.length}`)

    if (y.length == 0) {
      const fallback = fallbackXYAxes(this.axis, data)
      if (fallback.y) y = [{ ...fallback.y, type: 'y' }]
      console.log(`[Pie] fallbackXYAxes → y=${y.length}`)
    }

    if (y.length == 0) {
      console.warn('[Pie] init skipped: no y axis')
      this._initOk = false
      return
    }

    // 比率字段用饼图展示可能产生误导（扇区大小≠整体占比），但允许用户自主决定
    const yName = y[0].name
    const yValue = y[0].value
    if (/率|%|percent|ratio|占比|份额|百分比/i.test(yName)) {
      console.warn(
        `[Pie] 注意：Y 轴 “${yName}” 是比率/百分比字段，` +
        `饼图的扇区大小表示绝对占比（非该比率的占比），请注意区分。` +
        `如果这不是您想要的，建议切换到 column 或 bar。`
      )
      // 不阻止渲染，让用户自行判断
    }

    // 饼图必须 series；按优先级兜底：x 轴 > 非数值非时间 > 非数值 > 任意非 y 轴
    if (series.length == 0) {
      const candidates = [
        this.axis.find((a) => a.type === 'x'),
        this.axis.find((a) => a.type !== 'y' && a.type !== 'series' && !isTimeField(data, a.value)),
        this.axis.find((a) => a.type !== 'y' && a.type !== 'series'),
        this.axis.find((a) => a.value !== yValue),
      ]
      const fallback = candidates.find(Boolean)
      if (fallback) {
        series = [{ ...fallback, type: 'series' }]
      }
    }

    if (series.length == 0) {
      console.warn('[Pie] init skipped: no series axis')
      this._initOk = false
      return
    }

    let _data = checkIsPercent(y, data)

    const seriesValue = series[0].value
    const uniqueSeries = new Set(_data.data.map((d) => String(d[seriesValue] ?? '')))

    // 饼图角度通道不支持负数：过滤负值并警告
    const negativeCount = _data.data.filter((d) => Number(d[yValue]) < 0).length
    if (negativeCount > 0) {
      console.warn(`[Pie] 过滤 ${negativeCount} 条负值数据，角度通道不支持负数`)
      _data.data = _data.data.filter((d) => Number(d[yValue]) >= 0)
    }

    if (uniqueSeries.size === 0 || _data.data.length === 0) {
      console.warn('[Pie] init skipped: no valid data')
      this._initOk = false
      return
    }

    if (uniqueSeries.size === 1) {
      console.warn(`[Pie] 数据仅 1 个分类 "${Array.from(uniqueSeries)[0]}"，渲染单扇区饼图`)
    }

    // 按 Y 值排序（对副本操作，不污染原始 this.data）
    if (this._sortOrder !== 'none') {
      _data.data.sort((a, b) => {
        const va = Number(a[yValue]) || 0
        const vb = Number(b[yValue]) || 0
        return this._sortOrder === 'asc' ? va - vb : vb - va
      })
    }

    if (uniqueSeries.size > 10) {
      const aggregated = aggregateToTopN(_data.data, seriesValue, yValue, 9, '其他')
      _data.data = aggregated.data
    }

    console.debug({ 'render-info': { y: y, series: series, data: _data }, instance: this })

    const isPercent = _data.isPercent
    const labelFormat = this.labelFormat
    const numberFmt = this.numberFormat

    const buildLabelText = (d: any) => {
      const name = d[seriesValue]
      const value = Number(d[yValue]) || 0
      const total = _data.data.reduce((sum, item) => sum + (Number(item[yValue]) || 0), 0)
      const percent = total > 0 ? ((value / total) * 100).toFixed(2) + '%' : '0%'
      const valueStr = `${formatNumber(value, numberFmt)}${isPercent ? '%' : ''}`

      switch (labelFormat) {
        case 'name_percent': return `${name}: ${percent}`
        case 'value_percent': return `${valueStr} (${percent})`
        case 'name_value':
        default: return `${name}: ${valueStr}`
      }
    }

    const options: G2Spec = {
      type: 'interval',
      coordinate: { type: 'theta', outerRadius: 0.8 },
      transform: [{ type: 'stackY' }],
      data: _data.data,
      interaction: {
        legendFilter: true,  // ← 点击图例切换扇区可见性
      },
      encode: {
        y: yValue,
        color: seriesValue,
      },
      scale: {
        x: {
          nice: true,
        },
        y: {
          type: 'linear',
        },
      },
      legend: {
        color: { position: 'bottom', layout: { justifyContent: 'center' } },
      },
      animate: { enter: { type: 'waveIn' } },
      labels: this.showLabel
        ? [
            {
              position: 'spider',
              text: (data: any) => buildLabelText(data),
            },
          ]
        : [],
      tooltip: {
        title: (data: any) => data[seriesValue],
        items: [
          (data: any) => {
            return {
              name: yName,
              value: `${formatNumber(data[yValue], numberFmt)}${isPercent ? '%' : ''}`,
            }
          },
        ],
      },
    }

    this._applySettingsToOptions(options, this._activeSettings || {})

    this.chart.options(options)
    this._initOk = true
    console.log(`[Pie] init DONE | y=${yValue} series=${seriesValue} dataLen=${_data.data.length}`)
  }

  protected _applyTypeSettings(settings: Record<string, any>): void {
    if (settings.show_label !== undefined) {
      this.showLabel = settings.show_label
    }
    if (settings.label_format !== undefined) {
      const validFormats = ['name_value', 'name_percent', 'value_percent']
      this.labelFormat = validFormats.includes(settings.label_format)
        ? settings.label_format
        : 'name_value'
    }
    super._applyTypeSettings(settings)
  }
}
