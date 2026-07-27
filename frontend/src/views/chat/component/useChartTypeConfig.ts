import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { request } from '@/utils/request'

// ── 图标：直接 import，避免跨模块引用导致生产构建初始化顺序问题 ──
import ICON_TABLE from '@/assets/svg/chart/icon_form_outlined.svg'
import ICON_COLUMN from '@/assets/svg/chart/icon_dashboard_outlined.svg'
import ICON_BAR from '@/assets/svg/chart/icon_bar_outlined.svg'
import ICON_LINE from '@/assets/svg/chart/icon_chart-line.svg'
import ICON_PIE from '@/assets/svg/chart/icon_pie_outlined.svg'
import ICON_SORT from '@/assets/svg/chart/icon_sort_outlined.svg'
import ICON_PAGE from '@/assets/svg/chart/icon_page_outlined.svg'
import ICON_NUMBER from '@/assets/svg/chart/icon_number_outlined.svg'
import ICON_STACK from '@/assets/svg/chart/icon_stack_outlined.svg'
import ICON_LABEL from '@/assets/svg/chart/icon_label_outlined.svg'
import ICON_GRID from '@/assets/svg/chart/icon_grid_outlined.svg'
import ICON_LEGEND from '@/assets/svg/chart/icon_legend_outlined.svg'
import ICON_SMOOTH from '@/assets/svg/chart/icon_smooth_outlined.svg'
import ICON_AREA from '@/assets/svg/chart/icon_area_outlined.svg'
import ICON_POINT from '@/assets/svg/chart/icon_point_outlined.svg'
import ICON_AXIS from '@/assets/svg/chart/icon_axis_outlined.svg'
import ICON_DONUT from '@/assets/svg/chart/icon_donut_outlined.svg'
import ICON_PERCENT from '@/assets/svg/chart/icon_percent_outlined.svg'
import ICON_PALETTE from '@/assets/svg/chart/icon_palette_outlined.svg'
import ICON_STYLE from '@/assets/svg/icon_style-set_outlined.svg'

// ── 图表类型图标映射 ──
const CHART_ICONS: Record<string, any> = {
  table: ICON_TABLE, column: ICON_COLUMN, bar: ICON_BAR,
  line: ICON_LINE, pie: ICON_PIE,
}
function _resolveChartIcon(iconKey: string | undefined): any {
  if (!iconKey) return null
  return CHART_ICONS[iconKey] || ICON_STYLE
}

// ── Settings 图标映射（与 chart_registry.py settingsSchema 完全对应）──
const SETTING_ICONS: Record<string, any> = {
  sort: ICON_SORT, sort_column: ICON_SORT, sort_order: ICON_SORT,
  page: ICON_PAGE, page_size: ICON_PAGE,
  number: ICON_NUMBER, number_format: ICON_NUMBER,
  palette: ICON_PALETTE, color_palette: ICON_PALETTE,
  stack: ICON_STACK,
  label: ICON_LABEL, show_label: ICON_LABEL,
  grid: ICON_GRID,
  legend: ICON_LEGEND,
  smooth: ICON_SMOOTH,
  area: ICON_AREA, show_area: ICON_AREA,
  point: ICON_POINT, show_points: ICON_POINT,
  axis: ICON_AXIS, y_axis_zero: ICON_AXIS,
  donut: ICON_DONUT,
  percent: ICON_PERCENT, label_format: ICON_PERCENT,
}
function _resolveSettingIcon(iconKey: string | undefined): any {
  if (!iconKey) return null
  return SETTING_ICONS[iconKey] || ICON_STYLE
}

// ── 类型定义 ──────────────────────────────────────────────────────────────────

interface ChartTypeInfo {
  typeId: string
  displayName: string
  icon: string
  category: string
  compatibleWith: string[]
  dataConstraints: Record<string, any>
  settingsSchema: Record<string, {
    desc_cn: string
    desc_en?: string
    type: string
    default: any
    icon: string
    label_cn: string
    label_en?: string
    options?: any[]
    show_when?: Record<string, any>
  }>
  baseClass: string
  hasSsr: boolean
  usesAxis: boolean
}

export interface ChartTypeConfig {
  chartTypes: Record<string, ChartTypeInfo>
  allTypeIds: string[]
  categories: Record<string, string[]>
  defaultType: string
}

export interface ChartTypeOption {
  value: string
  name: string
  icon: any  // SVG component
}

export interface ToolbarButton {
  key: string
  icon: any      // SVG component
  label: string
  type: string   // "bool" | "select"
  options: any[]
  optionLabels?: Record<string, string>  // option value → display label
  default: any
}

/** 选项值的中文显示名映射 */
const OPTION_LABELS: Record<string, Record<string, string>> = {
  sort_order: { asc: '升序', desc: '降序' },
  sort: { none: '默认', asc: '升序', desc: '降序' },
  number_format: { full: '完整', abbreviated: '缩写', percent: '百分比' },
  color_palette: { default: '默认', warm: '暖色', cool: '冷色', business: '商务' },
  label_format: { name_value: '名称+数值', name_percent: '名称+百分比', value_percent: '数值+百分比' },
  page_size: { '20': '20条/页', '50': '50条/页', '100': '100条/页' },
}

// ── API 不可用时的兜底 ──────────────────────────────────────────────────────

// 与 chart_registry.py 完全同步的兜底配置
// 确保 API 不可用时，所有图表类型的设置按钮都能正常显示
const FALLBACK_CONFIG: ChartTypeConfig = {
  chartTypes: {
    table: {
      typeId: 'table', displayName: '明细表', icon: 'table', category: 'table',
      compatibleWith: ['column', 'bar', 'line'], dataConstraints: { min_metrics: 0, max_metrics: 999, min_dimensions: 0, max_dimensions: 999 },
      settingsSchema: {
        sort_column: { desc_cn: '', type: 'select', default: '', icon: 'sort', label_cn: '排序列', options: [] },
        sort_order: { desc_cn: '', type: 'select', default: 'desc', icon: 'sort', label_cn: '排序', options: ['asc', 'desc'] },
        page_size: { desc_cn: '', type: 'select', default: 50, icon: 'page', label_cn: '分页', options: [20, 50, 100] },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
      },
      baseClass: 'BaseChart', hasSsr: false, usesAxis: false,
    },
    column: {
      typeId: 'column', displayName: '柱状图', icon: 'column', category: 'comparison',
      compatibleWith: ['bar', 'line', 'pie'], dataConstraints: {
        min_metrics: 1, min_dimensions: 1,
        requiredChannels: {
          x: { type: 'dimension', forbidden: ['temporal'], maxCardinality: 60 },
          y: { type: 'metric', min: 1, max: 1 },
          color: { type: 'dimension', optional: true, maxCardinality: 10 },
        },
      },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        stack: { desc_cn: '', type: 'bool', default: false, icon: 'stack', label_cn: '堆叠', show_when: { has_series: true } },
        sort: { desc_cn: '', type: 'select', default: 'none', icon: 'sort', label_cn: '排序', options: ['none', 'asc', 'desc'] },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    bar: {
      typeId: 'bar', displayName: '条形图', icon: 'bar', category: 'comparison',
      compatibleWith: ['column', 'line', 'pie'], dataConstraints: {
        min_metrics: 1, min_dimensions: 1,
        requiredChannels: {
          x: { type: 'dimension', forbidden: ['temporal'], maxCardinality: 60 },
          y: { type: 'metric', min: 1, max: 1 },
          color: { type: 'dimension', optional: true, maxCardinality: 10 },
        },
      },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        stack: { desc_cn: '', type: 'bool', default: false, icon: 'stack', label_cn: '堆叠', show_when: { has_series: true } },
        sort: { desc_cn: '', type: 'select', default: 'none', icon: 'sort', label_cn: '排序', options: ['none', 'asc', 'desc'] },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    line: {
      typeId: 'line', displayName: '折线图', icon: 'line', category: 'trend',
      compatibleWith: ['column', 'bar', 'pie'], dataConstraints: {
        min_metrics: 1, min_dimensions: 1,
        requiredChannels: {
          x: { type: 'dimension', preferred: ['temporal', 'ordinal'], maxCardinality: 500 },
          y: { type: 'metric', min: 1, max: 1 },
          color: { type: 'dimension', optional: true, maxCardinality: 20 },
        },
      },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        smooth: { desc_cn: '', type: 'bool', default: false, icon: 'smooth', label_cn: '平滑' },
        show_area: { desc_cn: '', type: 'bool', default: false, icon: 'area', label_cn: '面积' },
        show_points: { desc_cn: '', type: 'bool', default: false, icon: 'point', label_cn: '数据点' },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
        y_axis_zero: { desc_cn: '', type: 'bool', default: true, icon: 'axis', label_cn: 'Y轴归零' },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    pie: {
      typeId: 'pie', displayName: '饼图', icon: 'pie', category: 'proportion',
      compatibleWith: [], dataConstraints: {
        min_metrics: 1, max_metrics: 1, min_dimensions: 1, max_dimensions: 1,
        requires_series: true, max_series_cardinality: 10,
        requiredChannels: {
          color: { type: 'dimension', maxCardinality: 10 },
          theta: { type: 'metric', positive: true, ratioForbidden: true },
        },
      },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        donut: { desc_cn: '', type: 'bool', default: false, icon: 'donut', label_cn: '环形' },
        sort: { desc_cn: '', type: 'select', default: 'none', icon: 'sort', label_cn: '排序', options: ['none', 'asc', 'desc'] },
        show_label: { desc_cn: '', type: 'bool', default: true, icon: 'label', label_cn: '标签' },
        label_format: { desc_cn: '', type: 'select', default: 'name_value', icon: 'percent', label_cn: '标签格式', options: ['name_value', 'name_percent', 'value_percent'] },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例' },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
  },
  allTypeIds: ['table', 'column', 'bar', 'line', 'pie'],
  categories: { table: ['table'], comparison: ['column', 'bar'], trend: ['line'], proportion: ['pie'] },
  defaultType: 'table',
}

// ── 单例 config ──────────────────────────────────────────────────────────────

const config = ref<ChartTypeConfig | null>(null)
let pending = false

export function useChartTypeConfig() {
  if (!config.value && !pending) {
    pending = true
    request.get('/chat/chart-types')
      .then((r: any) => { config.value = r })
      .catch(() => {
        console.warn('[ChartTypeConfig] API unavailable, using fallback')
        config.value = FALLBACK_CONFIG
      })
  }
  return { config }
}

/**
 * 获取图表类型切换列表（响应式）
 *
 * 以 sourceTypeRef（AI 最初选择的类型）为基线，查找其 compatibleWith，
 * 始终包含 sourceType 自身，确保可以切回。
 *
 * @param configRef - useChartTypeConfig() 返回的 config ref
 * @param sourceTypeRef - 原始图表类型 ref（AI 选择的），跟随数据加载变化
 * @returns 可切换的图表类型列表（含 SVG 图标组件）
 */
export function getChartTypeList(
  configRef: Ref<ChartTypeConfig | null>,
  sourceTypeRef: Ref<string | undefined>,
): ComputedRef<ChartTypeOption[]> {
  return computed<ChartTypeOption[]>(() => {
    const sourceType = sourceTypeRef.value
    if (!sourceType || !configRef.value) return []
    const info = configRef.value.chartTypes[sourceType]
    if (!info) return []

    // 当前类型 + 兼容类型（去重）
    const seen = new Set([sourceType])
    const ids = [sourceType]
    const compat = info.compatibleWith || []

    // compatibleWith 为空时兜底：展示全部非 table 图表类型
    // 确保用户始终可以从表格切换到可视化图表
    if (compat.length === 0) {
      for (const id of configRef.value.allTypeIds) {
        if (!seen.has(id) && id !== sourceType) {
          seen.add(id); ids.push(id)
        }
      }
    } else {
      for (const id of compat) {
        if (!seen.has(id)) { seen.add(id); ids.push(id) }
      }
    }

    return ids.map(id => ({
      value: id,
      name: configRef.value!.chartTypes[id]?.displayName || id,
      icon: _resolveChartIcon(configRef.value!.chartTypes[id]?.icon),
    }))
  })
}

/**
 * 获取当前图表类型的所有设置按钮（统一列表，从 settingsSchema 驱动）
 *
 * 设计变更: 不再区分 quickToggles / gearSettings。
 * 所有设置（bool + select）统一收入齿轮 popover，工具栏只放一个齿轮按钮。
 *
 * show_when 条件支持:
 *   - { has_series: true } — 仅当数据含 series 字段时显示
 *   - 未来可扩展更多条件
 *
 * @param configRef - useChartTypeConfig() 返回的 config ref
 * @param typeIdRef - 当前图表类型 ID 的 ref
 * @param hasSeriesRef - 当前数据是否包含 series 字段的 ref（用于 show_when 条件）
 * @returns allSettings: 所有设置按钮列表（已过滤 show_when 条件）
 */
export function getToolbarButtons(
  configRef: Ref<ChartTypeConfig | null>,
  typeIdRef: Ref<string | undefined>,
  hasSeriesRef?: Ref<boolean>,
) {
  const allSettings = computed<ToolbarButton[]>(() => {
    const currentType = typeIdRef.value
    if (!currentType || !configRef.value) return []
    const schema = configRef.value.chartTypes[currentType]?.settingsSchema
    if (!schema) return []

    const hasSeries = hasSeriesRef?.value ?? false

    const buttons: ToolbarButton[] = []
    for (const [key, s] of Object.entries(schema) as [string, any][]) {
      if (!s.icon) continue

      // show_when 条件过滤
      if (s.show_when) {
        if (s.show_when.has_series === true && !hasSeries) continue
        if (s.show_when.has_series === false && hasSeries) continue
      }

      buttons.push({
        key,
        icon: _resolveSettingIcon(s.icon),
        label: s.label_cn || s.label_en || key,
        type: s.type || 'bool',
        options: s.options || [],
        optionLabels: OPTION_LABELS[key] || {},
        default: s.default,
      })
    }
    return buttons
  })

  return { allSettings }
}
