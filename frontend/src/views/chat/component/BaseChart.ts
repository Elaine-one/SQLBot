export interface ChartAxis {
  name: string
  value: string
  type?: 'x' | 'y' | 'series' | 'other-info'
  'multi-quota'?: boolean
  hidden?: boolean
}

export interface ChartData {
  [key: string]: any
}

// 允许任意 string（新增图表类型从 API 下发），同时保留已知类型的自动补全
export type ChartTypes = 'table' | 'bar' | 'column' | 'line' | 'pie' | (string & {})

export abstract class BaseChart {
  id: string
  _name: string = 'base-chart'
  axis: Array<ChartAxis> = []
  data: Array<ChartData> = []
  showLabel: boolean = false
  /** 数字格式化模式：full=完整千分位, abbreviated=自动缩写(万/亿), percent=百分比 */
  numberFormat: 'full' | 'abbreviated' | 'percent' = 'abbreviated'

  constructor(id: string, name: string) {
    this.id = id
    this._name = name
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>): void {
    this.axis = axis
    this.data = data
  }

  applySettings(_settings: Record<string, any>): void | Promise<void> {
    // 子类覆盖——将 settings 映射到各自的渲染库 (G2/S2) options
  }

  abstract render(): void

  abstract destroy(): void
}
