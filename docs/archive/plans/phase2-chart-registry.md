# 阶段2 开发文档：图表类型配置化改造

> 编写日期: 2026-07-16
> 状态: 设计完成，待实施
> 依赖: 阶段1/1.5 完成后执行
> 参考: Metabase 可插拔可视化架构 (defineConfig / checkRenderable / per-type settings)

---

## 一、问题

新增一种图表类型需改动 **6 处注册代码**（不含渲染代码和 i18n）：

| # | 文件 | 改动 | 性质 |
|---|------|------|------|
| 1 | `executor.py:728-733` | if/elif 关键词链追加 | 后端 |
| 2 | `BaseChart.ts:13` | ChartTypes 联合类型追加字面量 | 前端 |
| 3 | `component/index.ts` | import + CHART_TYPE_MAP 加条目 | 前端 |
| 4 | `ChartBlock.vue:153-181` | switch 加 case | 前端 |
| 5 | `sq-view/index.vue:51-79` | **相同 switch 的 copy-paste** | 前端 |
| 6 | `g2-ssr/app.js:36-45` | switch 加 case | SSR |

加上渲染文件和 i18n，实际 **10+ 处改动**。此外还有三个设计问题：

- **关键词是子串扫描**，`"不要用饼图"` 命中 `"饼图"`，"扇形"命中 pie，bar/table 没有关键词分支
- **SQL 生成阶段 LLM 的 chart-type 选择被浪费**，`_generate_chart()` 不用这个值，自己重新扫关键词
- **所有图表共享同一个 axis 结构**，pie 不需要 x 轴，line 可以配置平滑/面积填充，但这些差异无法在 JSON 中表达

---

## 二、设计原则

| 原则 | 说明 |
|------|------|
| **单一配置源** | `chart_registry.py` 是唯一需要修改来新增图表类型的文件 |
| **一次定义，多处消费** | 每个 ChartTypeDef 的字段同时供给：LLM 提示词、executor 关键词匹配、前端 API、工具栏渲染、渲染类应用 |
| **数据形状感知** | 约束注入 LLM 提示词供自检（非运行时拦截——SQLBot 是 LLM 选类型，不是用户选） |
| **settings_schema 一石三鸟** | 同一个 schema：① 注入 LLM 提示词引导生成 ② 下发前端驱动工具栏按钮 ③ 渲染类按 key 映射到 G2/S2 选项 |

---

## 三、核心设计

### 3.1 单一配置源：chart_registry.py

```
                     ┌────────────────────────────────────────┐
                     │          chart_registry.py              │
                     │         (加新图表类型只改这里)            │
                     │                                         │
                     │  CHART_TYPE_REGISTRY = {                │
                     │    "pie": ChartTypeDef(                 │
                     │      type_id, keywords, display_name,   │
                     │      category, selection_rule,          │
                     │      data_constraints,                  │
                     │      settings_schema,  ← 结构化 dict    │
                     │      compatible_with,                   │
                     │      base_class, has_ssr, uses_axis,    │
                     │    ),                                   │
                     │  }                                      │
                     └──┬──────────┬───────────────┬──────────┘
                        │          │               │
        ┌───────────────┘          │               └──────────────┐
        ▼                          ▼                              ▼
  后端消费者                   前端 API                       前端渲染
  ┌──────────────────┐   ┌──────────────────┐   ┌──────────────────────────┐
  │ executor.py      │   │ GET /api/chat/   │   │ ChartBlock.vue           │
  │ 关键词+数据形状→  │   │   chart-types    │   │ compatibleWith → 类型切换 │
  │ chart_type hint  │   │                  │   │ settingsSchema → 工具栏   │
  ├──────────────────┤   │ 返回:            │   │ dataConstraints → 灰显    │
  │ template.yaml    │   │ - chartTypes{}   │   ├──────────────────────────┤
  │ {supported_types}│   │ - allTypeIds[]   │   │ component/index.ts       │
  │ {selection_rules}│   │ - categories{}   │   │ import.meta.glob()       │
  │ {settings_schema}│   │ - defaultType    │   │ → 自动发现 charts/*.ts   │
  ├──────────────────┤   └──────────────────┘   ├──────────────────────────┤
  │ g2-ssr/app.js    │                           │ charts/Bar.ts 等 5 个类  │
  │ 约定派发          │                           │ applySettings(settings)  │
  └──────────────────┘                           │ → G2 options 映射        │
                                                 └──────────────────────────┘
```

### 3.2 ChartTypeDef 字段

```python
@dataclass
class ChartTypeDef:
    # ── 标识（全部消费者）──
    type_id: str                          # "pie"
    keywords: List[str]                   # 用户意图信号
    display_name_cn: str                  # 替代 i18n 硬编码
    display_name_en: str
    icon: str

    # ── 视觉分类 ──
    category: str                         # trend | comparison | proportion | table

    # ── LLM 提示词素材 ──
    selection_rule_cn: str                # "占比用 pie（≤10类，恰好1个数值字段）"
    selection_rule_en: str
    data_constraints: dict                # {min_metrics,max_metrics,requires_series,…}

    # ── 类型专属配置（一石三鸟：LLM提示词 + 前端工具栏 + 渲染类G2映射）──
    settings_schema: Dict[str, dict]
    # 结构化 dict，每个 setting 包含:
    #   desc_cn / desc_en  → 拼成 LLM 提示词文本
    #   type / default     → 前端按钮类型 (bool=toggle, select=循环) 和默认值
    #   icon / label_cn    → 前端工具栏按钮图标和文字
    #   options            → select 类型的选项列表

    # ── 运行时元数据 ──
    compatible_with: List[str]            # 可切换到的类型
    base_class: str = "BaseG2Chart"       # 前端 glob 校验继承链
    has_ssr: bool = True
    uses_axis: bool = True                # axis vs columns 结构
```

**刻意不纳入的字段:** `min_size/default_size`（无仪表板布局引擎），`guard_rules`文本列表（data_constraints 注入 LLM 更有效），`prefers_time_dim`（合并到 selection_rule 文本中）。

### 3.3 settings_schema：一次定义，三处消费

这是本设计最核心的抽象。一个 setting 条目同时服务于三个目的：

```
settings_schema = {
    "donut": {
        "desc_cn": "bool, 环形图(内径0.5), 默认false",  ① LLM 提示词
        "desc_en": "bool, donut chart, default false",
        "type": "bool",        ─┐
        "default": False,       ├ ② 前端工具栏按钮
        "icon": "donut",        │
        "label_cn": "环形",    ─┘
    },
}
```

**消费者 ① — LLM 提示词**：`get_settings_schema_text("pie")` 从 `desc_cn` 拼出：

```
饼图专属配置项 (可选，放在 JSON 的 "settings" 字段中):
  - donut: bool, 环形图(内径0.5), 默认false
  - sort: string, 扇区排序, none=原始 asc=升序 desc=降序, 默认none
```

注入 `chart.user` 模板。LLM 按需生成 `"settings":{"donut":true}`。

**消费者 ② — 前端工具栏**：API 下发的 `settingsSchema` 直接驱动 `v-for` 渲染按钮：

```typescript
// settingsSchema 中的 type 字段决定按钮行为:
//   "bool"   → toggle 按钮（点一下切换 true/false）
//   "select" → 点击循环按钮（在 options 列表中循环切换）
```

新增图表类型时，工具栏按钮**从 settingsSchema 自动长出**，无需手写 `v-if`。

**消费者 ③ — 渲染类**：图表类的 `applySettings(settings)` 方法，将 `settings` JSON 映射为 G2/S2 options：

```typescript
// Pie.ts
applySettings(settings: Record<string, any>) {
  if (settings.donut) {
    this.chart.options({ coordinate: { type: 'theta', innerRadius: 0.5 } })
  }
  if (settings.sort === 'asc' || settings.sort === 'desc') {
    // 对 data 排序后重新 set
  }
  this.chart.render()
}
```

### 3.4 图表类型选择：三信源融合

```
用户输入 "用饼图展示各产品销量"
    │
    ▼
┌──────────────────────────────────────────────────────┐
│ 信源 1: 关键词 (用户意图) — 权重最高                   │
│   "饼图" in question → "pie"                         │
├──────────────────────────────────────────────────────┤
│ 信源 2: 数据形状 (数据现实) — 辅助信息                  │
│   SQL 返回 50 个产品 → pie.max_series_cardinality=10  │
│   → 警告: "饼图建议≤10类，当前50类"                    │
├──────────────────────────────────────────────────────┤
│ 信源 3: LLM 最终判断                                  │
│   收到 hint + warning → 决定用 pie 还是改用 column     │
└──────────────────────────────────────────────────────┘
```

### 3.5 完整 settings_schema 目录

| 图表类型 | setting key | 类型 | 默认值 | 按钮 | 说明 |
|---------|-----------|------|--------|------|------|
| **table** | `sort_column` | select | 第一数值列 | ↕ | 默认排序的列 |
| | `sort_order` | select | desc | ↕ | asc / desc |
| | `page_size` | select | 50 | 📄 | 20 / 50 / 100 |
| | `number_format` | select | full | 🔢 | full / abbreviated / percent |
| **column** | `color_palette` | select | default | 🎨 | default / warm / cool / business |
| | `stack` | bool | false | 📊 | 分组 ↔ 堆叠 (多series时出现) |
| | `sort` | select | none | ↕ | none / asc / desc |
| | `show_label` | bool | false | 🏷 | 显示数值标签 |
| | `number_format` | select | full | 🔢 | full / abbreviated / percent |
| | `grid` | bool | true | 🏗 | 显隐网格线 |
| | `legend` | bool | true | 📖 | 显隐图例 (多series时出现) |
| **bar** | `color_palette` | select | default | 🎨 | 同 column |
| | `stack` | bool | false | 📊 | |
| | `sort` | select | none | ↕ | |
| | `show_label` | bool | false | 🏷 | |
| | `number_format` | select | full | 🔢 | |
| | `grid` | bool | true | 🏗 | |
| | `legend` | bool | true | 📖 | |
| **line** | `color_palette` | select | default | 🎨 | default / warm / cool / business |
| | `smooth` | bool | false | 📈 | 直线 ↔ 平滑曲线 |
| | `show_area` | bool | false | 🏔 | 填充线下区域 |
| | `show_points` | bool | false | 🔵 | 显示数据点标记 |
| | `show_label` | bool | false | 🏷 | 显示数值标签 |
| | `number_format` | select | full | 🔢 | full / abbreviated / percent |
| | `grid` | bool | true | 🏗 | 显隐网格线 |
| | `legend` | bool | true | 📖 | 显隐图例 |
| | `y_axis_zero` | bool | true | 📐 | Y轴是否从0开始 |
| **pie** | `color_palette` | select | default | 🎨 | default / warm / cool / business |
| | `donut` | bool | false | 🍩 | 饼图 ↔ 环形图 |
| | `sort` | select | none | ↕ | none / asc / desc |
| | `show_label` | bool | true | 🏷 | 显示标签 |
| | `label_format` | select | name_value | % | name_value / name_percent / value_percent |
| | `legend` | bool | true | 📖 | 显隐图例 |

**共 15 个 setting 定义，每种类型 4-9 个，每种类型下同时可见的按钮约 4-7 个。**

---

## 四、后端改动

### 4.1 executor.py — 关键词 → registry

```python
# 改前 (728-733): 6 行 if/elif，只覆盖 column/line/pie 三种
chart_type = ""
question = getattr(self.memory, "user_question", "").lower()
if any(w in question for w in ["柱状图", "柱状", "bar", "column"]):
    chart_type = "column"
elif any(w in question for w in ["折线", "趋势", "line", "折线图"]):
    chart_type = "line"
elif any(w in question for w in ["饼图", "占比", "pie", "饼", "扇形"]):
    chart_type = "pie"

# 改后: registry 关键词 + 数据形状融合
from apps.chat.agent.chart_registry import get_chart_type_hint
chart_type = get_chart_type_hint(
    getattr(self.memory, "user_question", ""),
    query.data
)
```

### 4.2 template.yaml — 硬编码 → 模板变量

| 位置 | 改前 | 改后 |
|------|------|------|
| `sql.generate_rules:152` | `支持的图表类型为表格(table)、柱状图(column)、…` | `{supported_chart_types}` |
| `sql.generate_rules:153` | `趋势 over time 用 line，…` | `{chart_selection_rules}` |
| `chart.generate_rules:414` | 同上 | `{supported_chart_types}` |
| `chart.user` | 无 | **新增** `{chart_type_settings_schema}` |

### 4.3 API 端点

```python
@router.get("/chart-types")
def get_chart_types(lang: str = "zh-CN"):
    """前端获取全部图表类型配置。启动时调用一次，写入 composable 缓存。"""
    from apps.chat.agent.chart_registry import get_frontend_config
    return get_frontend_config(lang)
```

返回 JSON 中 `settingsSchema` 即结构化 dict，前端直接使用。

### 4.4 g2-ssr/app.js — switch → 约定派发

```javascript
// 改前: switch 硬编码
// 改后:
function getOptions(type, axis, data) {
    try {
        const mod = require(`./charts/${type}.js`);
        const fn = mod[`get${type[0].toUpperCase() + type.slice(1)}Options`];
        if (typeof fn === 'function') return fn(BASE_OPTIONS, axis, data);
    } catch (e) { /* fallback */ }
    return BASE_OPTIONS;
}
```

---

## 五、前端改动

### 5.1 component/index.ts — glob 自动发现

```typescript
// 改前: 手动 import + 手动 CHART_TYPE_MAP
// 改后:
const modules = import.meta.glob('./charts/*.ts', { eager: true })
const CHART_TYPE_MAP: Record<string, typeof BaseChart> = {}
for (const [path, mod] of Object.entries(modules)) {
  const name = (path.split('/').pop() || '').replace('.ts', '')
  if (name === 'utils') continue
  const cls = (mod as any)[name]
  if (cls && isParent(cls, BaseChart)) CHART_TYPE_MAP[name.toLowerCase()] = cls
}
```

### 5.2 ChartBlock.vue / sq-view — 删除 switch

```typescript
// 改前: 30 行 switch (chartObject.value.type) { case 'column': ... }
// 改后:
const { config } = useChartTypeConfig()
const chartTypeList = computed(() => {
  const info = config.value?.chartTypes[currentChartType.value]
  return (info?.compatibleWith || []).map(id => ({
    value: id,
    name: config.value?.chartTypes[id]?.displayName,
    icon: CHART_ICONS[id],
  }))
})
```

两个文件用同一个 composable，消掉 copy-paste 重复。

### 5.3 工具栏：settingsSchema 驱动

```typescript
// 从 API 配置计算出当前图表类型应该显示哪些按钮
const toolbarButtons = computed(() => {
  const schema = config.value?.chartTypes[currentChartType.value]?.settingsSchema
  if (!schema) return []
  return Object.entries(schema)
    .filter(([_, s]) => s.icon)           // 有 icon 的才显示为按钮
    .map(([key, s]) => ({
      key,
      icon: ICON_MAP[s.icon],
      label: s.label_cn,
      type: s.type,                       // "bool" | "select"
      options: s.options || [],
      default: s.default,
    }))
})

// 当前激活的 settings 状态
const settingsState = reactive<Record<string, any>>({})

// bool: toggle, select: 点击循环
function toggleSetting(btn: ToolbarButton) {
  const current = settingsState[btn.key] ?? btn.default
  if (btn.type === 'bool') {
    settingsState[btn.key] = !current
  } else if (btn.type === 'select') {
    const idx = btn.options.indexOf(current)
    settingsState[btn.key] = btn.options[(idx + 1) % btn.options.length]
  }
  applyAndRerender()
}
```

**模板：**

```html
<!-- 已有按钮保持不变：类型切换、明细表、SQL、导出、仪表板、全屏 -->
<!-- 新增：settings 驱动的上下文工具栏，插在视觉样式区域 -->
<template v-for="btn in toolbarButtons" :key="btn.key">
  <el-tooltip :content="btn.label" placement="top">
    <el-button
      class="tool-btn"
      :class="{ 'chart-active': (settingsState[btn.key] ?? btn.default) !== btn.default }"
      text
      @click="toggleSetting(btn)"
    >
      <el-icon size="16"><component :is="btn.icon" /></el-icon>
    </el-button>
  </el-tooltip>
</template>
```

### 5.4 图表渲染类 — applySettings

在 `BaseChart` / `BaseG2Chart` 中新增方法，每个子类按自己的 settings 映射到 G2/S2 options：

```typescript
// BaseChart.ts
abstract applySettings(settings: Record<string, any>): void

// Pie.ts 示例
applySettings(settings: Record<string, any>) {
  if (settings.donut !== undefined) {
    const ir = settings.donut ? 0.5 : 0
    this.chart.options({ coordinate: { type: 'theta', innerRadius: ir, outerRadius: 0.8 } })
  }
  if (settings.sort && settings.sort !== 'none') {
    this.data.sort((a, b) => { /* 按 y 值排序 */ })
  }
  if (settings.show_label !== undefined) {
    this.showLabel = settings.show_label
  }
  if (settings.label_format) {
    // 更新 label text 函数
  }
  this.chart.render()
}
```

### 5.5 useChartTypeConfig composable

```typescript
// frontend/src/views/chat/component/useChartTypeConfig.ts
import { ref } from 'vue'

interface ChartTypeConfig {
  chartTypes: Record<string, {
    typeId: string; displayName: string; icon: string; category: string
    compatibleWith: string[]; dataConstraints: Record<string, any>
    settingsSchema: Record<string, {
      desc_cn: string; type: string; default: any; icon: string
      label_cn: string; options?: string[]
    }>
    baseClass: string; hasSsr: boolean; usesAxis: boolean
  }>
  allTypeIds: string[]
  categories: Record<string, string[]>
  defaultType: string
}

const config = ref<ChartTypeConfig | null>(null)
let pending = false

export function useChartTypeConfig() {
  if (!config.value && !pending) {
    pending = true
    fetch('/api/chat/chart-types')
      .then(r => r.json())
      .then(data => { config.value = data })
      .catch(() => {
        // fallback: 5 种类型硬编码兜底
        config.value = FALLBACK_CONFIG
      })
  }
  return { config }
}
```

### 5.6 BaseChart.ts / i18n — 降级

- **BaseChart.ts**: `ChartTypes` 改为 `string` + 运行时从 API `allTypeIds` 校验
- **i18n**: 现有键保留，新增类型不再需要加——`displayName` 由 API 下发

---

## 六、实施计划

### Phase 2a: 消除硬编码

| # | 内容 | 文件 | 风险 |
|---|------|------|------|
| 1 | 完善 `chart_registry.py`：5 类型结构化 settings_schema + 全部派生函数 | registry | — |
| 2 | `executor.py:728-733` → `get_chart_type_from_keywords()` | executor | 低 |
| 3 | `template.yaml` 三处硬编码 → `{supported_chart_types}` / `{chart_selection_rules}` | template.yaml | 中 |
| 4 | `component/index.ts` → `import.meta.glob` 自动发现 | index.ts | 低 |
| 5 | 新增 `GET /api/chat/chart-types` + 前端 `useChartTypeConfig` composable | api/chat.py | 低 |
| 6 | `ChartBlock.vue` / `sq-view` switch → composable 的 `compatibleWith` | 两个 vue 文件 | 中 |
| 7 | `g2-ssr/app.js` switch → 约定派发 | app.js | 中 |
| 8 | BaseChart.ts 放宽类型，i18n 不再追加 | BaseChart.ts | 低 |

### Phase 2b: 智能类型选择

| # | 内容 | 文件 | 风险 |
|---|------|------|------|
| 9 | `get_chart_type_hint()` 融合关键词 + 数据形状 | registry, executor | 中 |
| 10 | 在 chart 生成 LLM 提示词中注入数据形状信息 | template.yaml | 中 |

### Phase 2c: 类型专属设置

| # | 内容 | 文件 | 风险 |
|---|------|------|------|
| 11 | `chart.user` 模板注入 `{chart_type_settings_schema}` | template.yaml | 低 |
| 12 | `ChartBlock.vue` 工具栏 v-for 渲染 settings 按钮 | ChartBlock.vue | 中 |
| 13 | `BaseChart.applySettings()` + 5 个图表类实现 | BaseChart.ts, charts/*.ts | 中 |
| 14 | LLM 生成 JSON 中允许 `"settings":{...}` 字段，渲染类应用 | executor, charts/*.ts | 低 |

---

## 七、风险

| 风险 | 缓解 |
|------|------|
| `import.meta.glob` 文件名不匹配 | Vite 构建时 fail-fast，约定文档化 |
| API `/chart-types` 加载失败 | composable 内置 5 类型硬编码 fallback |
| g2-ssr 动态 require 失败 | try/catch → 降级默认渲染 |
| 模板变量遗漏注入点 | `grep "table.*column.*bar.*line.*pie" template.yaml` 全覆盖 |
| LLM 不理解 settings_schema | 字段全部可选，LLM 忽略不出错 |
| 工具栏按钮过多（>7个） | 共用 settings 始终显示，上下文 settings 仅当数据满足条件时出现 |
| 旧 DB chart_config 无 settings 字段 | 向前兼容，settings 不存在时按 default 值处理 |
