<script setup lang="ts">
import type { ChatMessage } from '@/api/chat.ts'
import DisplayChartBlock from '@/views/chat/component/DisplayChartBlock.vue'
import ChartPopover from '@/views/chat/chat-block/ChartPopover.vue'
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useClipboard } from '@vueuse/core'
import { concat } from 'lodash-es'
import {
  useChartTypeConfig,
  getChartTypeList,
  getToolbarButtons,
  type ToolbarButton as ToolbarBtn,
} from '@/views/chat/component/useChartTypeConfig.ts'
import type { ChartTypes } from '@/views/chat/component/BaseChart.ts'

// ── 图表类型 & 设置图标（直接 import，与旧代码一致，避免模块顶层调用函数导致
//    生产构建中模块初始化顺序问题 → "M is not a function"）──
import ICON_TABLE from '@/assets/svg/chart/icon_form_outlined.svg'
import ICON_SETTINGS from '@/assets/svg/icon_style-set_outlined.svg'

// ── 功能性图标（非图表类型/非 settings）──
import icon_sql_outlined from '@/assets/svg/icon_sql_outlined.svg'
import icon_export_outlined from '@/assets/svg/icon_export_outlined.svg'
import icon_file_image_colorful from '@/assets/svg/icon_file-image_colorful.svg'
import icon_file_excel_colorful from '@/assets/svg/icon_file-excel_colorful.svg'
import icon_into_item_outlined from '@/assets/svg/icon_into-item_outlined.svg'
import icon_window_max_outlined from '@/assets/svg/icon_window-max_outlined.svg'
import icon_window_mini_outlined from '@/assets/svg/icon_window-mini_outlined.svg'
import icon_copy_outlined from '@/assets/svg/icon_copy_outlined.svg'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import SQLComponent from '@/views/chat/component/SQLComponent.vue'
import { useAssistantStore } from '@/stores/assistant'
import AddViewDashboard from '@/views/dashboard/common/AddViewDashboard.vue'
import html2canvas from 'html2canvas'
import { chatApi } from '@/api/chat'
import { useChatConfigStore } from '@/stores/chatConfig.ts'
import { migrateSettings } from '@/views/chat/component/SettingsMigration'
import { scoreChartCompatibility } from '@/views/chat/component/ChartCompatibility'

const chatConfig = useChatConfigStore()
const showSQLBtn = chatConfig.getShowSQL
const props = withDefaults(
  defineProps<{
    recordId?: number
    message: ChatMessage
    isPredict?: boolean
    chatType?: ChartTypes
    enlarge?: boolean
    loadingData?: boolean
  }>(),
  {
    recordId: undefined,
    isPredict: false,
    chatType: undefined,
    enlarge: false,
    loadingData: false,
  }
)

const { copy } = useClipboard({ legacy: true })
const loading = ref<boolean>(false)
const { t } = useI18n()
const addViewRef = ref(null)
const emits = defineEmits(['exitFullScreen'])

const dataObject = computed<{
  fields: Array<string>
  data: Array<{ [key: string]: any }>
  limit: number | undefined
  datasource: number | undefined
  sql: string | undefined
}>(() => {
  if (props.message?.record?.data) {
    if (typeof props.message?.record?.data === 'string') {
      return JSON.parse(props.message.record.data)
    } else {
      return props.message.record.data
    }
  }
  return {}
})
const assistantStore = useAssistantStore()
const isCompletePage = computed(() => !assistantStore.getAssistant || assistantStore.getEmbedded)

const isAssistant = computed(() => assistantStore.getAssistant)

const chartId = computed(() => props.message?.record?.id + (props.enlarge ? '-fullscreen' : ''))

const data = computed(() => {
  if (props.isPredict) {
    let _list = []
    if (
      props.message?.record?.predict_data &&
      typeof props.message?.record?.predict_data === 'string'
    ) {
      if (
        props.message?.record?.predict_data.length > 0 &&
        props.message?.record?.predict_data.trim().startsWith('[') &&
        props.message?.record?.predict_data.trim().endsWith(']')
      ) {
        try {
          _list = JSON.parse(props.message?.record?.predict_data)
        } catch (e) {
          console.error(e)
        }
      }
    } else {
      if (props.message?.record?.predict_data?.length > 0) {
        _list = props.message?.record?.predict_data
      }
    }
    if (_list.length == 0) {
      return _list
    }

    if (dataObject.value.data && dataObject.value.data?.length > 0) {
      return concat(dataObject.value.data, _list)
    }
    return _list
  } else {
    return dataObject.value.data
  }
})

const chartRef = ref()

const chartObject = computed<{
  type: ChartTypes
  title: string
  axis: {
    x: { name: string; value: string }
    y: { name: string; value: string }
    series: { name: string; value: string }
  }
  columns: Array<{ name: string; value: string }>
}>(() => {
  if (props.message?.record?.chart) {
    return JSON.parse(props.message.record.chart)
  }
  return {}
})

// ── 图表类型状态 ──
// sourceType: AI 原始选择的类型，响应式跟随 chartObject 变化（数据异步加载）
const sourceType = computed(() => chartObject.value.type as ChartTypes | undefined)

const currentChartType = ref<ChartTypes | undefined>(
  props.chatType ?? sourceType.value ?? 'table'
)

const chartType = computed<ChartTypes>({
  get() {
    if (currentChartType.value) {
      return currentChartType.value
    }
    return props.chatType ?? sourceType.value ?? 'table'
  },
  set(v) {
    currentChartType.value = v
  },
})

// 标记用户是否手动切换过图表类型；手动切换后不再自动纠正
const hasUserManuallySwitched = ref(false)

// 当消息/数据变化时重置手动切换标记，允许对新数据做自动纠正
watch(
  () => props.message?.record?.id,
  () => {
    hasUserManuallySwitched.value = false
  }
)

const { config } = useChartTypeConfig()

// → 从 composable 计算类型切换列表（以 sourceType 为基线，不受用户切换影响）
const baseChartTypeList = getChartTypeList(config, sourceType)

// → 注入兼容性评分，按分数降序排列（对标 Metabase getSensibleDisplays）
const chartTypeList = computed(() => {
  if (!data.value || data.value.length === 0) return baseChartTypeList.value
  const cols = chartObject.value?.columns?.length > 0
    ? chartObject.value.columns
    : Object.keys(data.value[0] || {}).map((k) => ({ name: k, value: k }))
  const scores = scoreChartCompatibility(data.value, cols)
  const scoreMap = new Map(scores.map((s) => [s.type, s]))
  return baseChartTypeList.value.map((item) => {
    const s = scoreMap.get(item.value)
    return {
      ...item,
      score: s?.score,
      reasons: s?.reasons,
    }
  })
})

// → 判断当前数据是否包含可用于分组的维度列（用于 show_when 条件）
const hasSeries = computed(() =>
  chartObject.value?.columns?.some((c) => !isNumericColumn(c.value)) ?? false
)

// → 从 composable 计算所有设置按钮（统一收入齿轮 popover，支持 show_when 条件过滤）
const { allSettings } = getToolbarButtons(config, currentChartType, hasSeries)

// 设置项分组配置
const SETTING_GROUPS = [
  { key: 'style', label: '样式', keys: ['color_palette', 'smooth', 'show_area', 'show_points', 'donut'], defaultExpanded: false },
  { key: 'label', label: '标签与图例', keys: ['show_label', 'label_format', 'legend'], defaultExpanded: false },
  { key: 'data', label: '数据与排序', keys: ['sort', 'stack', 'y_axis_zero', 'number_format'], defaultExpanded: true },
  { key: 'table', label: '表格设置', keys: ['sort_column', 'sort_order', 'page_size'], defaultExpanded: true },
]

const expandedGroups = reactive<Record<string, boolean>>({})
SETTING_GROUPS.forEach((g) => { expandedGroups[g.key] = g.defaultExpanded })

// 判断字段是否为数值列（抽样前 20 行，≥80% 非空值可转为数字）
function isNumericColumn(field: string): boolean {
  const rows = data.value || []
  if (rows.length === 0) return false
  let numericCount = 0
  let nonEmptyCount = 0
  const sample = rows.slice(0, 20)
  for (const row of sample) {
    const v = row[field]
    if (v === null || v === undefined || v === '') continue
    nonEmptyCount++
    const s = String(v).replace(/[,%]/g, '').trim()
    if (s !== '' && !isNaN(Number(s))) numericCount++
  }
  return nonEmptyCount > 0 && numericCount / nonEmptyCount >= 0.8
}

// → 对 select 类型设置项动态填充 options（schema 无法预先知道列名）
const displaySettings = computed<ToolbarBtn[]>(() => {
  const columns = chartObject.value?.columns ?? []
  const allColumnNames = columns.map((c) => c.name)

  return allSettings.value.map((btn) => {
    if (btn.type !== 'select') return btn
    if (btn.key === 'sort_column' && currentChartType.value === 'table') {
      return { ...btn, options: allColumnNames }
    }
    return btn
  })
})

// 按功能对设置项分组，减少齿轮面板视觉压力
const groupedDisplaySettings = computed(() => {
  const btnMap = new Map(displaySettings.value.map((b) => [b.key, b]))
  return SETTING_GROUPS.map((g) => ({
    ...g,
    buttons: g.keys.map((k) => btnMap.get(k)).filter(Boolean) as ToolbarBtn[],
  })).filter((g) => g.buttons.length > 0)
})

// ── 持久化（按图表类型隔离）──
const LS_KEY = 'sqlbot_chart_settings'

function loadStoredSettings(): Record<string, Record<string, any>> {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    // 兼容旧格式：扁平对象 → 按当前类型分组
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      const typeKeys = Object.keys(parsed)
      // 已知的图表类型 key（不依赖异步加载的 config）
      const knownTypes = ['table', 'column', 'bar', 'line', 'pie']
      // 如果没有任何 key 是已知图表类型，则视为旧格式扁平对象
      if (!typeKeys.some(k => knownTypes.includes(k))) {
        const currentType = currentChartType.value || sourceType.value || 'table'
        return { [currentType]: parsed }
      }
      // 对每个类型的 settings 运行迁移
      for (const type of Object.keys(parsed)) {
        if (typeof parsed[type] === 'object') {
          parsed[type] = migrateSettings(parsed[type], type)
        }
      }
      return parsed
    }
    return {}
  } catch { return {} }
}

function persistSettings() {
  try { localStorage.setItem(LS_KEY, JSON.stringify(chartSettings)) } catch {}
}

const chartSettings = reactive<Record<string, Record<string, any>>>(loadStoredSettings())

/** 获取当前图表类型的 settings 分组（自动创建） */
function currentTypeSettings(): Record<string, any> {
  const type = currentChartType.value
  if (!type) return {}
  if (!chartSettings[type]) chartSettings[type] = {}
  return chartSettings[type]
}

// 从 schema 补齐默认值（只补当前类型未设置的 key）
function initSettingsFromDefaults(typeId: string) {
  if (!config.value) return
  try {
    const schema = config.value.chartTypes[typeId]?.settingsSchema
    if (!schema) return
    const settings = currentTypeSettings()
    for (const [key, s] of Object.entries(schema) as [string, any][]) {
      if (!(key in settings)) {
        settings[key] = s.default
      }
    }
  } catch (e) {
    console.warn('[ChartBlock] initSettingsFromDefaults failed:', e)
  }
}

/**
 * 获取当前图表类型有效的 settings（按类型隔离 + 按 schema 过滤）
 *
 * 解决的核心问题:
 *   1. 同名设置在不同类型下默认值不同（如 pie.show_label=true vs column.show_label=false），
 *      全局共享会导致切换类型后默认值互相污染。
 *   2. 某类型专属设置（如 donut）会残留到另一类型中。
 *
 * 方案:
 *   - 按图表类型隔离 settings（chartSettings[typeId][key]）
 *   - 只返回当前类型 schema 中定义的 key
 */
function getEffectiveSettings(): Record<string, any> {
  const currentType = currentChartType.value
  if (!currentType || !config.value) return {}

  const schema = config.value.chartTypes[currentType]?.settingsSchema
  if (!schema) return {}

  const settings = currentTypeSettings()
  const result: Record<string, any> = {}
  for (const key of Object.keys(schema)) {
    if (key in settings) {
      result[key] = settings[key]
    }
  }
  return result
}

// ── 状态判断与切换 ──

/** 按钮高亮：bool=当前为true；select=与默认值不同 */
function isSettingActive(btn: ToolbarBtn): boolean {
  const settings = currentTypeSettings()
  const val = settings[btn.key] !== undefined ? settings[btn.key] : btn.default
  if (btn.type === 'bool') return !!val
  return val !== btn.default
}

function effectiveValue(btn: ToolbarBtn): any {
  const settings = currentTypeSettings()
  return settings[btn.key] !== undefined ? settings[btn.key] : btn.default
}

/** gear popover 是否至少有一个设置处于激活状态 → 齿轮图标高亮 */
const hasActiveSettings = computed(() => allSettings.value.some(b => isSettingActive(b)))

const gearVisible = ref(false)

// 图表类型切换防抖：快速连续点击时只以最后一次选择触发渲染，避免中间实例造成空白
let _typeSwitchTimer: any = null

// 设置项变更防抖：同一轮交互中多次点击设置只保留最后一次渲染
let _settingsApplyTimer: any = null

function toggleSetting(btn: ToolbarBtn) {
  const settings = currentTypeSettings()
  if (btn.type === 'bool') {
    settings[btn.key] = !effectiveValue(btn)
  }
  persistSettings()
  applyAllSettings()
}

function selectGearSetting(btn: ToolbarBtn, val: any) {
  const settings = currentTypeSettings()
  settings[btn.key] = val
  persistSettings()
  applyAllSettings()
}

function applyAllSettings() {
  if (_settingsApplyTimer) clearTimeout(_settingsApplyTimer)
  _settingsApplyTimer = setTimeout(() => {
    _settingsApplyTimer = null
    nextTick(() => {
      try {
        chartRef.value?.applySettings?.(getEffectiveSettings())
      } catch (e) {
        console.warn('[ChartBlock] applyAllSettings failed:', e)
      }
    })
  }, 50)
}

// ── 原有逻辑 ──

function changeTable() {
  onTypeChange('table')
}

function setChartType(val: ChartTypes, fromAutoCorrection = false) {
  console.log(`[ChartBlock] setChartType: ${chartType.value} → ${val} | auto=${fromAutoCorrection} | hasData=${data.value?.length > 0}`)

  if (!fromAutoCorrection) {
    hasUserManuallySwitched.value = true
  }

  // 兼容性检查：对标 Metabase isSensible — 软门禁，只告警不阻止
  // 真正的硬阻断由渲染层 _initOk=false → fallback hint 承担
  if (chartRef.value?.canRenderChartType && data.value?.length > 0) {
    const compatibility = chartRef.value.canRenderChartType(val)
    console.log(`[ChartBlock] canRenderChartType(${val}):`, compatibility)
    if (compatibility && !compatibility.ok) {
      console.warn(`[ChartBlock] 类型 ${val} 可能不适合当前数据：${compatibility.reason}`)
      ElMessage.warning(`当前数据可能不适合「${val}」图表：${compatibility.reason}`)
      // 不 return！允许切换，让渲染层做兜底处理
    }
  }

  // 不手动 destroyChart！由 _doRender 根据 _currentType 判断是否走原地切换。
  // 手动 destroy 会清掉 _currentType，导致笛卡尔原地切换逻辑被跳过。
  console.log(`[ChartBlock] setting chartType = ${val} (no manual destroy)`)
  chartType.value = val
  initSettingsFromDefaults(val)

  // 防抖渲染：连续切换时只保留最后一次
  if (_typeSwitchTimer) clearTimeout(_typeSwitchTimer)
  _typeSwitchTimer = setTimeout(() => {
    _typeSwitchTimer = null
    console.log(`[ChartBlock] debounced onTypeChange firing | type=${val} | settings=`, getEffectiveSettings())
    chartRef.value?.onTypeChange(getEffectiveSettings())
  }, 120)
}

function onTypeChange(val: any) {
  setChartType(val, false)
}

function reloadChart() {
  chartRef.value?.onTypeChange()
}

// 当 AI 推荐的图表类型与数据/问题严重不匹配时自动纠正
watch(
  sourceType,
  () => {
    nextTick(() => {
      if (hasUserManuallySwitched.value) return
      const recommended = chartRef.value?.getRecommendedChartType?.()
      if (!recommended || recommended === chartType.value) return

      const current = chartType.value
      // 严重误用场景：line 画分类对比 → column/bar；pie 画对比/趋势 → column/bar；
      // column/bar 画时间趋势（数据缺少分类维度）→ line。
      // table 是通用兜底，不自动纠正，避免用户想查看明细表时被强制切走。
      const isSevereMismatch =
        (current === 'line' && (recommended === 'column' || recommended === 'bar')) ||
        (current === 'pie' && (recommended === 'column' || recommended === 'bar')) ||
        ((current === 'column' || current === 'bar') && recommended === 'line')

      if (isSevereMismatch) {
        console.warn(`[ChartBlock] 图表类型自动纠正：${current} → ${recommended}`)
        setChartType(recommended, true)
      }
    })
  },
  { immediate: true }
)

const dialogVisible = ref(false)

function setHiddenSidebarBtnZIndex(value: string) {
  const sidebarBtns = document.querySelectorAll('.hidden-sidebar-btn')
  sidebarBtns.forEach((btn) => {
    ;(btn as HTMLElement).style.zIndex = value
  })
}

function openFullScreen() {
  setHiddenSidebarBtnZIndex('0')
  dialogVisible.value = true
}

function closeFullScreen() {
  emits('exitFullScreen')
}

function onExitFullScreen() {
  dialogVisible.value = false
  setHiddenSidebarBtnZIndex('11')
}

const sqlShow = ref(false)

function showSql() {
  sqlShow.value = true
}

const showLabel = ref(false)

function addToDashboard() {
  // 使用 DisplayChartBlock.getViewInfo() 获取图表信息
  // 它会自动处理：① 用户切换后的最新图表类型 ② 根据图表类型重新推导坐标轴
  const viewInfo = chartRef.value?.getViewInfo?.()
  const recordeInfo = {
    id: '1-1',
    data: viewInfo?.data ?? { data: data.value },
    sql: props.message?.record?.sql,
    datasource: props.message?.record?.datasource,
    chart: viewInfo?.chart ?? {},
  }
  // 保存用户当前的图表设置（图注、网格、标签、配色等），
  // 确保仪表盘中的图表与聊天中看到的完全一致
  if (recordeInfo.chart) {
    recordeInfo.chart.settings = getEffectiveSettings()
  }
  // @ts-expect-error eslint-disable-next-line @typescript-eslint/ban-ts-comment
  addViewRef.value?.optInit(recordeInfo)
}

function copyText() {
  if (props.message?.record?.sql) {
    copy(props.message.record.sql).then(() => {
      ElMessage.success(t('embedded.copy_successful'))
    })
  }
}

const exportRef = ref()

function exportToExcel() {
  if (chartRef.value && props.recordId) {
    loading.value = true
    chatApi
      .export2Excel(props.recordId, props.message?.record?.chat_id || 0)
      .then((res) => {
        const blob = new Blob([res], {
          type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        })
        const link = document.createElement('a')
        link.href = URL.createObjectURL(blob)
        link.download = `${chartObject.value.title ?? 'Excel'}.xlsx`
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
      })
      .catch(async (error) => {
        if (error.response) {
          try {
            let text = await error.response.data.text()
            try {
              text = JSON.parse(text)
            } finally {
              ElMessage({
                message: text,
                type: 'error',
                showClose: true,
              })
            }
          } catch (e) {
            console.error('Error processing error response:', e)
          }
        } else {
          console.error('Other error:', error)
          ElMessage({
            message: error,
            type: 'error',
            showClose: true,
          })
        }
      })
      .finally(() => {
        loading.value = false
      })
    exportRef.value?.hide()
  }
}

function exportToImage() {
  const obj = document.getElementById('chart-component-' + chartId.value)
  if (obj) {
    html2canvas(obj).then((canvas) => {
      canvas.toBlob(function (blob) {
        if (blob) {
          const link = document.createElement('a')
          link.download = (chartObject.value.title ?? 'chart') + '.png'
          link.href = URL.createObjectURL(blob)
          document.body.appendChild(link)
          link.click()
          document.body.removeChild(link)
          URL.revokeObjectURL(link.href)
        }
      }, 'image/png')
    })
  }
  exportRef.value?.hide()
}

defineExpose({
  reloadChart,
})

watch(
  () => chartObject.value?.type,
  (val) => {
    if (val) {
      // 数据/类型变更时统一走 setChartType 路径（标记为自动纠正，不视为用户手动切换）
      nextTick(() => setChartType(val, true))
    }
  }
)

// 图表类型切换时：从 schema 补齐默认值
// 注意：不在此处调用 applyAllSettings()，渲染由 onTypeChange → renderChart 路径统一处理
// 避免同一轮类型切换触发多次 chart init（每次 init 对 Table 意味着 JSON 深克隆 + S2 重建）
watch(currentChartType, (newType) => {
  if (newType && config.value) {
    initSettingsFromDefaults(newType)
  }
})

// 首次挂载：等待 config 与图表实例就绪后初始化
onMounted(() => {
  const tryInit = () => {
    if (!config.value) { setTimeout(tryInit, 200); return }
    if (!chartRef.value) { setTimeout(tryInit, 100); return }
    const ct = currentChartType.value
    if (ct) {
      initSettingsFromDefaults(ct)
      applyAllSettings()
    }
  }
  tryInit()
})
</script>

<template>
  <div
    v-if="
      (!isPredict && (message?.record?.sql || message?.record?.chart)) ||
      (isPredict && message?.record?.chart && data.length > 0)
    "
    v-loading.fullscreen.lock="loading"
    class="chart-component-container"
    :class="{ 'full-screen': enlarge }"
  >
    <div class="header-bar">
      <div class="title">
        {{ chartObject.title }}
      </div>
      <div class="buttons-bar">
        <div class="chart-select-container">
          <el-tooltip effect="dark" :offset="8" :content="t('chat.type')" placement="top">
            <ChartPopover
              v-if="chartTypeList.length > 0"
              :chart-type-list="chartTypeList"
              :chart-type="chartType"
              :title="t('chat.type')"
              @type-change="onTypeChange"
            ></ChartPopover>
          </el-tooltip>

          <el-tooltip
            effect="dark"
            :offset="8"
            :content="t('chat.chart_type.table')"
            placement="top"
          >
            <el-button
              class="tool-btn"
              :class="{ 'chart-active': currentChartType === 'table' }"
              text
              @click="changeTable"
            >
              <el-icon size="16">
                <ICON_TABLE />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>

        <!-- 齿轮 → 统一图表设置（bool + select 全部收入 popover） -->
        <el-popover
          v-if="allSettings.length > 0"
          v-model:visible="gearVisible"
          trigger="click"
          popper-class="chart-gear-popover"
          placement="bottom"
        >
          <template #reference>
            <div>
              <el-tooltip effect="dark" :offset="8" content="图表设置" placement="top">
                <el-button
                  class="tool-btn"
                  :class="{ 'chart-active': hasActiveSettings }"
                  text
                >
                  <el-icon size="16">
                    <component :is="ICON_SETTINGS" />
                  </el-icon>
                </el-button>
              </el-tooltip>
            </div>
          </template>
          <div class="gear-popover">
            <div class="gear-popover-content">
              <div class="gear-title">图表设置</div>
              <div
                v-for="group in groupedDisplaySettings"
                :key="group.key"
                class="gear-group"
              >
                <div
                  class="gear-group-title"
                  @click="expandedGroups[group.key] = !expandedGroups[group.key]"
                >
                  <span>{{ group.label }}</span>
                  <span
                    class="gear-group-arrow"
                    :class="{ 'is-expanded': expandedGroups[group.key] }"
                  ></span>
                </div>
                <div v-show="expandedGroups[group.key]" class="gear-group-content">
                  <div
                    v-for="btn in group.buttons"
                    :key="btn.key"
                    class="gear-item"
                  >
                    <div class="gear-row">
                      <component :is="btn.icon" v-if="btn.icon" class="gear-item-icon" />
                      <span class="gear-label">{{ btn.label }}</span>
                    </div>
                    <!-- bool 类型：开关按钮 -->
                    <div v-if="btn.type === 'bool'" class="gear-toggle">
                      <span
                        class="gear-opt gear-opt-bool"
                        :class="{ 'gear-opt-active': effectiveValue(btn) === true }"
                        @click="toggleSetting(btn)"
                      >{{ effectiveValue(btn) ? '开' : '关' }}</span>
                    </div>
                    <!-- select 类型：选项列表 -->
                    <div v-else class="gear-options">
                      <span
                        v-for="opt in btn.options"
                        :key="opt"
                        class="gear-opt"
                        :class="{ 'gear-opt-active': effectiveValue(btn) === opt }"
                        @click="selectGearSetting(btn, opt)"
                      >{{ btn.optionLabels?.[opt] || opt }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </el-popover>

        <div v-if="message?.record?.sql && showSQLBtn">
          <el-tooltip effect="dark" :offset="8" :content="t('chat.show_sql')" placement="top">
            <el-button class="tool-btn" text @click="showSql">
              <el-icon size="16">
                <icon_sql_outlined />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>
        <div v-if="message?.record?.chart">
          <el-popover
            ref="exportRef"
            trigger="click"
            popper-class="export_to_select"
            placement="bottom"
          >
            <template #reference>
              <div>
                <el-tooltip
                  effect="dark"
                  :offset="8"
                  :content="t('chat.export_to')"
                  placement="top"
                >
                  <el-button class="tool-btn" text>
                    <el-icon size="16">
                      <icon_export_outlined />
                    </el-icon>
                  </el-button>
                </el-tooltip>
              </div>
            </template>
            <div class="popover">
              <div class="popover-content">
                <div class="title">{{ t('chat.export_to') }}</div>
                <div class="popover-item" @click="exportToExcel">
                  <el-icon size="16">
                    <icon_file_excel_colorful />
                  </el-icon>
                  <div class="model-name">{{ t('chat.excel') }}</div>
                </div>
                <div
                  v-if="currentChartType !== 'table'"
                  class="popover-item"
                  @click="exportToImage"
                >
                  <el-icon size="16">
                    <icon_file_image_colorful />
                  </el-icon>
                  <div class="model-name">{{ t('chat.picture') }}</div>
                </div>
              </div>
            </div>
          </el-popover>
        </div>
        <div v-if="message?.record?.chart && !isAssistant">
          <el-tooltip effect="dark" :content="t('chat.add_to_dashboard')" placement="top">
            <el-button class="tool-btn" text @click="addToDashboard">
              <el-icon size="16">
                <icon_into_item_outlined />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>
        <div class="divider" />
        <div v-if="!enlarge">
          <el-tooltip
            effect="dark"
            :offset="8"
            :content="!isCompletePage ? $t('common.zoom_in') : t('chat.full_screen')"
            placement="top"
          >
            <el-button class="tool-btn" text @click="openFullScreen">
              <el-icon size="16">
                <icon_window_max_outlined />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>
        <div v-else>
          <el-tooltip
            effect="dark"
            :offset="8"
            :content="!isCompletePage ? $t('common.zoom_out') : t('chat.exit_full_screen')"
            placement="top"
          >
            <el-button class="tool-btn" text @click="closeFullScreen">
              <el-icon size="16">
                <icon_window_mini_outlined />
              </el-icon>
            </el-button>
          </el-tooltip>
        </div>
      </div>
    </div>

    <template v-if="message?.record?.chart">
      <div class="chart-block">
        <DisplayChartBlock
          :id="chartId"
          ref="chartRef"
          :chart-type="chartType"
          :message="message"
          :data="data"
          :loading-data="loadingData"
          :show-label="showLabel"
        />
      </div>
      <div v-if="dataObject.limit" class="over-limit-hint">
        {{ t('chat.data_over_limit', [dataObject.limit]) }}
      </div>
    </template>

    <AddViewDashboard ref="addViewRef"></AddViewDashboard>
    <el-dialog
      v-if="!enlarge"
      v-model="dialogVisible"
      fullscreen
      :show-close="false"
      class="chart-fullscreen-dialog"
      header-class="chart-fullscreen-dialog-header"
      body-class="chart-fullscreen-dialog-body"
    >
      <ChartBlock
        v-if="dialogVisible"
        :message="message"
        :record-id="recordId"
        :is-predict="isPredict"
        :chat-type="chartType"
        :loading-data="loadingData"
        enlarge
        @exit-full-screen="onExitFullScreen"
      />
    </el-dialog>

    <el-drawer
      v-model="sqlShow"
      :size="!isCompletePage ? '100%' : '600px'"
      :title="t('chat.show_sql')"
      direction="rtl"
      body-class="chart-sql-drawer-body"
    >
      <div class="sql-block">
        <SQLComponent
          v-if="message.record?.sql"
          :sql="message.record?.sql"
          style="margin-top: 12px"
        />
        <el-button v-if="message.record?.sql" circle class="input-icon" @click="copyText">
          <el-icon size="16">
            <icon_copy_outlined />
          </el-icon>
        </el-button>
      </div>
    </el-drawer>
  </div>
</template>

<style lang="less">
.chart-fullscreen-dialog {
  padding: 0;
}

.chart-fullscreen-dialog-header {
  display: none;
}

.chart-fullscreen-dialog-body {
  padding: 0;
  height: 100%;
}

.chart-sql-drawer-body {
  padding: 24px;
}

.export_to_select.export_to_select {
  padding: 4px 0;
  width: 120px !important;
  min-width: 120px !important;
  box-shadow: 0px 4px 8px 0px #1f23291a;
  border: 1px solid #dee0e3;

  .popover {
    .popover-content {
      padding: 0 4px;
      max-height: 300px;
      overflow-y: auto;

      .title {
        width: 100%;
        height: 32px;
        margin-bottom: 2px;
        display: flex;
        align-items: center;
        padding-left: 8px;
        color: #8f959e;
      }
    }

    .popover-item {
      height: 32px;
      display: flex;
      align-items: center;
      padding-left: 12px;
      padding-right: 8px;
      margin-bottom: 2px;
      position: relative;
      border-radius: 6px;
      cursor: pointer;

      &:last-child {
        margin-bottom: 0;
      }

      &:hover {
        background: #1f23291a;
      }

      .model-name {
        margin-left: 8px;
        font-weight: 400;
        font-size: 14px;
        line-height: 22px;
        max-width: 220px;
      }

      .done {
        margin-left: auto;
        display: none;
      }

      &.isActive {
        color: var(--ed-color-primary);

        .done {
          display: block;
        }
      }
    }
  }
}

// ── 齿轮设置 popover ──
.chart-gear-popover.chart-gear-popover {
  padding: 4px 0;
  min-width: 180px;
  box-shadow: 0px 4px 8px 0px #1f23291a;
  border: 1px solid #dee0e3;

  .gear-popover {
    .gear-popover-content {
      padding: 0 4px;
      max-height: 360px;
      overflow-y: auto;

      .gear-title {
        width: 100%;
        height: 32px;
        margin-bottom: 2px;
        display: flex;
        align-items: center;
        padding-left: 8px;
        color: #8f959e;
        font-size: 12px;
      }

      .gear-group {
        margin-bottom: 8px;

        &:last-child {
          margin-bottom: 0;
        }

        .gear-group-title {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: 28px;
          padding: 0 12px;
          margin-bottom: 4px;
          font-size: 12px;
          color: #1f2329;
          font-weight: 500;
          cursor: pointer;
          border-radius: 4px;

          &:hover {
            background: #f2f3f5;
          }

          .gear-group-arrow {
            width: 0;
            height: 0;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid #8f959e;
            transition: transform 0.2s;

            &.is-expanded {
              transform: rotate(180deg);
            }
          }
        }

        .gear-group-content {
          padding: 0 4px;
        }
      }

      .gear-item {
        padding: 6px 12px;
        margin-bottom: 4px;

        .gear-row {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 6px;

          .gear-item-icon {
            width: 14px;
            height: 14px;
            flex-shrink: 0;
            color: #8f959e;
          }
        }

        .gear-label {
          font-size: 12px;
          color: #8f959e;
        }

        .gear-toggle {
          display: flex;
        }

        .gear-options {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        .gear-opt {
          display: inline-block;
          padding: 2px 10px;
          font-size: 12px;
          line-height: 22px;
          border-radius: 4px;
          cursor: pointer;
          background: #f2f3f5;
          color: #1f2329;
          transition: all 0.15s;
          user-select: none;

          &:hover {
            background: #e3e5e8;
          }

          &.gear-opt-active {
            background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
            color: var(--ed-color-primary, rgba(28, 186, 144, 1));
          }

          &.gear-opt-bool {
            min-width: 36px;
            text-align: center;
          }
        }
      }
    }
  }
}
</style>
<style scoped lang="less">
.chart-component-container {
  width: 100%;
  padding: 16px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(222, 224, 227, 1);
  border-radius: 12px;

  &.full-screen {
    border: unset;
    border-radius: unset;
    padding: 0;
    height: 100%;

    .header-bar {
      border-bottom: 1px solid rgba(31, 35, 41, 0.15);
      height: 55px;
      padding: 16px 24px;
    }

    .chart-block {
      margin: unset;
      padding: 16px;
      height: calc(100% - 56px);
    }
  }

  .header-bar {
    height: 32px;
    display: flex;

    align-items: center;
    flex-direction: row;
    gap: 16px;

    .tool-btn {
      width: 24px;
      height: 24px;

      font-size: 16px;
      font-weight: 400;
      line-height: 24px;
      border-radius: 6px;
      color: rgba(100, 106, 115, 1);

      .tool-btn-inner {
        display: flex;
        flex-direction: row;
        align-items: center;
      }

      &:hover {
        background: rgba(31, 35, 41, 0.1);
      }

      &:active {
        background: rgba(31, 35, 41, 0.1);
      }
    }

    .chart-active {
      background: var(--ed-color-primary-1a, rgba(28, 186, 144, 0.1));
      color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      border-radius: 6px;

      :deep(.ed-select__wrapper) {
        background: transparent;
      }

      :deep(.ed-select__input) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }

      :deep(.ed-select__placeholder) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }

      :deep(.ed-select__caret) {
        color: var(--ed-color-primary, rgba(28, 186, 144, 1));
      }
    }

    .title {
      flex: 1;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;

      color: rgba(31, 35, 41, 1);
      font-weight: 500;
      font-size: 16px;
      line-height: 24px;
    }

    .buttons-bar {
      display: flex;
      flex-direction: row;
      align-items: center;

      gap: 16px;

      .divider {
        width: 1px;
        height: 16px;
        border-left: 1px solid rgba(31, 35, 41, 0.15);
      }
    }

    .chart-select-container {
      padding: 3px;
      display: flex;
      flex-direction: row;
      gap: 4px;
      border-radius: 6px;

      border: 1px solid rgba(217, 220, 223, 1);

      .chart-select {
        min-width: 40px;
        width: 40px;
        height: 24px;

        :deep(.ed-select__wrapper) {
          padding: 4px;
          min-height: 24px;
          box-shadow: unset;
          border-radius: 6px;

          &:hover {
            background: rgba(31, 35, 41, 0.1);
          }

          &:active {
            background: rgba(31, 35, 41, 0.1);
          }
        }

        :deep(.ed-select__caret) {
          font-size: 12px !important;
        }
      }
    }
  }

  .chart-block {
    height: 352px;
    width: 100%;

    margin-top: 16px;
  }
  .over-limit-hint {
    min-height: 24px;
    line-height: 24px;
    font-size: 14px;
  }
}

.sql-block {
  position: relative;

  .input-icon {
    min-width: unset;
    position: absolute;
    top: 12px;
    right: 12px;
    color: #1f2329;
    display: none;
    background-color: transparent !important;

    border-color: #dee0e3;
    box-shadow: 0px 4px 8px 0px #1f23291a;

    &:hover,
    &:focus {
      color: var(--ed-color-primary);
    }

    &:active {
      color: var(--ed-color-primary-dark-2);
    }
  }

  &:hover {
    .input-icon {
      display: flex;
    }
  }
}
</style>
