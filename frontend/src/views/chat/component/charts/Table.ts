import { BaseChart, type ChartAxis, type ChartData } from '@/views/chat/component/BaseChart.ts'
import { toRaw } from 'vue'
import {
  copyToClipboard,
  type S2DataConfig,
  S2Event,
  type S2MountContainer,
  type S2Options,
  type SortMethod,
  TableSheet,
  type SortFuncParam,
} from '@antv/s2'
import { debounce } from 'lodash-es'
import { i18n } from '@/i18n'
import { formatNumber } from '@/views/chat/component/charts/utils.ts'
import '@antv/s2/dist/s2.min.css'

const { t } = i18n.global

const createSmartSortFunc = (sortMethod: string) => {
  const compareNumericString = (a: string, b: string): number => {
    const isNegA = a.startsWith('-')
    const isNegB = b.startsWith('-')

    // 负数 < 正数
    if (isNegA && !isNegB) return -1
    if (!isNegA && isNegB) return 1

    const [intA, decA = ''] = isNegA ? a.slice(1).split('.') : a.split('.')
    const [intB, decB = ''] = isNegB ? b.slice(1).split('.') : b.split('.')

    // 都是正数
    if (!isNegA && !isNegB) {
      if (intA.length !== intB.length) return intA.length - intB.length
      const intCmp = intA.localeCompare(intB)
      if (intCmp !== 0) return intCmp
      if (decA && decB) return decA.localeCompare(decB)
      return decA ? 1 : decB ? -1 : 0
    }

    // 都是负数：绝对值大的实际值小，比较结果取反
    if (intA.length !== intB.length) return -(intA.length - intB.length)
    const intCmp = intA.localeCompare(intB)
    if (intCmp !== 0) return -intCmp
    if (decA && decB) return -decA.localeCompare(decB)
    return decA ? 1 : decB ? -1 : 0
  }

  return (params: SortFuncParam) => {
    const { data, sortFieldId } = params
    if (!data || data.length === 0) return data
    const isAsc = sortMethod.toLowerCase() === 'asc'
    return [...data].sort((a: any, b: any) => {
      const valA = a[sortFieldId],
        valB = b[sortFieldId]
      if (valA == null) return isAsc ? -1 : 1
      if (valB == null) return isAsc ? 1 : -1
      const strA = String(valA),
        strB = String(valB)
      const isNumA = !isNaN(Number(strA)) && strA.trim() !== ''
      const isNumB = !isNaN(Number(strB)) && strB.trim() !== ''
      if (isNumA && !isNumB) return isAsc ? -1 : 1
      if (!isNumA && isNumB) return isAsc ? 1 : -1
      if (isNumA && isNumB) {
        const cmp = compareNumericString(strA, strB)
        return isAsc ? cmp : -cmp
      }
      const cmp = strA.localeCompare(strB)
      return isAsc ? cmp : -cmp
    })
  }
}

export class Table extends BaseChart {
  table?: TableSheet = undefined
  /** S2 表格使用 ResizeObserver + 200ms debounce，非实时缩放 */
  readonly isLiveResizable: boolean = false

  container: S2MountContainer | null = null

  debounceRender: any

  resizeObserver: ResizeObserver

  constructor(id: string) {
    super(id, 'table')
    this.container = document.getElementById(id)

    this.debounceRender = debounce(async (width?: number, height?: number) => {
      if (this.table) {
        this.table.changeSheetSize(width, height)
        await this.table.render(false)
      }
    }, 200)

    this.resizeObserver = new ResizeObserver(([entry] = []) => {
      const [size] = entry.borderBoxSize || []
      this.debounceRender(size.inlineSize, size.blockSize)
    })

    if (this.container?.parentElement) {
      this.resizeObserver.observe(this.container.parentElement)
    }
  }

  applyPostInitSettings(settings: Record<string, any>): void {
    if (!this.table) return

    // 排序（仅当用户显式选择排序列时才生效，避免默认按首列排序）
    if (settings.sort_column) {
      let col = settings.sort_column
      if (typeof col === 'string') {
        const matched = this.axis.find((a) => a.name === col)
        if (matched) col = matched.value
      }
      const order = settings.sort_order || 'desc'
      if (col) {
        const sortParams = [{
          sortFieldId: col,
          sortMethod: order === 'asc' ? 'asc' : ('desc' as SortMethod),
          sortFunc: createSmartSortFunc(order),
        }]
        this.table.setDataCfg({ sortParams } as any)
      }
    }

    // 分页
    if (settings.page_size) {
      this.table.setOptions({ pagination: { current: 1, pageSize: settings.page_size } })
    }
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    // 过滤 + 去重：只保留 value 为有效字符串的轴，避免 S2 内部读取 undefined.rows 报错
    const seen = new Set<string>()
    const deduped: Array<ChartAxis> = []
    for (const a of axis) {
      const value = a.value == null ? '' : String(a.value).trim()
      if (!value || a.hidden || seen.has(value)) continue
      seen.add(value)
      // 创建新对象，避免修改传入的 axis 引用
      deduped.push({ ...a, value })
    }
    super.init(deduped, data)

    // 防御：无列或无数据时不创建 S2 实例
    if (!this.axis || this.axis.length === 0 || !this.data || this.data.length === 0) {
      console.warn('[Table] init skipped: empty axis or data', { axis: this.axis, data: this.data })
      return
    }

    const numberFmt = this.numberFormat

    // 先 toRaw() 解包 Vue Proxy，再 structuredClone 深拷贝（比 JSON round-trip 快 3-5 倍）
    let plainData: Array<ChartData> = []
    if (this.data) {
      const raw = toRaw(this.data)
      try {
        plainData = structuredClone(raw)
      } catch {
        plainData = JSON.parse(JSON.stringify(raw))
      }
    }

    const s2DataConfig: S2DataConfig = {
      sortParams:
        this.axis?.map((a) => {
          return {
            sortFieldId: a.value,
          }
        }) ?? [],
      fields: {
        columns: this.axis?.map((a) => a.value) ?? [],
        rows: [],
        values: [],
        valueInCols: false,
      },
      meta:
        this.axis?.map((a) => {
          return {
            field: a.value,
            name: a.name,
            formatter: (value: any) => {
              const formatted = formatNumber(value, numberFmt)
              return String(formatted)
            },
          }
        }) ?? [],
      data: plainData,
    }

    const sortState: Record<string, string> = {}

    const handleSortClick = (params: any) => {
      const { meta } = params
      const s2 = meta.spreadsheet
      if (s2 && meta.isLeaf) {
        const fieldId = meta.field
        const currentMethod = sortState[fieldId] || 'none'
        const sortOrder = ['none', 'desc', 'asc']
        const nextMethod = sortOrder[(sortOrder.indexOf(currentMethod) + 1) % sortOrder.length]
        sortState[fieldId] = nextMethod
        if (nextMethod === 'none') {
          s2.emit(S2Event.RANGE_SORT, [{ sortFieldId: fieldId, sortMethod: 'none' as SortMethod }])
        } else {
          s2.emit(S2Event.RANGE_SORT, [
            {
              sortFieldId: fieldId,
              sortMethod: nextMethod as SortMethod,
              sortFunc: createSmartSortFunc(nextMethod),
            },
          ])
        }
        s2.render()
      }
    }

    const s2Options: S2Options = {
      width: 600,
      height: 360,
      showDefaultHeaderActionIcon: false,
      headerActionIcons: [
        {
          icons: ['GlobalDesc'],
          belongsCell: 'colCell',
          displayCondition: (node: any) => node.isLeaf && sortState[node.field] === 'desc',
          onClick: handleSortClick,
        },
        {
          icons: ['GlobalAsc'],
          belongsCell: 'colCell',
          displayCondition: (node: any) => node.isLeaf && sortState[node.field] === 'asc',
          onClick: handleSortClick,
        },
        {
          icons: ['SortDown'],
          belongsCell: 'colCell',
          displayCondition: (node: any) =>
            node.isLeaf && (!sortState[node.field] || sortState[node.field] === 'none'),
          onClick: handleSortClick,
        },
      ],
      tooltip: {
        operation: {
          sort: true,
        },
        dataCell: {
          enable: true,
          content: (cell) => {
            const meta = cell.getMeta()
            const container = document.createElement('div')
            container.style.padding = '8px 0'
            container.style.minWidth = '100px'
            container.style.maxWidth = '400px'
            container.style.display = 'flex'
            container.style.alignItems = 'center'
            container.style.padding = '8px 16px'
            container.style.cursor = 'pointer'
            container.style.color = '#606266'
            container.style.fontSize = '14px'
            container.style.whiteSpace = 'pre-wrap'

            const formattedValue = formatNumber(meta.fieldValue, numberFmt)
            const text = document.createTextNode(String(formattedValue))
            container.appendChild(text)

            return container
          },
        },
      },
      // 如果有省略号, 复制到的是完整文本
      interaction: {
        copy: {
          enable: true,
          withFormat: false,
          withHeader: false,
        },
        brushSelection: {
          dataCell: true,
          rowCell: true,
          colCell: true,
        },
      },
      placeholder: {
        cell: '-',
        empty: {
          icon: 'Empty',
          description: 'No Data',
        },
      },
    }

    if (this.container) {
      try {
        this.table = new TableSheet(this.container, s2DataConfig, s2Options)
        // S2 在 TableSheet 构造函数中不会立即初始化 dataSet.fields，
        // 而 TableFacet 构造函数里会直接读取 dataSet.fields 做布局，
        // 若首次 render 前 fields 未初始化会抛 "reading 'rows'"。
        // 手动同步 setDataCfg 一次，确保 fields 就绪。
        this.table.dataSet.setDataCfg(this.table.dataCfg)
      } catch (e) {
        console.error('[Table] TableSheet creation failed', e, s2DataConfig)
        return
      }
      // right click
      this.table.on(S2Event.GLOBAL_COPIED, (data) => {
        ElMessage.success(t('qa.copied'))
        console.debug('copied: ', data)
      })
      this.table.getCanvasElement().addEventListener('contextmenu', (event) => {
        event.preventDefault()
      })
      this.table.on(S2Event.GLOBAL_CONTEXT_MENU, (event) => copyData(event, this.table))
      // this.table.on(S2Event.RANGE_SORT, (sortParams) => {
      //   console.log('sortParams:', sortParams)
      // })
    }
  }

  render() {
    this.table?.render()
  }

  destroy() {
    this.table?.destroy()
    this.resizeObserver?.disconnect()
    // 清空容器 DOM，防止 S2 Canvas 在图表类型切换后残留
    if (this.container && typeof this.container !== 'string') {
      const el = this.container as HTMLElement
      el.innerHTML = ''
    }
  }

  applySettings(settings: Record<string, any>): void | Promise<void> {
    console.log(`[Table] applySettings | axisLen=${this.axis?.length} | dataLen=${this.data?.length} | hasTable=${!!this.table} | needsReinit=${settings.number_format !== undefined}`)

    if (!this.axis || this.axis.length === 0 || !this.data || this.data.length === 0) {
      console.warn(`[Table] applySettings BAILED: empty axis or data`)
      return
    }

    let needsReinit = false

    // number_format 变更需要重建 init —— 列格式化器在 init() 的 meta 中设定，
    // S2 不支持动态更新 formatter，所以必须销毁后重建
    if (settings.number_format !== undefined) {
      const validFormats: Array<'full' | 'abbreviated' | 'percent'> = ['full', 'abbreviated', 'percent']
      const newFormat = validFormats.includes(settings.number_format) ? settings.number_format : 'abbreviated'
      if (this.numberFormat !== newFormat) {
        this.numberFormat = newFormat
        needsReinit = true
      }
    }

    // 首次渲染或 number_format 变化时重建 S2 实例
    if (needsReinit || !this.table) {
      console.log(`[Table] calling init() | needsReinit=${needsReinit} | hasTable=${!!this.table}`)
      this.table?.destroy()
      this.init(this.axis, this.data)
      console.log(`[Table] init done | hasTable=${!!this.table}`)
    }

    // init 失败（如无列/无数据）则直接返回
    if (!this.table) {
      console.warn(`[Table] init failed: table is null`)
      return
    }

    // 确保排序、分页等设置在实例创建后应用
    this.applyPostInitSettings(settings)

    return this.table.render(false)
  }
}

function copyData(event: any, s2?: TableSheet) {
  event.preventDefault()
  if (!s2) {
    return
  }
  const cells = s2.interaction.getCells()

  if (cells.length == 0) {
    return
  } else if (cells.length == 1) {
    const c = cells[0]
    const cellMeta = s2.facet.getCellMeta(c.rowIndex, c.colIndex)
    if (cellMeta) {
      let value = cellMeta.fieldValue
      if (value === null || value === undefined) {
        value = '-'
      }
      value = value + ''
      copyToClipboard(value).finally(() => {
        ElMessage.success(t('qa.copied'))
        console.debug('copied:', cellMeta.fieldValue)
      })
    }
    return
  } else {
    let currentRowIndex = -1
    let currentRowData: Array<string> = []
    const rowData: Array<string> = []
    for (let i = 0; i < cells.length; i++) {
      const c = cells[i]
      const cellMeta = s2.facet.getCellMeta(c.rowIndex, c.colIndex)
      if (!cellMeta) {
        continue
      }
      if (currentRowIndex == -1) {
        currentRowIndex = c.rowIndex
      }
      if (c.rowIndex !== currentRowIndex) {
        rowData.push(currentRowData.join('\t'))
        currentRowData = []
        currentRowIndex = c.rowIndex
      }
      let value = cellMeta.fieldValue
      if (value === null || value === undefined) {
        value = '-'
      }
      value = value + ''
      currentRowData.push(value)
    }
    rowData.push(currentRowData.join('\t'))
    const finalValue = rowData.join('\n')
    copyToClipboard(finalValue).finally(() => {
      ElMessage.success(t('qa.copied'))
      console.debug('copied:\n', finalValue)
    })
  }
}
