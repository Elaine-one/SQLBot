import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart.ts'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart.ts'
import type { G2Spec } from '@antv/g2'
import {
  buildAxisTitle,
  checkIsPercent,
  fallbackXYAxes,
  formatNumber,
  getAxesWithFilter,
  inferAxisUnit,
  isTimeField,
  processMultiQuotaData,
  sortDataByTimeField,
} from '@/views/chat/component/charts/utils.ts'

export class Line extends BaseG2Chart {
  constructor(id: string) {
    super(id, 'line')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    super.init(axis, data)

    const numberFmt = this.numberFormat
    const axes = getAxesWithFilter(this.axis)

    if (axes.x.length == 0 || axes.y.length == 0) {
      // axis 类型缺失时的安全兜底（常见于 AI 选 table 后手动切换到可视化图表）
      const fallback = fallbackXYAxes(this.axis, data)
      if (fallback.x) axes.x = [{ ...fallback.x, type: 'x' }]
      if (fallback.y) axes.y = [{ ...fallback.y, type: 'y' }]
    }

    if (axes.x.length == 0 || axes.y.length == 0) {
      console.warn('[Line] init skipped: no x/y axis', { axis: this.axis, axes })
      this._initOk = false
      return
    }

    let config = {
      data: data,
      y: axes.y,
      series: axes.series,
    }
    if (axes.multiQuota.length > 0) {
      config = processMultiQuotaData(
        axes.x,
        config.y,
        axes.multiQuota,
        axes.multiQuotaName,
        config.data
      )
    }

    const x = axes.x
    const y = config.y
    const series = config.series

    const _data = checkIsPercent(y, config.data)

    // 若 X 轴是时间维度，按时间升序排序，避免折线在未排序的时间点间乱穿
    const xField = x[0].value
    const xIsTime = isTimeField(_data.data, xField)
    if (xIsTime) {
      _data.data = sortDataByTimeField(_data.data, xField)
    }

    // 非时间维度下，按 Y 值排序（对副本操作，不污染原始 this.data）
    if (!xIsTime && this._sortOrder !== 'none') {
      const yField = y[0].value
      _data.data.sort((a, b) => {
        const va = Number(a[yField]) || 0
        const vb = Number(b[yField]) || 0
        return this._sortOrder === 'asc' ? va - vb : vb - va
      })
    }

    console.debug({ 'render-info': { x: x, y: y, series: series, data: _data }, instance: this })

    const options: G2Spec = {
      type: 'view',
      data: _data.data,
      encode: {
        x: x[0].value,
        y: y[0].value,
        color: series.length > 0 ? series[0].value : undefined,
      },
      axis: {
        x: {
          title: { text: x[0].name || x[0].value },
          labelFontSize: 12,
          labelAutoHide: xIsTime
            ? { type: 'hide', keepHeader: true, keepTail: true }
            : true,
          labelAutoRotate: !xIsTime,
          labelAutoWrap: true,
          labelAutoEllipsis: true,
        },
        y: {
          title: { text: buildAxisTitle(y[0].name, inferAxisUnit(y[0].name)) },
          labelFormatter: (value: any) => {
            return String(formatNumber(value, numberFmt))
          },
        },
      },
      scale: {
        x: {
          nice: true,
        },
        y: {
          nice: true,
          type: 'linear',
        },
      },
      children: [
        {
          // 面积层：默认完全透明，show_area=true 时通过 applier 调整透明度
          type: 'area',
          style: {
            opacity: 0,
          },
          tooltip: false,
        },
        {
          type: 'line',
          labels: this.showLabel
            ? [
                {
                  text: (data: any) => {
                    const value = data[y[0].value]
                    if (value === undefined || value === null) {
                      return ''
                    }
                    return `${formatNumber(value, numberFmt)}${_data.isPercent ? '%' : ''}`
                  },
                  style: {
                    dx: -10,
                    dy: -12,
                  },
                  transform: [
                    { type: 'contrastReverse' },
                    { type: 'exceedAdjust' },
                    { type: 'overlapHide' },
                  ],
                },
              ]
            : [],
          // G2 v5.3.3 mark-level tooltip 不支持 function callback。
          // 设 false 禁用 mark 级别 tooltip，由 view 层默认 tooltip 交互统一处理。
          tooltip: false,
        },
        {
          // 数据点层：默认隐藏，show_points=true 时通过 applier 显示
          type: 'point',
          style: {
            fill: 'white',
            opacity: 0,
          },
          encode: {
            size: 2.5,
          },
          tooltip: false,
        },
      ],
    } as G2Spec

    this._applySettingsToOptions(options, this._activeSettings || {})

    this.chart.options(options)
    this._initOk = true
  }

  protected _applyTypeSettings(settings: Record<string, any>): void {
    if (settings.show_label !== undefined) {
      this.showLabel = settings.show_label
    }
    super._applyTypeSettings(settings)
  }
}
