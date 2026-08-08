# SQLBot 图表系统改进方案

> 编写日期: 2026-07-17
> 状态: 草案
> 参考: Metabase 可插拔可视化架构 / 帆软 FineReport AI+BI 方案

---

## 一、现状诊断

### 1.1 当前架构回顾

SQLBot 的图表渲染链路：

```
用户提问 → executor.py (关键词→chart_type) → LLM 生成 chart JSON
                                                    │
  { type, title, axis:{x,y,series}, columns, settings }
                                                    │
         SSE 推送 → ChartAnswer.vue 接收 → ChartBlock.vue
                                                    │
         DisplayChartBlock 拆解 axis → ChartComponent
                                                    │
         getChartInstance(type) → G2/S2 渲染
```

### 1.2 核心缺陷清单

| # | 缺陷 | 严重度 | 表现 |
|---|------|--------|------|
| 1 | **LLM 输出不可靠** | 致命 | chart JSON 格式错误/axis 缺失 → 图表空白 |
| 2 | **无渲染错误边界** | 致命 | G2 抛异常 → 用户看到空白，没有任何错误提示 |
| 3 | **轴数据与图表类型强耦合** | 严重 | LLM 生成 table（无 axis）→ 切换到 column 时 axis 为空 → 空白 |
| 4 | **图表类型选择无数据约束** | 严重 | 用户可选不兼容的图表类型（如单列数据选饼图） |
| 5 | **chart JSON 格式无校验** | 中等 | 前端信任 LLM 输出，不校验完整性 |
| 6 | **settings 应用不稳定** | 中等 | applySettings 链路长，中间任何环节失败都静默 |
| 7 | **图标系统脆弱** | 中等 | 跨模块 SVG 引用导致生产构建 "M is not a function" |

### 1.3 根因分析

SQLBot 的根本问题是 **AI 输出作为唯一真值源 (Single Source of Truth)**：

```
┌──────────────────────────────────────────────────┐
│                    SQLBot                          │
│                                                    │
│   LLM ──→ chart JSON ──→ 前端渲染                   │
│    ↑                        ↑                      │
│    不可靠输出               无校验无兜底              │
│                                                    │
│   单点故障: LLM 输出质量 = 系统可用性                 │
└──────────────────────────────────────────────────┘
```

对比成熟系统的设计原则：

```
┌──────────────────────────────────────────────────┐
│               Metabase / 帆软                      │
│                                                    │
│   数据(SQL结果) ──→ 约束检查 ──→ 用户选择类型 ──→ 渲染│
│       ↑              ↑              ↑              │
│     可靠源        自动过滤       人工确认            │
│                                                    │
│   LLM 只做自然语言→SQL 翻译，不介入渲染配置           │
└──────────────────────────────────────────────────┘
```

---

## 二、业界方案对比

### 2.1 Metabase

**架构特点**：

| 维度 | 实现 |
|------|------|
| 图表引擎 | ECharts v6 + D3.js v7 |
| 类型选择 | **数据驱动**：每种图表 `checkRenderable(series, settings)` 声明数据约束 |
| 配置系统 | `defineConfig` 声明式：`id` / `getName` / `checkRenderable` / `settings` / `mount` |
| 渲染模式 | 插件隔离：`mount(Component, container, props)` → `{update, unmount}` |
| 错误处理 | `PluginErrorBoundary` 包裹每个 viz，渲染失败静默降级 |
| Settings | 三层合并：全局默认 → Card 覆盖 → Dashboard 覆盖 |
| 状态管理 | Redux Toolkit + RTK Query |

**关键模式 — `checkRenderable`**：

```typescript
// 每种图表实现自己的验证逻辑
{
  id: "pie",
  checkRenderable(series, settings) {
    // 数据不满足约束时 throw，Metabase 自动隐藏该类型
    if (series.length === 0) throw new Error("No data")
    if (metricCount > 1) throw new Error("Pie needs exactly 1 metric")
    if (breakoutCardinality > 10) throw new Error("Pie: ≤10 categories")
  }
}
```

**关键模式 — 插件生命周期**：

```typescript
// 每个 viz 独立 React 树，带完整的 mount/update/unmount
{
  mount(Component, container, initialProps) {
    const root = createRoot(container)
    return {
      update: (props) => root.render(<Component {...props} />),
      unmount: () => root.unmount(),
    }
  }
}
```

**优势**：
- 图表类型选择由数据约束决定，用户永远只能选能渲染的类型
- 渲染失败有 ErrorBoundary 兜底，不影响页面
- 插件化架构，新增类型零侵入

### 2.2 帆软 FineReport (FanRuan)

**架构特点**：

| 维度 | 实现 |
|------|------|
| 图表引擎 | 自研 ECharts 封装 + 企业级图表库 |
| AI 集成 | **API 对接**：通过 REST 接大模型 → 意图解析 → SQL 生成 → 图表推荐 |
| 图表推荐 | AI 分析数据特征 → 推荐最合适图表（趋势→折线，分布→热力） |
| 自然语言 | 用户自然语言提问 → 自动生成图表 + 数据解读文本 |
| 数据大屏 | 多维下钻 + AI 自动生成分析解读 |
| 部署方式 | **私有化部署**——大模型不接触企业敏感数据 |

**帆软的核心设计思想**：

1. **AI 做推荐，不做决定**：AI 分析数据特征后推荐图表面类型，但最终由用户确认选择
2. **数据解读作为附加值**：图表不只展示数据，还附带 AI 生成的文字分析
3. **多层安全架构**：私有化部署 + 细粒度权限，大模型在受限环境下运行
4. **行业垂直模型**：金融/制造/零售等行业大模型提供更精准的分析

### 2.3 三方对比总结

| 能力 | SQLBot (现状) | Metabase | 帆软 FineReport |
|------|:-----------:|:--------:|:-------------:|
| 图表选择方式 | AI 关键词猜测 | 数据约束过滤 | AI 推荐 + 用户确认 |
| LLM 职责 | SQL + chart JSON | 仅自然语言→查询 | SQL + 建议图表 + 解读 |
| 渲染健壮性 | 无错误边界 | ErrorBoundary | 成熟商业级 |
| axis 推导 | LLM 手写 | 渲染器自动映射 | 渲染器自动映射 |
| 数据约束检查 | ❌ 无 | ✅ checkRenderable | ✅ |
| 插件化程度 | glob 代码发现 | 独立打包 + 沙箱 | 商业插件生态 |
| 设置系统 | settingsSchema 驱动 | 三层合并覆盖 | 企业级配置 |
| 部署安全 | — | — | 私有化部署 |

---

## 三、改进方案

### 3.1 总体架构目标

```
                    ┌──────────────┐
                    │  用户提问      │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  LLM 生成 SQL │  ← LLM 只负责 SQL，不输出 chart 配置
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐
                    │  SQL 执行结果  │  ← { columns: [{name,type}], rows: [...] }
                    │  (结构化数据)  │
                    └──────┬───────┘
                           ▼
              ┌─────────────────────────┐
              │   Chart Matcher (新增)    │
              │                          │
              │  对每种图表类型执行:        │
              │    checkRenderable(data)  │
              │                          │
              │  返回: 兼容的图表类型列表   │
              └──────────┬──────────────┘
                         ▼
              ┌─────────────────────────┐
              │  用户选择 + AI 推荐       │  ← AI 推荐一个默认类型，用户可切换
              └──────────┬──────────────┘
                         ▼
              ┌─────────────────────────┐
              │   Axis Resolver (新增)   │
              │                          │
              │  从 columns 自动推导:     │
              │    x → 维度列 (time/cat) │
              │    y → 指标列 (numeric)  │
              │    series → 分类列       │
              │    (可选, ≤10 唯一值)     │
              └──────────┬──────────────┘
                         ▼
              ┌─────────────────────────┐
              │   Viz Renderer           │
              │   (G2 / S2 / ECharts)   │
              │   + ErrorBoundary        │
              └─────────────────────────┘
```

### 3.2 分阶段实施计划

#### Phase 3a: 加固当前系统 (P0 — 本周)

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 1 | **Axis 自动推导** | DisplayChartBlock.vue | ✅ 已完成。axis 缺失时从 columns+data 智能推导 x/y/series |
| 2 | **渲染 ErrorBoundary** | ChartComponent.vue | 新增：包裹 G2/S2 渲染，异常时显示"图表渲染失败，请尝试切换类型" |
| 3 | **chart JSON 校验** | DisplayChartBlock.vue | 新增：收到 chart JSON 后验证 type/columns 等必要字段；不合格自动降级为 table |
| 4 | **图标系统去跨模块依赖** | useChartTypeConfig.ts | ✅ 已完成。SVG import 从 chartIcons.ts 移至 useChartTypeConfig.ts 自身 |

#### Phase 3b: 数据驱动类型选择 (P1 — 下周)

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 5 | **Chart Matcher 概念** | 新增 `chartMatcher.ts` | 每种图表类型声明 `checkRenderable(data, settings)`，运行时过滤 |
| 6 | **`getChartTypeList` 升级** | useChartTypeConfig.ts | 集成 checkRenderable 过滤，只返回数据兼容的类型 |
| 7 | **`dataConstraints` 运行时检查** | ChartBlock.vue | 利用 API 下发的 dataConstraints 执行客户端校验 |
| 8 | **LLM 不再生成 axis 映射** | template.yaml + executor.py | LLM 只输出 `{type, title}`，axis 由前端 Axis Resolver 推导 |

#### Phase 3c: 架构升级 (P2 — 两周后)

| # | 改动 | 文件 | 说明 |
|---|------|------|------|
| 9 | **Chart Renderer 抽象** | 新增 `ChartRenderer.ts` | mount/update/unmount 生命周期，参考 Metabase 插件模式 |
| 10 | **Settings 三层合并** | ChartBlock.vue | API 默认 → 用户偏好(localStorage) → 当前覆盖 |
| 11 | **AI 图表推荐** | executor.py | AI 不决定类型，只推荐置信度最高的类型 + 理由 |
| 12 | **AI 数据解读** | executor.py | 图表旁附带 AI 生成的简短文字解读（参考帆软方案） |
| 13 | **ECharts 迁移评估** | 研究 | 对比 G2/S2 vs ECharts 在 SQLBot 场景下的优劣 |

### 3.3 关键设计细节

#### 3.3.1 Chart Matcher (checkRenderable)

```typescript
// frontend/src/views/chat/component/chartMatcher.ts

interface DataProfile {
  columns: Array<{ name: string; value: string; isNumeric: boolean; uniqueCount: number }>
  rowCount: number
}

interface MatchResult {
  compatible: boolean
  reason?: string           // 不兼容原因
  confidence: number        // 0-1 推荐置信度
}

const CHART_MATCHERS: Record<string, (data: DataProfile) => MatchResult> = {
  pie(data) {
    const metrics = data.columns.filter(c => c.isNumeric)
    const dims = data.columns.filter(c => !c.isNumeric)
    if (metrics.length !== 1) return { compatible: false, reason: '饼图需要恰好1个数值列', confidence: 0 }
    if (dims[0]?.uniqueCount > 10) return { compatible: false, reason: '饼图不适合超过10个分类', confidence: 0 }
    return { compatible: true, confidence: 0.8 }
  },
  line(data) {
    const hasTime = data.columns.some(c => /time|date|时间|日期/.test(c.name.toLowerCase()))
    if (!hasTime) return { compatible: true, confidence: 0.3 }  // 可以用但不推荐
    return { compatible: true, confidence: 0.9 }
  },
  column(data) { /* ... */ },
  bar(data) { /* ... */ },
  table(data) {
    return { compatible: true, confidence: data.columns.length > 5 ? 0.6 : 0.2 }
  },
}
```

#### 3.3.2 Axis Resolver (自动推导)

```typescript
// 当 chart JSON 中无 axis 时，从数据列自动推导
function resolveAxes(columns, data, targetChartType) {
  const profile = columns.map(col => ({
    ...col,
    isNumeric: detectNumeric(data, col.value),
    isTime: detectTime(data, col.value),
    uniqueCount: countUnique(data, col.value, 100),
  }))

  switch (targetChartType) {
    case 'pie':
      return {
        y: [profile.find(c => c.isNumeric)],
        series: [profile.find(c => !c.isNumeric)],
      }
    case 'line':
    case 'column':
    case 'bar':
      return {
        x: [profile.find(c => c.isTime) || profile.find(c => !c.isNumeric) || profile[0]],
        y: [profile.find(c => c.isNumeric)],
        series: findSeriesCandidate(profile),  // 第二个非数值列，2≤唯一值≤10
      }
    case 'table':
      return { columns: profile }
  }
}
```

#### 3.3.3 ErrorBoundary

```vue
<!-- ChartComponent.vue 中新增 -->
<template>
  <div :id="chartId" class="chart-container">
    <div v-if="renderError" class="chart-error">
      <span>图表渲染失败</span>
      <el-button size="small" @click="retryRender">重试</el-button>
    </div>
  </div>
</template>

<script setup>
const renderError = ref(false)

function renderChart() {
  renderError.value = false
  try {
    chartInstance = getChartInstance(params.type, chartId.value)
    if (chartInstance) {
      chartInstance.init(axis.value, params.data)
      chartInstance.render()
    }
  } catch (e) {
    console.error('[ChartComponent] render failed:', e)
    renderError.value = true
  }
}
</script>
```

#### 3.3.4 LLM 职责收窄

```
改前: LLM 生成完整 chart JSON
  { type, title, axis:{x:{name,value}, y:{name,value}, series:{name,value}}, 
    columns:[...], settings:{...} }
  
  问题: LLM 需要理解数据库列名、数据类型、渲染引擎的 axis 概念
        → 出错概率高，且修复只能靠模板调优

改后: LLM 只输出类型 + 标题
  { type: "line", title: "月度收入趋势" }
  
  axis 映射由前端 Axis Resolver 从 SQL 结果集推导
  settings 完全由前端工具栏控制，不经过 LLM
```

---

## 四、帆软方案的借鉴点

### 4.1 AI 推荐 + 用户确认

帆软的核心模式：AI 分析数据 → **推荐**图表类型 → 用户确认 → 渲染。 不会出现 AI 选错类型导致页面不可用。

SQLBot 可以借鉴：
- AI 给出 `recommendedType` + `recommendationReason`
- 前端可选择接受推荐，或手动切换
- 即使 AI 推荐错误，用户也能手动纠正

### 4.2 AI 数据解读

帆软的图表不只展示可视化，还附带 AI 生成的文字解读。这是 SQLBot 可以自然扩展的方向——LLM 已经在对话上下文中，可以在图表旁生成自然语言总结。

### 4.3 私有化部署 + 数据安全

帆软强调私有化部署确保大模型不接触企业敏感数据。SQLBot 的后端已经分离了 SQL 执行和数据返回，这个架构是合理的，可以进一步强化审计日志和权限控制。

---

## 五、实施优先级矩阵

```
                    影响大
                      │
         Phase 3a     │     Phase 3b
         (本周)       │     (下周)
         ████████     │     ████████
         Axis推导     │     Chart Matcher
         ErrorBoundary│     LLM职责收窄
         JSON校验     │     推荐系统
                      │
    ──────────────────┼──────────────────
                      │
         Phase 3c     │
         (两周后)     │
         ░░░░░░░░     │
         渲染器抽象    │
         ECharts评估  │
         三层Settings │
                      │
                    影响小
                      
         紧急 ←──────────────────→ 重要
```

---

## 六、与现有 dev-plan 的关系

本方案是 `dev-plan-phase2-chart-registry.md` 的延续和升级：

| 维度 | Phase 2 (已完成) | Phase 3 (本方案) |
|------|:-----------:|:-----------:|
| 目标 | 消除硬编码注册 | 消除 AI 依赖脆弱性 |
| 图表类型 | 配置化 (chart_registry.py) | 数据驱动过滤 (checkRenderable) |
| 轴映射 | LLM 生成 | 前端自动推导 |
| 错误处理 | 无 | ErrorBoundary + JSON 校验 |
| 设置系统 | settingsSchema 驱动工具栏 | + 三层合并 |
| LLM 职责 | SQL + chart JSON | SQL only (图表配置交给前端) |

Phase 2 是"让图表类型可配置"，Phase 3 是"让图表渲染健壮可靠"。
