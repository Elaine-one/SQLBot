import { BaseChart } from '@/views/chat/component/BaseChart.ts'
import { CartesianChart } from '@/views/chat/component/CartesianChart'

// ── glob 自动发现 charts/ 目录下的所有图表类 ──
// 约定: charts/Bar.ts 导出 class Bar extends BaseG2Chart
// 新增图表文件后零注册，Vite 构建时自动打包

const chartModules = import.meta.glob('./charts/*.ts', { eager: true })

const CHART_TYPE_MAP: { [key: string]: any } = {}

for (const [path, mod] of Object.entries(chartModules)) {
  const fileName = (path.split('/').pop() || '').replace('.ts', '')
  // 跳过工具文件
  if (fileName === 'utils') continue
  // 文件名即类名: Bar.ts → class Bar, Pie.ts → class Pie
  const cls = (mod as Record<string, any>)[fileName]
  if (cls) {
    CHART_TYPE_MAP[fileName.toLowerCase()] = cls
  }
}

// ── 辅助 ──

const isParent = (type: any, parentType: any) => {
  let _type = type
  while (_type) {
    if (_type === parentType) {
      return true
    }
    _type = _type.__proto__
  }
  return false
}

/** 笛卡尔图表的类型列表 — 这些类型共享同一个 CartesianChart 实例 */
const CARTESIAN_TYPES = new Set(['column', 'bar', 'line'])

export function getChartInstance(type: string, id: string): BaseChart | undefined {
  // 笛卡尔图表统一用 CartesianChart，切换类型不需要销毁重建
  if (CARTESIAN_TYPES.has(type)) {
    return new CartesianChart(id, type)
  }

  if (isParent(CHART_TYPE_MAP[type], BaseChart)) {
    return new CHART_TYPE_MAP[type](id) as BaseChart
  }
  return undefined
}

/** 判断给定类型是否可以在不销毁实例的情况下切换 */
export function canSwitchInPlace(fromType: string, toType: string): boolean {
  // 笛卡尔图表之间可以原地切换
  if (CARTESIAN_TYPES.has(fromType) && CARTESIAN_TYPES.has(toType)) return true
  // 同类型之间可以
  return fromType === toType
}

