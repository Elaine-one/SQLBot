/**
 * 图表图标注册表 — 集中式图标映射 (Single Source of Truth)
 * ============================================================
 *
 * 所有 API 下发的 icon 字段值（string）到 SVG 组件的映射均在此文件维护。
 *
 * 设计参考: dev-plan-phase2 第 5.3 节「ICON_MAP[s.icon] 动态解析」
 *
 * 使用方:
 *   - useChartTypeConfig composable (resolveChartIcon / resolveSettingIcon)
 *   - ChartBlock.vue / sq-view/index.vue → 通过 composable 间接消费
 *   - ChartPopover.vue → 通过 props 接收已解析的图标
 *
 * 新增图标:
 *   1. 在 src/assets/svg/chart/ 下放置 SVG 文件
 *   2. 在此文件的 CHART_TYPE_ICONS / SETTING_ICONS 中添加 import + 条目
 *   3. 消费方无需改动——chartTypeList 和 toolbarButtons 自动长出
 */

// ── 图表类型图标 ──────────────────────────────────────────────────────────────
import ICON_TABLE from '@/assets/svg/chart/icon_form_outlined.svg'
import ICON_COLUMN from '@/assets/svg/chart/icon_dashboard_outlined.svg'
import ICON_BAR from '@/assets/svg/chart/icon_bar_outlined.svg'
import ICON_LINE from '@/assets/svg/chart/icon_chart-line.svg'
import ICON_PIE from '@/assets/svg/chart/icon_pie_outlined.svg'

// ── Settings 专属图标 ──────────────────────────────────────────────────────────
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

// ── 通用 fallback ──────────────────────────────────────────────────────────────
import ICON_STYLE from '@/assets/svg/icon_style-set_outlined.svg'

/**
 * 图表类型图标映射: API 字段 `chartTypes[id].icon` → SVG 组件
 *
 * API 返回示例:
 *   { typeId: "column", icon: "column", ... }
 *   { typeId: "pie",    icon: "pie",    ... }
 *
 * 这里的 key 值与 chart_registry.py ChartTypeDef.icon 字段完全对应。
 */
export const CHART_TYPE_ICONS: Record<string, any> = {
  table: ICON_TABLE,
  column: ICON_COLUMN,
  bar: ICON_BAR,
  line: ICON_LINE,
  pie: ICON_PIE,
  // 扩展示例（取消注释 chart_registry.py 中的定义后启用）:
  // area: ICON_AREA,
  // scatter: ICON_SCATTER,
  // radar: ICON_RADAR,
  // funnel: ICON_FUNNEL,
}

/**
 * Settings 图标映射: API 字段 `settingsSchema[key].icon` → SVG 组件
 *
 * API 返回示例:
 *   { stack: { icon: "stack", type: "bool", ... } }
 *   { sort:  { icon: "sort",  type: "select", ... } }
 *
 * 这里的 key 值与 chart_registry.py settings_schema[].icon 字段完全对应。
 */
export const SETTING_ICONS: Record<string, any> = {
  // 排序/排序列
  sort: ICON_SORT,
  sort_column: ICON_SORT,
  sort_order: ICON_SORT,

  // 分页
  page: ICON_PAGE,
  page_size: ICON_PAGE,

  // 数值格式
  number: ICON_NUMBER,
  number_format: ICON_NUMBER,

  // 配色
  palette: ICON_PALETTE,
  color_palette: ICON_PALETTE,

  // 堆叠
  stack: ICON_STACK,

  // 标签
  label: ICON_LABEL,
  show_label: ICON_LABEL,

  // 网格
  grid: ICON_GRID,

  // 图例
  legend: ICON_LEGEND,

  // 平滑曲线
  smooth: ICON_SMOOTH,

  // 面积填充
  area: ICON_AREA,
  show_area: ICON_AREA,

  // 数据点
  point: ICON_POINT,
  show_points: ICON_POINT,

  // 坐标轴
  axis: ICON_AXIS,
  y_axis_zero: ICON_AXIS,

  // 环形
  donut: ICON_DONUT,

  // 百分比 / 标签格式
  percent: ICON_PERCENT,
  label_format: ICON_PERCENT,
}

/**
 * 解析图表类型图标
 *
 * @param iconKey - API 下发的 icon 字段值 (string)，如 "column"、"pie"、"line"
 * @returns Vue 组件（SVG）或 null
 */
export function resolveChartIcon(iconKey: string | undefined): any {
  if (!iconKey) return null
  return CHART_TYPE_ICONS[iconKey] || ICON_STYLE
}

/**
 * 解析 Settings 按钮图标
 *
 * @param iconKey - API 下发的 settingsSchema[key].icon 字段值 (string)
 * @returns Vue 组件（SVG）或 null
 */
export function resolveSettingIcon(iconKey: string | undefined): any {
  if (!iconKey) return null
  return SETTING_ICONS[iconKey] || ICON_STYLE
}

/**
 * 获取通用设置图标（齿轮），用于 gear popover 触发按钮
 */
export function getGearIcon(): any {
  return ICON_STYLE
}
