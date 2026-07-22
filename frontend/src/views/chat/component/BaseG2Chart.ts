import { BaseChart } from '@/views/chat/component/BaseChart.ts'
import { Chart } from '@antv/g2'

const COLOR_PALETTES: Record<string, string[]> = {
  default: ['#5B8FF9', '#5AD8A6', '#5D7092', '#F6BD16', '#E8684A', '#6DC8EC', '#9270CA', '#FF9D4D', '#269A99', '#FF99C3'],
  warm: ['#F6BD16', '#E8684A', '#FF9D4D', '#F08BB4', '#D580FF', '#FFB366', '#FF6B6B', '#FFD93D', '#FF8C42', '#E84855'],
  cool: ['#5B8FF9', '#5AD8A6', '#5D7092', '#36B4C6', '#3299FE', '#4ECDC4', '#2E86AB', '#6CB4EE', '#7EC8E3', '#A0D2DB'],
  business: ['#1B3A5C', '#2E6B8F', '#47A1C7', '#6CBDDB', '#A8D8EA', '#0D2137', '#3485A9', '#59B0C9', '#8CCFE8', '#C2E5F2'],
}

// ═══════════════════════════════════════════════════════════════════════════════
//  声明式设置映射表 — 新增设置只需在此添加一条记录
// ═══════════════════════════════════════════════════════════════════════════════
//
//  每个 SettingApplier 描述:
//    key         — 与 settingsSchema 中的 key 完全对应
//    appliesTo   — 哪些图表类型适用（留空=仅通过 optionGuard 判断）
//    optionGuard— 运行时判断当前 options 是否应该应用此设置
//    apply       — 将 setting 值写入 options 的具体逻辑
//
//  新增图表类型时: 若该类型复用已有设置 key，只需在 appliesTo 中加入 typeId
//  新增设置 key时: 在此表添加一条记录，然后在 settingsSchema 中补齐 schema

interface SettingApplier {
  /** 与 settingsSchema 的 key 完全对应 */
  key: string
  /** 适用图表类型（空数组=仅靠 optionGuard 判断） */
  appliesTo: string[]
  /** 运行时守卫：检查当前 options 结构是否支持此设置 */
  optionGuard: (options: any, chartName: string) => boolean
  /** 将 setting 值写入 options */
  apply: (options: any, value: any, chartName: string) => void
}

const SETTING_APPLIERS: SettingApplier[] = [
  // ── 通用设置 ──
  {
    key: 'color_palette',
    appliesTo: ['column', 'bar', 'line', 'pie'],
    optionGuard: () => true,
    apply: (options, value) => {
      if (!COLOR_PALETTES[value]) return
      options.scale = options.scale || {}
      const existingColorScale = options.scale.color || {}
      // G2 v5 中自定义颜色序列应使用 scale.color.range，而不是 palette
      options.scale.color = Object.assign({}, existingColorScale, {
        range: COLOR_PALETTES[value]
      })
    },
  },
  {
    key: 'show_label',
    appliesTo: ['column', 'bar', 'line', 'pie'],
    optionGuard: () => true,
    // show_label 不在 _applySettingsToOptions 中处理，
    // 而是由子类 _applyTypeSettings 设置 this.showLabel，init() 中读取
    apply: () => { /* handled in _applyTypeSettings */ },
  },
  {
    key: 'legend',
    appliesTo: ['column', 'bar', 'line', 'pie'],
    optionGuard: (options) => !!(
      options.encode?.color ||
      options.legend ||
      options.coordinate?.type === 'theta'
    ),
    apply: (options, value) => {
      if (value === false) {
        options.legend = false
      } else {
        // 保留图表已有的 legend 配置对象（如 pie 的底部居中布局），避免被 true 覆盖为默认布局
        options.legend = typeof options.legend === 'object' && options.legend !== null
          ? options.legend
          : { color: {} }
      }
    },
  },
  {
    key: 'sort',
    appliesTo: ['column', 'bar', 'pie'],
    optionGuard: () => true,
    // sort 由子类 _applyTypeSettings 处理 data.sort()
    apply: () => { /* handled in _applyTypeSettings */ },
  },

  // ── column/bar 专属 ──
  {
    key: 'stack',
    appliesTo: ['column', 'bar'],
    optionGuard: (options) =>
      options.type === 'interval' &&
      options.coordinate?.type !== 'theta' &&
      !!options.encode?.color,
    apply: (options, value) => {
      options.transform = value ? [{ type: 'stackY' }] : []
    },
  },
  {
    key: 'grid',
    appliesTo: ['column', 'bar', 'line'],
    optionGuard: (options) => !!options.axis,
    apply: (options, value) => {
      // G2 v5：axis.x / axis.y 的 grid 支持 boolean 或对象配置；
      // 使用带样式对象可确保网格线可见且风格统一
      options.axis = options.axis || {}
      const gridCfg = value
        ? { line: { style: { stroke: '#E5E6EB', lineWidth: 1, lineDash: [0, 0] } } }
        : false
      options.axis.x = Object.assign({}, options.axis.x, { grid: gridCfg })
      options.axis.y = Object.assign({}, options.axis.y, { grid: gridCfg })
    },
  },

  // ── line 专属 ──
  {
    key: 'smooth',
    appliesTo: ['line'],
    optionGuard: (options) => !!(options.children && options.children.some((c: any) => c.type === 'line')),
    apply: (options, value) => {
      for (const child of options.children) {
        if (child.type === 'line') {
          if (!child.style) child.style = {}
          child.style.lineInterpolate = value ? 'monotone' : 'linear'
        }
      }
    },
  },
  {
    key: 'show_area',
    appliesTo: ['line'],
    optionGuard: (options) => !!(options.children && options.children.some((c: any) => c.type === 'area')),
    apply: (options, value) => {
      for (const child of options.children) {
        if (child.type === 'area') {
          if (!child.style) child.style = {}
          child.style.opacity = value ? 0.3 : 0
        }
      }
    },
  },
  {
    key: 'show_points',
    appliesTo: ['line'],
    optionGuard: (options) => !!(options.children && options.children.some((c: any) => c.type === 'point')),
    apply: (options, value) => {
      for (const child of options.children) {
        if (child.type === 'point') {
          if (!child.style) child.style = {}
          child.style.opacity = value ? 1 : 0
        }
      }
    },
  },
  {
    key: 'y_axis_zero',
    appliesTo: ['line'],
    optionGuard: (options) => !!(options.scale && options.children),
    apply: (options, value) => {
      const existingYScale = options.scale.y || {}
      // G2 v5 linear scale 支持 zero 属性强制包含 0
      options.scale.y = Object.assign({}, existingYScale, {
        zero: value === true,
      })
    },
  },

  // ── pie 专属 ──
  {
    key: 'donut',
    appliesTo: ['pie'],
    optionGuard: (options) => options.coordinate?.type === 'theta',
    apply: (options, value) => {
      options.coordinate.innerRadius = value ? 0.5 : 0
    },
  },
  {
    key: 'label_format',
    appliesTo: ['pie'],
    optionGuard: () => true,
    // label_format 由 Pie._applyTypeSettings 处理标签文本格式
    apply: () => { /* handled in _applyTypeSettings */ },
  },

  // ── table 专属 ── (Table 类直接覆盖 applySettings，此处占位)
  {
    key: 'sort_column',
    appliesTo: ['table'],
    optionGuard: () => false, // Table 类不走此路径
    apply: () => {},
  },
  {
    key: 'sort_order',
    appliesTo: ['table'],
    optionGuard: () => false,
    apply: () => {},
  },
  {
    key: 'page_size',
    appliesTo: ['table'],
    optionGuard: () => false,
    apply: () => {},
  },
  {
    key: 'number_format',
    appliesTo: ['table', 'column', 'bar', 'line'],
    optionGuard: () => true,
    // number_format 在 BaseG2Chart._applyTypeSettings 中写入 this.numberFormat
    // init() 时 labels/tooltips 的 formatNumber(value, this.numberFormat) 消费
    apply: () => { /* handled in _applyTypeSettings */ },
  },

  // 坐标轴字段由 DisplayChartBlock 按数据通道语义自动推导，不再通过 settings 控制。
]

// 按 key 索引，方便 O(1) 查找
const SETTING_APPLIER_MAP = new Map(SETTING_APPLIERS.map(a => [a.key, a]))

// ═══════════════════════════════════════════════════════════════════════════════

export abstract class BaseG2Chart extends BaseChart {
  chart: Chart
  /** 标记最近一次 init() 是否成功设置 options（未 bail out） */
  protected _initOk: boolean = false
  /** G2 Canvas 图表支持实时缩放（autoFit: true），无需 debounce */
  readonly isLiveResizable: boolean = true
  /** applySettings 暂存的 settings，由 init() 消费 */
  protected _activeSettings: Record<string, any> = {}
  /** 当前排序状态，init() 据此对渲染副本排序（避免原地修改 this.data） */
  protected _sortOrder: 'asc' | 'desc' | 'none' = 'none'

  constructor(id: string, name: string) {
    super(id, name)
    this.chart = new Chart({
      container: id,
      autoFit: true,
      padding: 'auto',
    })

    this.chart.theme({
      view: {
        viewFill: '#FFFFFF',
      },
    })
  }

  // ── 唯一对外入口 ──

  applySettings(settings: Record<string, any>): void | Promise<void> {
    // Step 1: 暂存 settings（init() 会读取）
    this._activeSettings = settings
    // Step 2: 子类修改 this 状态（showLabel, data 排序等）
    this._applyTypeSettings(settings)
    // Step 3: 重建——init() 内部统一调用 chart.options()，无外部 options 调用
    this.init(this.axis, this.data)
    // Step 4: 渲染
    if (this._initOk) {
      return this.render()
    }
  }

  protected _applyTypeSettings(settings: Record<string, any>): void {
    if (settings.number_format !== undefined) {
      const validFormats: Array<'full' | 'abbreviated' | 'percent'> = ['full', 'abbreviated', 'percent']
      this.numberFormat = validFormats.includes(settings.number_format)
        ? settings.number_format
        : 'abbreviated'
    }
    // 'none' 视为不排序，避免字符串 truthy 导致默认排序
    this._sortOrder = settings.sort && settings.sort !== 'none' ? settings.sort : 'none'
  }

  // ── 各图表 init() 构建完 options 后调用的公共方法 ──

  /**
   * 将通用 + 类型独占 settings 注入 options 对象。
   * 在各图表 init() 中、this.chart.options(options) 之前调用。
   *
   * 通过 SETTING_APPLIERS 声明式映射，自动完成:
   *   1. 只应用当前图表类型 (appliesTo) 匹配的设置
   *   2. 运行时守卫 (optionGuard) 确保目标 options 结构兼容
   *   3. 调用对应的 apply 函数写入 options
   */
  protected _applySettingsToOptions(options: any, settings: Record<string, any>): void {
    const chartName = this._name

    for (const [key, value] of Object.entries(settings)) {
      if (value === undefined || value === null) continue

      const applier = SETTING_APPLIER_MAP.get(key)
      if (!applier) continue

      // 检查1: appliesTo 中是否包含当前图表类型
      if (applier.appliesTo.length > 0 && !applier.appliesTo.includes(chartName)) {
        continue
      }

      // 检查2: 运行时守卫 — 当前 options 结构是否支持此设置
      if (!applier.optionGuard(options, chartName)) {
        continue
      }

      // 执行应用
      applier.apply(options, value, chartName)
    }
  }

  // ── 渲染 / 销毁 ──

  render(): Promise<void> | undefined {
    return this.chart?.render()?.catch((e: any) => {
      console.warn('[BaseG2Chart] render rejected:', e?.message || e)
    })
  }

  destroy() {
    this.chart?.destroy()
  }
}
