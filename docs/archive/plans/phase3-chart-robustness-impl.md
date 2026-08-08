# SQLBot 图表系统健壮性改造 — 开发实施文档（修订版）

> 编写日期: 2026-07-17
> 修订日期: 2026-07-17（基于实际代码审查重写）
> 状态: 待实施
> 前置依赖: Phase 2 (chart_registry.py + useChartTypeConfig + 图标系统) 已完成

---

## 〇、审查结论与架构决策

### 当前架构（正确，保留）

```
LLM 生成 chart JSON (type + title + axis + columns)
        │
        ├── axis 存在 (column/bar/line/pie) → 前端直接使用
        │
        └── axis 缺失 (table 类型) → 前端 _deriveAxis() 兜底推导
```

当前模板 [template.yaml:410-507](SQLBot-main/backend/templates/template.yaml#L410-L507) 已有详细的 JSON 生成规则。**双路径架构是正确的**：
- LLM 理解 SQL 语义，生成的 axis 映射比纯启发式推导更准确
- 前端 `_deriveAxis` 作为兜底，处理 table→chart 切换场景

### 核心问题定位

| 问题 | 根因 | 修复方向 |
|------|------|---------|
| chart LLM **拿不到列信息** | executor.py:736 `schema=""` | 注入 SQL 结果集的列名+类型 |
| LLM axis 使用了 SQL 中不存在的列名 | 无校验 | 后端增加 `_validate_and_fix_chart_json` |
| 部分图表切换后空白 | table 无 axis → `_deriveAxis` 只检查第一行 | 增强列类型检测 |
| API 不可用时设置按钮缺失 | FALLBACK_CONFIG 与 registry 不同步 | 补齐 6 个缺失条目 |
| settings apply 无错误保护 | 无 try-catch | 增加防御性代码 |

---

## 一、改造范围总览

```
┌──────────────────────────────────────────────────────────────────┐
│                       改造文件清单                                │
├─────────────┬────────────────────────────────────────────────────┤
│   层级       │  文件                                              │
├─────────────┼────────────────────────────────────────────────────┤
│  后端        │  executor.py          (修改: 注入列信息 + 校验兜底)  │
│             │  template.yaml        (修改: 增强 chart 模板规则)   │
│             │  chart_registry.py    (无改动, 复用)                │
├─────────────┼────────────────────────────────────────────────────┤
│  前端        │  useChartTypeConfig.ts (修改: FALLBACK_CONFIG 同步) │
│             │  DisplayChartBlock.vue (修改: 增强 _deriveAxis)     │
│             │  ChartBlock.vue        (修改: 错误处理 try-catch)   │
│             │  ChartComponent.vue    (修改: renderChart 错误处理)  │
├─────────────┼────────────────────────────────────────────────────┤
│  数据库      │  ChatRecord 模型      (无改动, 兼容旧 chart JSON)   │
└─────────────┴────────────────────────────────────────────────────┘
```

**不改造的部分**（与旧文档一致）：
- `chart_registry.py` — 已完成，是单一配置源
- `charts/Column.ts, Bar.ts, Line.ts, Pie.ts, Table.ts` — 渲染类稳定
- `BaseG2Chart.ts, BaseChart.ts` — 基类稳定
- `ChartPopover.vue` — 已修复（移除 firstItem，增加 null-safe）
- `ChartAnswer.vue` — SSE 接收逻辑已稳定
- `index.ts` — glob 自动发现已工作
- `sq-view/index.vue` — Dashboard 复用同一套 composable

---

## 二、后端改造

### 2.1 executor.py — 注入列信息 + 校验兜底

**当前问题**（[executor.py:734-738](SQLBot-main/backend/apps/chat/agent/executor.py#L734-L738)）：

```python
user_msg = tpl["user"].format(
    lang="zh-CN", sql=query.sql, question=question,
    chart_type=chart_type, schema="",  # ← 空字符串！
    chart_type_settings_schema=get_settings_schema_text(chart_type),
)
```

Chart LLM 看不到列名和类型，只能从 SQL 文本中猜测。这是 LLM 生成错误 axis 的**根本原因**。

**改造**：

```python
# executor.py: _generate_chart() 修改点

async def _generate_chart(self, query) -> None:
    """Generate chart config via LLM and save to ChatRecord."""
    from apps.template.generate_chart.generator import get_chart_template
    from apps.chat.curd.chat import save_chart_answer, save_chart, get_chart_config
    import orjson as _orjson

    record = getattr(self.memory, "record", None)
    session = self.memory.session

    if not query.data:
        _log.info("[Agent] no data for chart generation")
        return

    tpl = get_chart_template()
    system_msg = tpl["system"].format(lang="zh-CN", sqlbot_name="SQLBot")
    rules_msg = tpl["generate_rules"].format(lang="zh-CN")

    from apps.chat.agent.chart_registry import (
        get_chart_type_hint,
        get_settings_schema_text,
        get_data_constraints_text,      # 新增
        get_selection_rules,            # 新增
    )

    question = getattr(self.memory, "user_question", "")
    chart_type = get_chart_type_hint(question, query.data)

    # ── 修改 1: 从 SQL 结果集提取列信息 ──
    columns_schema_text = _build_columns_schema(query.data)

    user_msg = tpl["user"].format(
        lang="zh-CN", sql=query.sql, question=question,
        chart_type=chart_type,
        columns_schema=columns_schema_text,               # 替换原来的 schema=""
        chart_type_settings_schema=get_settings_schema_text(chart_type),
        data_constraints=get_data_constraints_text(),      # 新增: 数据约束
        chart_selection_rules=get_selection_rules(),       # 新增: 选择原则
    )

    # ... LLM 调用不变 ...

    # ── 修改 2: 校验 + 修复 ──
    chart = _validate_and_fix_chart_json(chart_json, query)

    # ... save 不变 ...
```

### 2.2 新增: `_build_columns_schema` — 构造列信息文本

```python
def _build_columns_schema(query_data) -> str:
    """
    从 SQL 执行结果中提取列信息，构造成 LLM 可读的文本。

    输入: query.data = {"fields": [...], "data": [[...], ...]}
    输出:
        列名 (别名)  |  类型       |  示例值
        month        |  varchar   |  2024-01
        income       |  decimal   |  1234567.89
        category     |  varchar   |  电子产品
    """
    fields = query_data.get("fields", []) if isinstance(query_data, dict) else []
    data_rows = query_data.get("data", []) if isinstance(query_data, dict) else []

    lines = ["SQL 查询结果列:"]
    # 取前 3 行作为示例
    sample_rows = data_rows[:3] if data_rows else []

    for i, field in enumerate(fields):
        if isinstance(field, dict):
            name = field.get("name", f"col_{i}")
            col_type = field.get("type", "unknown")
        else:
            name = str(field)
            col_type = "unknown"

        # 提取示例值
        samples = []
        for row in sample_rows:
            if isinstance(row, (list, tuple)) and i < len(row):
                samples.append(str(row[i]))
            elif isinstance(row, dict) and name in row:
                samples.append(str(row[name]))

        sample_str = ", ".join(samples[:3]) if samples else "(无数据)"
        lines.append(f"  - {name} (类型: {col_type}, 示例: {sample_str})")

    return "\n".join(lines)
```

### 2.3 新增: `_validate_and_fix_chart_json` — LLM 输出校验

```python
def _validate_and_fix_chart_json(chart_json, query) -> dict:
    """
    校验并修复 LLM 输出的 chart JSON。

    只修复明确的格式/字段错误，不改 LLM 的类型选择决策。

    规则:
      1. JSON 解析失败 → 默认 {type:"table"}
      2. type 不在注册表中 → 降级为 "table"
      3. axis.x / axis.y / axis.series 中使用了 SQL 中不存在的列名 → 移除该字段
      4. title 缺失 → 从 SQL 表名自动生成
      5. settings 字段 → 移除（完全由前端控制）
    """
    import json as _json
    from apps.chat.agent.chart_registry import validate_chart_type

    # Step 1: 解析
    if isinstance(chart_json, str):
        try:
            chart = _json.loads(chart_json)
        except _json.JSONDecodeError:
            chart = {"type": "table"}
    else:
        chart = chart_json if chart_json else {"type": "table"}

    # Step 2: type 校验
    if not chart.get("type") or not validate_chart_type(chart["type"]):
        chart["type"] = "table"

    # Step 3: 校验 axis 中的列名是否在 SQL 输出中存在
    valid_columns = _extract_sql_output_columns(query)
    if valid_columns and "axis" in chart:
        axis = chart["axis"]
        # x
        if axis.get("x") and axis["x"].get("value") not in valid_columns:
            axis.pop("x", None)
        # y (可能是单个对象或数组)
        y = axis.get("y")
        if y:
            if isinstance(y, list):
                axis["y"] = [item for item in y if item.get("value") in valid_columns]
                if not axis["y"]:
                    axis.pop("y", None)
            elif isinstance(y, dict):
                if y.get("value") not in valid_columns:
                    axis.pop("y", None)
        # series
        if axis.get("series") and axis["series"].get("value") not in valid_columns:
            axis.pop("series", None)
        # multi-quota
        mq = axis.get("multi-quota")
        if mq and isinstance(mq, dict):
            mq["value"] = [v for v in mq.get("value", []) if v in valid_columns]
            if not mq["value"]:
                axis.pop("multi-quota", None)

    # Step 4: title 兜底
    if not chart.get("title"):
        chart["title"] = _generate_default_title(query)

    # Step 5: 移除 settings（前端控制）
    chart.pop("settings", None)

    return chart


def _extract_sql_output_columns(query) -> set:
    """从 SQL 结果集中提取所有合法的列名/别名。"""
    columns = set()
    try:
        data = query.data
        if isinstance(data, dict):
            fields = data.get("fields", [])
            for f in fields:
                if isinstance(f, dict):
                    columns.add(f.get("name", ""))
                else:
                    columns.add(str(f))
    except Exception:
        pass
    # 也尝试从 SQL 文本中解析别名（兜底）
    sql = getattr(query, "sql", "") or ""
    if isinstance(sql, str):
        import re
        # 匹配 SELECT 中的别名: ... AS alias, ... AS "alias", ... `alias`
        for m in re.finditer(
            r'(?:AS\s+|\.)([`"]?)(\w+)\1',
            sql, re.IGNORECASE
        ):
            columns.add(m.group(2))
        # 匹配 SELECT 中的纯列名（无 AS）
        select_part = re.sub(r'(?s)SELECT\s+|\s+FROM.*', '', sql, flags=re.IGNORECASE)
        # 取最后一个 token 作为潜在列名
        for token in select_part.split(','):
            token = token.strip().split()[-1] if token.strip().split() else ''
            token = token.strip('`"[]')
            if token and not token.upper() in ('SELECT', 'FROM', 'WHERE', 'LIMIT', ''):
                columns.add(token)
    return columns


def _generate_default_title(query) -> str:
    """从 SQL 中提取表名作为兜底标题。"""
    try:
        sql = getattr(query, "sql", "") or ""
        import re
        match = re.search(r'FROM\s+`?(\w+)`?', sql, re.IGNORECASE)
        if match:
            return f"{match.group(1)} 数据表"
    except Exception:
        pass
    return "数据查询结果"
```

### 2.4 template.yaml — 增强图表模板

**新增模板变量**: `{columns_schema}`, `{data_constraints}`, `{chart_selection_rules}`

**修改 chart.user**（[template.yaml:546-562](SQLBot-main/backend/templates/template.yaml#L546-L562)）：

```yaml
chart:
  user: |
    ## 请根据上述要求，使用语言：{lang} 进行回答，若有深度思考过程，则思考过程也需要使用 {lang} 输出
    ## 回答中不需要输出你的分析，请直接输出符合要求的JSON
    ## 必须注意：不论要求你用什么语言回答，生成图表结构中的value字段内容必须与提供的SQL中提供的字段别名的字符保持一致！
    <user-question>
    {question}
    </user-question>
    <sql>
    {sql}
    </sql>
    <columns-schema>
    {columns_schema}
    </columns-schema>
    <chart-type>
    {chart_type}
    </chart-type>
    <data-constraints>
    {data_constraints}
    </data-constraints>
    <chart-selection-rules>
    {chart_selection_rules}
    </chart-selection-rules>
    {chart_type_settings_schema}
```

**增强 chart.generate_rules** — 在现有规则基础上增加列名校验规则和自检步骤：

```yaml
chart:
  generate_rules: |
    以下是你必须遵守的规则和可以参考的基础示例：  
    <Rules>
      <!-- 现有规则全部保留 (line 410-507) -->

      <!-- 新增规则 1: 列名校验 -->
      <rule priority="critical">
        <title>列名必须来自 SQL 输出</title>
        axis 中每个字段的 "value" 必须与以下来源之一完全匹配:
        1. <columns-schema> 中列出的列名
        2. <sql> 中 SELECT 子句的列别名 (AS 后的名称)
        不要编造 <columns-schema> 和 <sql> 中不存在的列名。
      </rule>

      <!-- 新增规则 2: 数据形状自检 -->
      <rule priority="high">
        <title>生成后自检数据形状</title>
        在生成 JSON 之前，检查 <data-constraints> 中对该类型的约束:
        - 数值列数量是否满足 min_metrics / max_metrics
        - 维度列数量是否满足 min_dimensions / max_dimensions
        - 饼图: 分类列唯一值是否 ≤ max_series_cardinality
        如果不满足约束，改用 "table" 类型。
      </rule>

      <!-- 新增规则 3: columns 字段必须输出 -->
      <rule priority="high">
        <title>columns 字段输出规则</title>
        无论生成什么 chart type，都必须输出 "columns" 字段：
        - 内容：<columns-schema> 中的列名和对应的 {lang} 显示名称
        - 格式：[{"name": "{lang}显示名", "value": "SQL列名"}, ...]
        - 作用：前端在 axis 缺失时依赖 columns 进行自动推导
      </rule>

    <Rules>
```

**关键变更说明**：
1. `columns_schema` 注入 — LLM 直接看到 SQL 输出的列名、类型、示例值，axis 映射准确率大幅提升
2. `data_constraints` 注入 — LLM 生成前自检数据形状是否匹配类型约束，不匹配则降级 table
3. `chart_selection_rules` 注入 — LLM 看到各类型的选择原则
4. columns 强制输出 — 即使 axis 因某些原因缺失，前端 `_deriveAxis` 也能从 columns 推导

---

## 三、前端改造

### 3.1 useChartTypeConfig.ts — FALLBACK_CONFIG 同步

**问题**：FALLBACK_CONFIG 缺少 6 个 settingsSchema 条目（审查发现）。

**修改**：与 [chart_registry.py:80-360](SQLBot-main/backend/apps/chat/agent/chart_registry.py#L80-L360) 完全同步。

```typescript
// useChartTypeConfig.ts: FALLBACK_CONFIG 修改

// table: 补充 sort_column
settingsSchema: {
  sort_column: { desc_cn: '', type: 'select', default: '', icon: 'sort', label_cn: '排序列', options: [] },
  sort_order: { desc_cn: '', type: 'select', default: 'desc', icon: 'sort', label_cn: '排序', options: ['asc', 'desc'] },
  page_size: { desc_cn: '', type: 'select', default: 50, icon: 'page', label_cn: '分页', options: [20, 50, 100] },
  number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
},

// column: 补充 color_palette
settingsSchema: {
  color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
  stack: { ... },
  // ... 其余不变
},

// bar: 补充 color_palette
// line: 补充 color_palette, show_points, y_axis_zero
// pie: 补充 color_palette, label_format
```

完整 FALLBACK_CONFIG 见附录 A。

### 3.2 DisplayChartBlock.vue — 增强 _deriveAxis

**问题**：`_deriveAxis` 只检查第一行判断列类型 ([DisplayChartBlock.vue:87-88](SQLBot-main/frontend/src/views/chat/component/DisplayChartBlock.vue#L87-L88))，第一行为 null 时误判。

**修改**：抽样多行 + 增加时间列检测。

```typescript
// DisplayChartBlock.vue: _deriveAxis 增强

function _deriveAxis(target: 'x' | 'y' | 'series') {
  const cols = chartObject.value?.columns
  if (!cols || cols.length === 0) return []
  const rows = props.data
  if (!rows || rows.length === 0) return []

  // ── 修改: 抽样前 20 行分析列类型 ──
  const SAMPLE_SIZE = 20
  const sampleRows = rows.slice(0, SAMPLE_SIZE)

  const colTypes = cols.map(col => {
    const vals = sampleRows
      .map(r => r[col.value])
      .filter(v => v !== null && v !== undefined && v !== '')

    // 数值判定: 抽样行中大多数非空值可转为数字
    const numericCount = vals.filter(v =>
      !isNaN(Number(String(v).replace(/[,%]/g, '').trim()))
    ).length
    // 80% 以上非空值为数值 → 数值列
    const isNumeric = vals.length > 0 && numericCount / vals.length >= 0.8

    // 时间列判定: 列名含时间关键词，或值匹配日期格式
    const timeNameMatch = /时间|日期|date|time|月|年|day|month|year/i.test(col.name)
    const timeValMatch = vals.some(v =>
      /^\d{4}[-/]\d{1,2}([-/]\d{1,2})?/.test(String(v))
    )
    const isTime = timeNameMatch || (timeValMatch && vals.length > 0 && (
      vals.filter(v => /^\d{4}[-/]\d{1,2}/.test(String(v))).length / vals.length >= 0.5
    ))

    const uniqueVals = new Set(rows.slice(0, 100).map(r => String(r[col.value] ?? '')))
    return { col, isNumeric, isTime, uniqueCount: uniqueVals.size }
  })

  if (target === 'x') {
    // 优先时间列 → 第一个非数值列 → 第一列
    const timeCol = colTypes.find(c => c.isTime)
    if (timeCol) return [{ name: timeCol.col.name, value: timeCol.col.value }]
    const dim = colTypes.find(c => !c.isNumeric) || colTypes[0]
    return dim ? [{ name: dim.col.name, value: dim.col.value }] : []
  }
  if (target === 'y') {
    // 第一个数值列
    const metric = colTypes.find(c => c.isNumeric)
    return metric ? [{ name: metric.col.name, value: metric.col.value }] : []
  }
  if (target === 'series') {
    // 第二个非数值列 + 唯一值合理（2~10个）
    const nonNum = colTypes.filter(c => !c.isNumeric && !c.isTime)
    if (nonNum.length >= 2) {
      const candidate = nonNum[1]
      if (candidate.uniqueCount >= 2 && candidate.uniqueCount <= 10) {
        return [{ name: candidate.col.name, value: candidate.col.value }]
      }
    }
    return []
  }
  return []
}
```

### 3.3 ChartBlock.vue — 错误处理

**当前状态**：`initSettingsFromDefaults` 和 `applyAllSettings` 无 try-catch（[ChartBlock.vue:186-233](SQLBot-main/frontend/src/views/chat/chat-block/ChartBlock.vue#L186-L233)）。

**修改**：增加防御性错误处理。

```typescript
// ChartBlock.vue 修改（增量）

// 1. initSettingsFromDefaults — 增加 try-catch
function initSettingsFromDefaults(typeId: string) {
  if (!config.value) return
  try {
    const schema = config.value.chartTypes[typeId]?.settingsSchema
    if (!schema) return
    for (const [key, s] of Object.entries(schema) as [string, any][]) {
      if (!(key in chartSettings)) {
        chartSettings[key] = s.default
      }
    }
  } catch (e) {
    console.warn('[ChartBlock] initSettingsFromDefaults failed:', e)
  }
}

// 2. applyAllSettings — 增加 try-catch
function applyAllSettings() {
  nextTick(() => {
    try {
      chartRef.value?.applySettings?.({ ...chartSettings })
    } catch (e) {
      console.warn('[ChartBlock] applyAllSettings failed:', e)
    }
  })
}

// 3. sourceType 兜底 — 已实现，保持不变
const sourceType = computed(() => chartObject.value.type as ChartTypes | undefined)
```

### 3.4 ChartComponent.vue — renderChart 错误处理

**当前状态**：[ChartComponent.vue:66-73](SQLBot-main/frontend/src/views/chat/component/ChartComponent.vue#L66-L73) — `getChartInstance` 返回 undefined 时静默返回。

**修改**：增加 console.warn 日志，便于排查。

```typescript
// ChartComponent.vue: renderChart 修改

function renderChart() {
  chartInstance = getChartInstance(params.type, chartId.value)
  if (chartInstance) {
    chartInstance.showLabel = params.showLabel
    chartInstance.init(axis.value, params.data)
    chartInstance.render()
  } else {
    console.warn(
      `[ChartComponent] Unsupported chart type: "${params.type}". ` +
      `Available: [${Object.keys(CHART_TYPE_MAP).join(', ')}]`
    )
  }
}
```

---

## 四、数据流约定（与旧文档一致，无变更）

### 4.1 后端 → 前端 chart JSON 格式

```json
{
  "type": "column",
  "title": "月度收入统计",
  "axis": {
    "x": {"name": "月份", "value": "month"},
    "y": [{"name": "收入", "value": "income"}],
    "series": {"name": "类别", "value": "category"}
  },
  "columns": [
    {"name": "月份", "value": "month"},
    {"name": "收入", "value": "income"},
    {"name": "类别", "value": "category"}
  ]
}
```

**字段说明**：

| 字段 | 来源 | 必填 | 说明 |
|------|------|------|------|
| `type` | LLM 输出 | 是 | 图表类型 ID，后端校验后落库 |
| `title` | LLM 输出 | 是 | 图表标题 |
| `axis` | LLM 输出 | **否** | 若 LLM 提供了则前端直接使用；缺失时由 `_deriveAxis` 推导 |
| `columns` | **LLM 输出（强制）** | 是 | SQL 结果集列定义，`_deriveAxis` 的输入 |
| `settings` | — | **禁止** | 完全由前端工具栏 + localStorage 控制 |

### 4.2 数据库存储

**ChatRecord 表**：`chart` 字段存上述 JSON。旧数据中有 `axis` → 前端直接使用；旧数据中无 `columns` → `_deriveAxis` 从 `data.fields` 推导（已实现）。

---

## 五、容错机制总览

| 环节 | 故障场景 | 兜底行为 |
|------|---------|---------|
| LLM 列名映射 | axis 使用了不存在的列名 | `_validate_and_fix_chart_json` 移除无效 axis 字段 → 前端 `_deriveAxis` 推导 |
| LLM 类型选择 | 选了不兼容数据的类型 | 模板中 `data_constraints` 自检 → 降级 table；校验层兜底 |
| LLM 输出 | JSON 解析失败 | 降级 `{type:"table"}` |
| LLM 输出 | type 不在注册表 | 降级 `"table"` |
| LLM 输出 | 无 title | 从 SQL 表名生成 |
| Axis 推导 | columns 无明确维度/指标 | x 用第一列，y 用第一数值列 |
| Axis 推导 | 无数值列 | 显示为 table |
| G2/S2 渲染 | 不支持的 chart type | `renderChart` 打 warn 日志，不崩溃 |
| Settings 应用 | applySettings 异常 | console.warn，不阻塞 UI |
| 网络请求 | /chart-types 失败 | FALLBACK_CONFIG 兜底（已同步） |

---

## 六、实施步骤

### Step 1: 后端（核心—提升 LLM 命中率）

1. **executor.py**: 新增 `_build_columns_schema()`, `_validate_and_fix_chart_json()`, `_extract_sql_output_columns()`, `_generate_default_title()`
2. **executor.py**: 修改 `_generate_chart()` — `schema=""` → `columns_schema=_build_columns_schema(query.data)`，注入 `data_constraints` + `chart_selection_rules`
3. **template.yaml**: chart.user 增加 `{columns_schema}`, `{data_constraints}`, `{chart_selection_rules}` 占位符
4. **template.yaml**: chart.generate_rules 增加 3 条新规则（列名校验、数据形状自检、columns 强制输出）
5. **验证**: 发送测试问题 → 检查 SSE chart JSON 中 axis 列名是否正确

### Step 2: 前端 FALLBACK_CONFIG 同步

1. **useChartTypeConfig.ts**: 补充 table(1), column(1), bar(1), line(3), pie(2) 共 8 个缺失 settingsSchema 条目
2. **验证**: 停止后端 → 刷新页面 → 各图表类型的齿轮 popover 应显示完整设置列表

### Step 3: 前端错误处理 + _deriveAxis 增强

1. **DisplayChartBlock.vue**: `_deriveAxis` 改为抽样 20 行 + 80% 阈值 + 时间列检测
2. **ChartBlock.vue**: `initSettingsFromDefaults` + `applyAllSettings` 增加 try-catch
3. **ChartComponent.vue**: `renderChart` 中 getChartInstance 返回 undefined 时增加 console.warn

### Step 4: 验证测试

1. **LLM 准确度**: 发 5 个不同类型的图表问题 → 检查 chart JSON 中 axis 列名是否全部正确
2. **类型切换**: table → column → bar → line → pie → table（循环不空白）
3. **Settings**: 点击堆叠/标签/网格等按钮 → 图表有视觉变化
4. **LLM 容错**: 模拟后端返回 `{"type":"nonsense"}` → 降级到 table
5. **空数据**: SQL 返回 0 行 → 显示"暂无数据"
6. **旧数据兼容**: 用升级前的 chart JSON 测试，图表正常渲染
7. **FALLBACK_CONFIG**: 停止后端 API → 刷新 → 齿轮 popover 按钮完整

---

## 七、关键设计决策记录

| 决策 | 理由 |
|------|------|
| **保留 LLM 生成 axis** | LLM 理解 SQL 语义，轴映射比纯启发式推导准确。度量为 0 时不强求完美 |
| **不新增 chartMatcher.ts** | 规则应与 `chart_registry.py` 一致。运行时过滤应先通过 `dataConstraints` 注入 LLM 提示词让 LLM 自检，前端通过兼容列表引导。纯前端硬编码规则不可取 |
| **不新增 axisResolver.ts** | `_deriveAxis` 增强即满足。单独模块是纯重构，非 Phase 3a 紧急范围 |
| **不新增 ErrorBoundary.vue** | G2 是 canvas 命令式渲染，Vue ErrorBoundary 无法捕获。应在图表类 init() 中做防御 |
| **不修改 settings 架构** | 当前 localStorage + schema 驱动 + 统一齿轮 popover 已工作良好 |

---

## 附录 A: 完整 FALLBACK_CONFIG

以下是修正后与 chart_registry.py 完全一致的 FALLBACK_CONFIG。替换 [useChartTypeConfig.ts:109-175](SQLBot-main/frontend/src/views/chat/component/useChartTypeConfig.ts#L109-L175) 中的对应部分。

```typescript
const FALLBACK_CONFIG: ChartTypeConfig = {
  chartTypes: {
    table: {
      typeId: 'table', displayName: '明细表', icon: 'table', category: 'table',
      compatibleWith: ['column', 'bar', 'line'], dataConstraints: {},
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
      compatibleWith: ['bar', 'line'], dataConstraints: { min_metrics: 1, min_dimensions: 1 },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        stack: { desc_cn: '', type: 'bool', default: false, icon: 'stack', label_cn: '堆叠', show_when: { has_series: true } },
        sort: { desc_cn: '', type: 'select', default: 'none', icon: 'sort', label_cn: '排序', options: ['none', 'asc', 'desc'] },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    bar: {
      typeId: 'bar', displayName: '条形图', icon: 'bar', category: 'comparison',
      compatibleWith: ['column', 'line'], dataConstraints: { min_metrics: 1, min_dimensions: 1 },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        stack: { desc_cn: '', type: 'bool', default: false, icon: 'stack', label_cn: '堆叠', show_when: { has_series: true } },
        sort: { desc_cn: '', type: 'select', default: 'none', icon: 'sort', label_cn: '排序', options: ['none', 'asc', 'desc'] },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    line: {
      typeId: 'line', displayName: '折线图', icon: 'line', category: 'trend',
      compatibleWith: ['column', 'bar'], dataConstraints: { min_metrics: 1, min_dimensions: 1 },
      settingsSchema: {
        color_palette: { desc_cn: '', type: 'select', default: 'default', icon: 'palette', label_cn: '配色', options: ['default', 'warm', 'cool', 'business'] },
        smooth: { desc_cn: '', type: 'bool', default: false, icon: 'smooth', label_cn: '平滑' },
        show_area: { desc_cn: '', type: 'bool', default: false, icon: 'area', label_cn: '面积' },
        show_points: { desc_cn: '', type: 'bool', default: false, icon: 'point', label_cn: '数据点' },
        show_label: { desc_cn: '', type: 'bool', default: false, icon: 'label', label_cn: '标签' },
        number_format: { desc_cn: '', type: 'select', default: 'full', icon: 'number', label_cn: '数值格式', options: ['full', 'abbreviated', 'percent'] },
        grid: { desc_cn: '', type: 'bool', default: true, icon: 'grid', label_cn: '网格' },
        legend: { desc_cn: '', type: 'bool', default: true, icon: 'legend', label_cn: '图例', show_when: { has_series: true } },
        y_axis_zero: { desc_cn: '', type: 'bool', default: true, icon: 'axis', label_cn: 'Y轴归零' },
      },
      baseClass: 'BaseG2Chart', hasSsr: true, usesAxis: true,
    },
    pie: {
      typeId: 'pie', displayName: '饼图', icon: 'pie', category: 'proportion',
      compatibleWith: [], dataConstraints: { min_metrics: 1, max_metrics: 1, min_dimensions: 1, max_dimensions: 1, requires_series: true, max_series_cardinality: 10 },
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
```
