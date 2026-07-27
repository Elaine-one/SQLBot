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
  processMultiQuotaData,
} from '@/views/chat/component/charts/utils.ts'

export class Column extends BaseG2Chart {
  constructor(id: string) {
    super(id, 'column')
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    super.init(axis, data)

    const numberFmt = this.numberFormat
    const axes = getAxesWithFilter(this.axis)

    if (axes.x.length == 0 || axes.y.length == 0) {
      const fallback = fallbackXYAxes(this.axis, data)
      if (fallback.x) axes.x = [{ ...fallback.x, type: 'x' }]
      if (fallback.y) axes.y = [{ ...fallback.y, type: 'y' }]
    }

    if (axes.x.length == 0 || axes.y.length == 0) {
      console.warn('[Column] init skipped: no x/y axis', { axis: this.axis, axes })
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

    // 按 Y 值排序（对副本操作，不污染原始 this.data）
    if (this._sortOrder !== 'none') {
      const yField = y[0].value
      _data.data.sort((a, b) => {
        const va = Number(a[yField]) || 0
        const vb = Number(b[yField]) || 0
        return this._sortOrder === 'asc' ? va - vb : vb - va
      })
    }

    console.debug({ 'render-info': { x: x, y: y, series: series, data: _data }, instance: this })

    const options: G2Spec = {
      type: 'interval',
      data: _data.data,
      encode: {
        x: x[0].value,
        y: y[0].value,
        color: series.length > 0 ? series[0].value : undefined,
      },
      style: {
        radiusTopLeft: (d: ChartData) => {
          if (d[y[0].value] && d[y[0].value] > 0) {
            return 4
          }
          return 0
        },
        radiusTopRight: (d: ChartData) => {
          if (d[y[0].value] && d[y[0].value] > 0) {
            return 4
          }
          return 0
        },
        radiusBottomLeft: (d: ChartData) => {
          if (d[y[0].value] && d[y[0].value] < 0) {
            return 4
          }
          return 0
        },
        radiusBottomRight: (d: ChartData) => {
          if (d[y[0].value] && d[y[0].value] < 0) {
            return 4
          }
          return 0
        },
      },
      axis: {
        x: {
          title: { text: x[0].name },
          labelFontSize: 12,
          labelAutoHide: {
            type: 'hide',
            keepHeader: true,
            keepTail: true,
          },
          labelAutoRotate: false,
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
      interaction: {
        elementHighlight: { background: true, region: true },
        tooltip: { series: series.length > 0, shared: true },
      },
      tooltip: (data: any) => {
        if (series.length > 0) {
          return {
            name: data[series[0].value],
            value: `${formatNumber(data[y[0].value], numberFmt)}${_data.isPercent ? '%' : ''}`,
          }
        } else {
          return {
            name: y[0].name,
            value: `${formatNumber(data[y[0].value], numberFmt)}${_data.isPercent ? '%' : ''}`,
          }
        }
      },
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
              position: (data: any) => {
                if (data[y[0].value] < 0) {
                  return 'bottom'
                }
                return 'top'
              },
              transform: [
                { type: 'contrastReverse' },
                { type: 'exceedAdjust' },
                { type: 'overlapHide' },
              ],
            },
          ]
        : [],
    } as G2Spec

    // 注入 settings（统一入口，无外部 chart.options 调用）
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