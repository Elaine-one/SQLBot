<script setup lang="ts">
import { computed, nextTick, onErrorCaptured, onMounted, onUnmounted, ref, watch } from 'vue'
import { getChartInstance, canSwitchInPlace } from '@/views/chat/component/index.ts'
import { checkRenderable, ChartRenderError } from '@/views/chat/component/ChartRenderErrors'
import type { BaseChart, ChartAxis, ChartData } from '@/views/chat/component/BaseChart.ts'
import { useEmitt } from '@/utils/useEmitt.ts'

const params = withDefaults(
  defineProps<{
    id: string | number
    type: string
    data?: Array<ChartData>
    columns?: Array<ChartAxis>
    x?: Array<ChartAxis>
    y?: Array<ChartAxis>
    series?: Array<ChartAxis>
    multiQuotaName?: string | undefined
    showLabel?: boolean
    settings?: Record<string, any>
  }>(),
  {
    data: () => [],
    columns: () => [],
    x: () => [],
    y: () => [],
    series: () => [],
    multiQuotaName: undefined,
    showLabel: false,
    settings: () => ({}),
  }
)

const chartId = computed(() => {
  return 'chart-component-' + params.id
})

const axis = computed(() => {
  const _list: Array<ChartAxis> = []
  const seen = new Set<string>()

  // x/y/series/other-info 先加入（有 type 标记，优先级高）
  params.x.forEach((column) => {
    _list.push({ name: column.name, value: column.value, type: 'x' })
    seen.add(column.value)
  })
  params.y.forEach((column) => {
    _list.push({
      name: column.name,
      value: column.value,
      type: 'y',
      'multi-quota': column['multi-quota'],
    })
    seen.add(column.value)
  })
  params.series.forEach((column) => {
    _list.push({ name: column.name, value: column.value, type: 'series' })
    seen.add(column.value)
  })
  if (params.multiQuotaName) {
    _list.push({
      name: params.multiQuotaName,
      value: params.multiQuotaName,
      type: 'other-info',
      hidden: true,
    })
    seen.add(params.multiQuotaName)
  }

  // columns 补充未被 x/y/series 覆盖的列（去重保护）
  // 防御：columns 可能因为 AI 未生成或旧格式而为 undefined
  if (params.columns && params.columns.length > 0) {
    params.columns.forEach((column) => {
      if (!seen.has(column.value)) {
        _list.push({ name: column.name, value: column.value })
        seen.add(column.value)
      }
    })
  }
  return _list
})

let chartInstance: BaseChart | undefined

// ── 错误边界（对标 Metabase ChartRenderingErrorBoundary）──
const renderError = ref<string | null>(null)

onErrorCaptured((err: Error) => {
  console.error('[ChartComponent] render error captured:', err)
  renderError.value = err.message || '图表渲染异常'
  return false // 阻止错误继续向上传播
})

// 保存最近一次 renderChart 使用的 settings，确保 view-render-all 等事件
// 触发重渲染时不会丢失用户自定义的图表设置（图注、配色、分页等）
let _lastSettings: Record<string, any> | undefined = undefined

// 记录当前已渲染的图表类型，类型变化时必须先销毁旧实例
let _currentType: string | undefined = undefined

// 渲染调度：同一轮 Vue 更新中多次触发 renderChart 时只执行一次实际渲染，
// 并作废过期的调度，避免快速切换类型时旧实例覆盖新实例。
let _renderTick = 0
let _scheduledType: string | undefined = undefined
let _scheduledSettings: Record<string, any> | undefined = undefined
let _renderTimer: any = null

// 记录当前正在进行的渲染类型，异步渲染完成后若类型已变则丢弃结果
let _renderingType: string | undefined = undefined

// 记录当前正在进行的渲染 tick，用于丢弃同类型下的过期渲染
let _lastRenderTick: number = 0

// 非表格图表的 Canvas 渲染数据上限：超出截断，防止绘制海量元素卡死浏览器
// table 走 S2 分页不限；line 可承载更多；column/bar/pie 超出上限时截尾
const MAX_DISPLAY_POINTS: Record<string, number> = {
  column: 60,
  bar: 60,
  pie: 20,
  line: 500,
}

function renderChart(settings?: Record<string, any>) {
  renderError.value = null // 清除上次错误
  _lastSettings = settings
  _scheduledType = params.type
  _scheduledSettings = settings
  _renderTick++
  console.log(`[ChartComponent] renderChart scheduled | type=${params.type} | tick=${_renderTick} | hasSettings=${!!settings} | timerExists=${!!_renderTimer}`)

  if (_renderTimer) return
  _renderTimer = setTimeout(() => {
    _renderTimer = null
    _doRender(_scheduledType, _scheduledSettings, _renderTick)
  }, 0)
}

async function _doRender(
  expectedType: string | undefined,
  settings: Record<string, any> | undefined,
  tick: number
) {
  console.log(`[ChartComponent] _doRender START | type=${expectedType} | tick=${tick} | currentType=${_currentType} | hasInstance=${!!chartInstance}`)

  // 若类型在调度期间又发生变化，本次渲染作废，由最新一次调度处理
  if (tick !== _renderTick || expectedType !== params.type) {
    console.log(`[ChartComponent] _doRender ABORT: tick mismatch (current=${_renderTick}) or type mismatch (current=${params.type})`)
    return
  }

  // 渲染前确保容器存在；mounted 后 DOM 可能尚未就绪，延迟一次 nextTick
  let container = document.getElementById(chartId.value)
  if (!container) {
    nextTick(() => {
      container = document.getElementById(chartId.value)
      if (container) {
        _doRender(expectedType, settings, tick)
      } else {
        console.warn(`[ChartComponent] container not found: ${chartId.value}`)
      }
    })
    return
  }

  // ── 数据预处理（在类型切换前完成）──
  const data = params.data
  const maxPoints = MAX_DISPLAY_POINTS[params.type]
  const displayData = maxPoints && data.length > maxPoints ? data.slice(0, maxPoints) : data

  // ── 类型切换：笛卡尔图表之间原地切换，其他类型才 destroy + create ──
  const typeChanged = _currentType !== params.type && _currentType !== undefined

  if (typeChanged) {
    console.log(`[ChartComponent] typeChanged: ${_currentType} → ${params.type} | canSwitchInPlace=${canSwitchInPlace(_currentType!, params.type)} | hasChangeChartType=${typeof (chartInstance as any)?.changeChartType === 'function'}`)

    // 笛卡尔类型之间（column/bar/line）：调用 changeChartType 原地切换，不销毁实例
    if (
      canSwitchInPlace(_currentType!, params.type) &&
      typeof (chartInstance as any)?.changeChartType === 'function'
    ) {
      console.log(`[ChartComponent] IN-PLACE switch via changeChartType`)
      _currentType = params.type
      chartInstance!.showLabel = params.showLabel
      chartInstance!.axis = axis.value
      chartInstance!.data = displayData
      _renderingType = params.type
      _lastRenderTick = tick
      try {
        await (chartInstance as any).changeChartType(params.type, settings ?? {})
      } catch (e) {
        console.warn('[ChartComponent] changeChartType failed:', e)
      }
      if (_renderingType !== params.type || _lastRenderTick !== tick) return
      console.log(`[ChartComponent] _doRender DONE (in-place) | type=${params.type}`)
      return
    }

    // 非笛卡尔或跨类型（如 column→pie）：传统 destroy + create
    console.log(`[ChartComponent] DESTROY+CREATE switch`)
    _clearAllCanvas(container)
    destroyChart()
    _currentType = params.type
    if (container.innerHTML) container.innerHTML = ''
    _clearAllCanvas(container)
  }

  // 防御性清理
  if (chartInstance && (chartInstance as any)._name !== params.type && !canSwitchInPlace((chartInstance as any)._name ?? '', params.type)) {
    console.log(`[ChartComponent] stale instance cleanup: ${(chartInstance as any)._name} → destroy`)
    _clearAllCanvas(container)
    destroyChart()
  }

  if (!chartInstance) {
    console.log(`[ChartComponent] creating new instance: type=${params.type}`)
    chartInstance = getChartInstance(params.type, chartId.value)
    if (chartInstance) {
      console.log(`[ChartComponent] instance created: class=${chartInstance.constructor.name} _name=${(chartInstance as any)._name}`)
    }
  }

  if (!typeChanged && _currentType === undefined) {
    _currentType = params.type
    console.log(`[ChartComponent] first render, _currentType = ${params.type}`)
  }

  if (!chartInstance) {
    console.warn(
      `[ChartComponent] Unsupported chart type: "${params.type}". ` +
      `Falling back to empty render. Check chart_registry.py for supported types.`
    )
    return
  }

  chartInstance.showLabel = params.showLabel

  // 把当前 axis / 截断后的数据写回实例，统一走 applySettings 入口
  chartInstance.axis = axis.value
  chartInstance.data = displayData

  // 统一由子类 applySettings 完成：状态应用 → init → render
  _renderingType = params.type
  _lastRenderTick = tick
  try {
    await chartInstance.applySettings(settings ?? {})
  } catch (e) {
    console.warn('[ChartComponent] applySettings failed:', e)
  }
  // 异步渲染期间若类型变化，或已有更新的同类型渲染，丢弃本次结果
  if (_renderingType !== params.type || _lastRenderTick !== tick) {
    return
  }

  // ── 初始化失败处理（对标 Metabase checkRenderable 硬门禁）──
  const initOk = (chartInstance as any)._initOk ?? true
  console.log(`[ChartComponent] _doRender RESULT | type=${params.type} | initOk=${initOk} | axisCount=${axis.value?.length} | dataLen=${displayData?.length}`)
  if (!initOk) {
    // 尝试 checkRenderable 获取具体失败原因
    let errorMsg: string | undefined
    let suggestion: string | undefined
    try {
      checkRenderable(params.type, axis.value, displayData)
    } catch (e) {
      if (e instanceof ChartRenderError) {
        errorMsg = e.message
        suggestion = e.suggestion
      }
    }
    console.warn(
      `[ChartComponent] Chart init failed for type "${params.type}". ` +
      `reason: ${errorMsg || 'unknown'} | ` +
      `Axis:`, axis.value, `Data sample:`, displayData.slice(0, 3)
    )
    try {
      ;(chartInstance as any).chart?.clear?.()
    } catch {}
    _showFallbackHint(container, errorMsg, suggestion)
  }
}

/** 清理容器中所有 canvas 元素（G2 可能把 canvas 挂载到容器外） */
function _clearAllCanvas(container: HTMLElement) {
  const canvases = container.querySelectorAll('canvas')
  canvases.forEach((c) => c.remove())
  // 同时清理 G2 可能外挂的工具提示 DOM
  const tooltips = container.querySelectorAll('.g2-tooltip')
  tooltips.forEach((t) => t.remove())
}

function _showFallbackHint(container: HTMLElement, message?: string, suggestion?: string) {
  _clearAllCanvas(container)
  container.innerHTML = ''
  const wrapper = document.createElement('div')
  wrapper.style.cssText =
    'height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;' +
    'color:#8f959e;font-size:14px;padding:0 24px;text-align:center;background:#f5f6f7;gap:8px;'

  const msg = document.createElement('div')
  msg.style.cssText = 'max-width:400px;line-height:1.6;'
  msg.textContent = message || '当前数据不适合该图表类型，请尝试切换到「明细表」或其他图表类型。'
  wrapper.appendChild(msg)

  if (suggestion) {
    const sug = document.createElement('div')
    sug.style.cssText = 'max-width:400px;line-height:1.5;font-size:13px;color:#b0b3b8;'
    sug.textContent = `💡 ${suggestion}`
    wrapper.appendChild(sug)
  }

  container.appendChild(wrapper)
}

watch(
  () => params.showLabel,
  () => {
    renderChart(_lastSettings)
  }
)

// 监听图表类型与轴/数据变化，确保齿轮面板修改轴字段或外部切换类型后能自动重绘
watch(
  () => params.type,
  () => renderChart(_lastSettings)
)
watch(
  () => params.x,
  () => renderChart(_lastSettings),
  { deep: true }
)
watch(
  () => params.y,
  () => renderChart(_lastSettings),
  { deep: true }
)
watch(
  () => params.series,
  () => renderChart(_lastSettings),
  { deep: true }
)
watch(
  () => params.data,
  () => renderChart(_lastSettings)
)

function destroyChart() {
  if (chartInstance) {
    chartInstance.destroy()
    chartInstance = undefined
  }
  _currentType = undefined
  _renderingType = undefined
  _lastRenderTick = 0

  // 额外清理容器 DOM，防止 table S2 或 G2 Canvas 残留
  const container = document.getElementById(chartId.value)
  if (container) {
    container.innerHTML = ''
  }
}

function getExcelData() {
  return {
    axis: axis.value,
    data: params.data,
  }
}

useEmitt({
  name: 'view-render-all',
  callback: () => renderChart(_lastSettings),
})

useEmitt({
  name: `view-render-${params.id}`,
  callback: () => renderChart(_lastSettings),
})

function applySettings(settings: Record<string, any>) {
  // 统一走 renderChart 调度，避免外部直接调用与调度中的渲染竞态
  renderChart(settings)
}

defineExpose({
  renderChart,
  destroyChart,
  getExcelData,
  applySettings,
})

onMounted(() => {
  nextTick(() => {
    const settings = params.settings
    renderChart(settings && Object.keys(settings).length > 0 ? settings : undefined)
  })
})

onUnmounted(() => {
  destroyChart()
})
</script>

<template>
  <div v-if="renderError" class="chart-error-boundary">
    <div class="chart-error-icon">⚠️</div>
    <div class="chart-error-msg">{{ renderError }}</div>
    <div class="chart-error-hint">请尝试切换到其他图表类型或刷新页面</div>
  </div>
  <div v-else :id="chartId" class="chart-container"></div>
</template>

<style scoped lang="less">
.chart-container {
  height: 100%;
  width: 100%;
}
.chart-error-boundary {
  height: 100%;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: #f5f6f7;
  color: #8f959e;
  font-size: 14px;
  text-align: center;
  padding: 24px;
  .chart-error-icon { font-size: 32px; }
  .chart-error-msg { max-width: 400px; line-height: 1.6; }
  .chart-error-hint { font-size: 13px; color: #b0b3b8; }
}
</style>
