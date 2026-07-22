/**
 * CartesianChart — 统一笛卡尔图表渲染类
 *
 * 对标 Metabase 的 CartesianChart 组件。
 * 一个类代替 Column.ts / Bar.ts / Line.ts 三个独立的类。
 * chartType 从构造函数注入，切换类型不需要销毁重建实例。
 *
 * 管道:
 *   settings + data → CartesianChartModel → toG2Option() → chart.options() → render()
 */

import { BaseG2Chart } from '@/views/chat/component/BaseG2Chart'
import type { ChartAxis, ChartData } from '@/views/chat/component/BaseChart'
import type { G2Spec } from '@antv/g2'
import {
  buildAxisTitle,
  formatNumber,
  inferAxisUnit,
} from '@/views/chat/component/charts/utils'
import {
  buildCartesianModel,
  type CartesianChartConfig,
} from '@/views/chat/component/CartesianChartModel'

// ═══════════════════════════════════════════════════════════════════════════════

const COLOR_PALETTES: Record<string, string[]> = {
  default: ['#5B8FF9', '#5AD8A6', '#5D7092', '#F6BD16', '#E8684A', '#6DC8EC', '#9270CA', '#FF9D4D', '#269A99', '#FF99C3'],
  warm: ['#F6BD16', '#E8684A', '#FF9D4D', '#F08BB4', '#D580FF', '#FFB366', '#FF6B6B', '#FFD93D', '#FF8C42', '#E84855'],
  cool: ['#5B8FF9', '#5AD8A6', '#5D7092', '#36B4C6', '#3299FE', '#4ECDC4', '#2E86AB', '#6CB4EE', '#7EC8E3', '#A0D2DB'],
  business: ['#1B3A5C', '#2E6B8F', '#47A1C7', '#6CBDDB', '#A8D8EA', '#0D2137', '#3485A9', '#59B0C9', '#8CCFE8', '#C2E5F2'],
}

export class CartesianChart extends BaseG2Chart {
  /** 当前图表类型: 'column' | 'bar' | 'line' */
  declare _name: string

  /** 最近一次 buildModel 的结果 */
  private _model: CartesianChartConfig | null = null

  /** 当前 settings（applySettings 传入） */
  protected _activeSettings: Record<string, any> = {}

  /** model 上次使用的 _name，用于判断是否需要重建 model（类型切换） */
  private _modelChartType: string | null = null

  constructor(id: string, chartType: string) {
    super(id, chartType)
    this._name = chartType
  }

  // ── 唯一对外入口 ──

  applySettings(settings: Record<string, any>): void | Promise<void> {
    this._activeSettings = settings ?? {}
    this._applyTypeSettings(this._activeSettings)

    // 类型变化或数据变化时需要重建 model
    const needRebuild = this._modelChartType !== this._name || !this._model
    if (needRebuild) {
      console.log(`[CartesianChart] building model | type=${this._name} | axisLen=${this.axis?.length} | dataLen=${this.data?.length}`)
      this._model = buildCartesianModel(
        this._name,
        this.axis,
        this.data,
        this._activeSettings,
        this.showLabel,
        this.numberFormat,
        this._sortOrder ?? 'none',
      )
      this._modelChartType = this._name
    } else {
      // 设置变化（非类型/数据变化）：原位更新 model 中的 settings，不重建
      if (this._model) {
        this._model.activeSettings = this._activeSettings
        this._model.showLabel = this.showLabel
        this._model.numberFormat = this.numberFormat
        this._model.sortOrder = this._sortOrder ?? 'none'
        console.log(`[CartesianChart] settings patched to model | showLabel=${this._model.showLabel} grid=${this._activeSettings.grid} smooth=${this._activeSettings.smooth}`)
      }
    }

    if (!this._model) {
      console.warn(`[CartesianChart] model build failed: null | type=${this._name}`)
      this._initOk = false
      return
    }

    console.log(`[CartesianChart] model ready | x=${this._model.x.field}(${this._model.x.type}) y=${this._model.y.field}(${this._model.y.type}) color=${this._model.color?.field ?? 'none'} | dataLen=${this._model.data.length}`)

    this._initOk = true
    const options = this._modelToG2Option(this._model)
    console.log(`[CartesianChart] options built, calling chart.options() + render()`)
    // G2 v5 的 chart.options() 不完全清除 coordinate 变换缓存。
    // column↔bar 切换时，如果不 clear()，旧 coordinate.transpose 会残留。
    this.chart.clear()
    this.chart.options(options)
    return this.render()
  }

  /** 切换图表类型（同实例内切换，不销毁重建） */
  changeChartType(newType: string, settings: Record<string, any>) {
    console.log(`[CartesianChart] changeChartType: ${this._name} → ${newType}`)
    this._name = newType
    // 强制重建 model（chartType 变了，通道要求也变了）
    this._modelChartType = null
    this._model = null
    this.applySettings(settings)
  }

  // ── 类型专属设置 ──

  protected _sortOrder: 'asc' | 'desc' | 'none' = 'none'

  protected _applyTypeSettings(settings: Record<string, any>): void {
    if (settings.show_label !== undefined) {
      this.showLabel = settings.show_label
    }
    if (settings.number_format !== undefined) {
      const validFormats: Array<'full' | 'abbreviated' | 'percent'> = ['full', 'abbreviated', 'percent']
      this.numberFormat = validFormats.includes(settings.number_format) ? settings.number_format : 'abbreviated'
    }
    this._sortOrder = settings.sort && settings.sort !== 'none' ? settings.sort : 'none'
  }

  // ── Model → G2 Option ──

  private _modelToG2Option(model: CartesianChartConfig): G2Spec {
    const { x, y, color, data, isPercent, numberFormat, showLabel, activeSettings } = model
    const hasColor = color !== null
    const isBar = model.type === 'bar'
    const numberFmt = numberFormat

    // encode — column/bar 共享同一套映射（x=维度, y=指标），bar 通过 coordinate.transpose 旋转
    const encode: Record<string, any> = {
      x: x.field,
      y: y.field,
      color: hasColor ? color!.field : undefined,
    }

    // 轴配置
    const xAxisCfg: any = {
      title: { text: x.name },
      labelFontSize: 12,
      labelAutoHide: { type: 'hide', keepHeader: true, keepTail: true },
      labelAutoRotate: false,
      labelAutoWrap: true,
      labelAutoEllipsis: true,
    }

    const yAxisCfg: any = {
      title: { text: buildAxisTitle(y.name, inferAxisUnit(y.name)) },
      labelFormatter: (value: any) => String(formatNumber(value, numberFmt)),
    }

    // ── 柱状图 / 条形图 ──
    if (model.type === 'column' || model.type === 'bar') {
      const base: any = {
        type: 'interval',
        data,
        encode,
        axis: { x: xAxisCfg, y: yAxisCfg },
        scale: { x: { nice: true }, y: { nice: true, type: 'linear' } },
        interaction: {
          elementHighlight: { background: true, region: true },
          legendFilter: true,
          tooltip: { series: hasColor, shared: true },
        },
        tooltip: {
          title: (d: any) => d[x.field],
          items: [
            {
              channel: 'y',
              valueFormatter: (v: any) => `${formatNumber(v, numberFmt)}${isPercent ? '%' : ''}`,
            },
          ],
        },
        labels: showLabel
          ? [
              {
                text: (d: any) => {
                  const v = d[y.field]
                  if (v === undefined || v === null) return ''
                  return `${formatNumber(v, numberFmt)}${isPercent ? '%' : ''}`
                },
                position: isBar ? 'right' : 'top',
                transform: [{ type: 'contrastReverse' }, { type: 'exceedAdjust' }, { type: 'overlapHide' }],
              },
            ]
          : [],
      }

      // 条形图 vs 柱状图：通过 coordinate 区分方向
      // 必须显式设置两种状态，否则 G2 chart.options() 可能保留旧的 transform
      base.coordinate = isBar
        ? { transform: [{ type: 'transpose' }] }
        : {}

      // 柱状图圆角
      if (model.type === 'column') {
        base.style = {
          radiusTopLeft: 4,
          radiusTopRight: 4,
        }
      }

      this._applySettings(base, activeSettings, model.type)
      return base
    }

    // 折线图使用 view + children
    const yField = y.field
    const lineBase: any = {
      type: 'view',
      data,
      encode,
      axis: { x: xAxisCfg, y: yAxisCfg },
      scale: { x: { nice: true }, y: { nice: true, type: 'linear' } },
      interaction: {
        legendFilter: true,  // ← 点击图例切换 series 可见性
        tooltip: { series: hasColor, shared: true },
      },
      children: [
        { type: 'area', style: { opacity: 0 }, tooltip: false },
        {
          type: 'line',
          labels: showLabel
            ? [
                {
                  text: (d: any) => {
                    const v = d[yField]
                    if (v === undefined || v === null) return ''
                    return `${formatNumber(v, numberFmt)}${isPercent ? '%' : ''}`
                  },
                  style: { dx: -10, dy: -12 },
                  transform: [{ type: 'contrastReverse' }, { type: 'exceedAdjust' }, { type: 'overlapHide' }],
                },
              ]
            : [],
          tooltip: false, // G2 v5 mark 级不支持 function callback
        },
        {
          type: 'point',
          style: { fill: 'white', opacity: 0 },
          encode: { size: 2.5 },
          tooltip: false,
        },
      ],
    }

    this._applySettings(lineBase, activeSettings, 'line')
    return lineBase
  }

  /**
   * 对标 Metabase 的 getCartesianChartOption()
   * 将 settings 应用到 G2 options
   */
  private _applySettings(options: any, settings: Record<string, any>, chartName: string): void {
    const hasColor = options.encode?.color !== undefined

    // color_palette
    if (settings.color_palette && COLOR_PALETTES[settings.color_palette]) {
      options.scale = options.scale || {}
      options.scale.color = { ...options.scale.color, range: COLOR_PALETTES[settings.color_palette] }
    }

    // grid
    if (settings.grid !== undefined && options.axis) {
      const gridCfg = settings.grid
        ? { line: { style: { stroke: '#E5E6EB', lineWidth: 1 } } }
        : false
      options.axis.x = { ...options.axis.x, grid: gridCfg }
      options.axis.y = { ...options.axis.y, grid: gridCfg }
    }

    // legend
    if (settings.legend !== undefined) {
      options.legend = settings.legend === false ? false : (options.legend || { color: {} })
    }

    // stack (column/bar only)
    if (chartName !== 'line' && settings.stack !== undefined) {
      options.transform = settings.stack && hasColor ? [{ type: 'stackY' }] : []
    }

    // line-specific
    if (chartName === 'line' && options.children) {
      for (const child of options.children) {
        if (child.type === 'line') {
          if (settings.smooth !== undefined) {
            if (!child.style) child.style = {}
            child.style.interpolate = settings.smooth ? 'monotone' : 'linear'
          }
        }
        if (child.type === 'area') {
          if (settings.show_area !== undefined) {
            if (!child.style) child.style = {}
            child.style.opacity = settings.show_area ? 0.3 : 0
          }
        }
        if (child.type === 'point') {
          if (settings.show_points !== undefined) {
            if (!child.style) child.style = {}
            child.style.opacity = settings.show_points ? 1 : 0
          }
        }
      }
    }

    // y_axis_zero (line only)
    if (chartName === 'line' && settings.y_axis_zero !== undefined) {
      options.scale = options.scale || {}
      options.scale.y = { ...options.scale.y, zero: settings.y_axis_zero === true }
    }
  }

  // ── 渲染 / 销毁 ──

  render(): Promise<void> | undefined {
    if (!this._initOk) return
    return this.chart?.render()?.catch((e: any) => {
      console.warn('[CartesianChart] render rejected:', e?.message || e)
    })
  }

  destroy() {
    this.chart?.destroy()
    this._model = null
    this._modelChartType = null
  }
}
