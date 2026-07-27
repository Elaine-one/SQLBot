"""
图表类型注册表 — 单一配置源 (Single Source of Truth)
=====================================================

设计参考:
  - Metabase 可插拔可视化架构 (defineConfig / checkRenderable / per-type settings)
  - SQLBot LLM 驱动图表生成的实际情况 (约束注入提示词引导 LLM, 而非运行时拦截)

新增图表类型:
  1. 在 CHART_TYPE_REGISTRY 中添加一条 ChartTypeDef 记录
  2. 在 frontend charts/ 目录下创建对应的渲染类文件
  3. (可选) 在 g2-ssr/charts/ 创建 SSR 渲染文件

所有关键词匹配、LLM 提示词、前端类型配置、可切换逻辑
均从此注册表自动派生。

使用示例:
    from apps.chat.agent.chart_registry import (
        get_chart_type_from_keywords, get_chart_type_hint,
        get_all_types, get_selection_rules, get_frontend_config,
        validate_chart_type, get_compatible_types,
    )
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  数据类定义
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ChartTypeDef:
    """单个图表类型的完整定义。

    每个字段的用途:
      - 标识/展示: 所有消费者共用
      - LLM 提示词素材: 注入 template.yaml 模板变量, 引导 LLM 选择和生成
      - 运行时元数据: 前端注册、SSR 派发、可切换逻辑
    """

    # ── 标识与展示 ──
    type_id: str                        # "column", "bar", "line", "pie"
    keywords: List[str] = field(default_factory=list)
    display_name_cn: str = ""
    display_name_en: str = ""
    icon: str = ""

    # ── 视觉分类 ──
    category: str = ""                  # trend | comparison | proportion |
                                        # correlation | single_value | table

    # ── LLM 提示词素材 ──
    selection_rule_cn: str = ""         # "占比用 pie（≤10 类, 恰好 1 个数值字段）"
    selection_rule_en: str = ""

    # 数据形状约束 — 注入 LLM 提示词供 LLM 自检，也暴露给前端供可选灰显
    # 字段: min_metrics, max_metrics, min_dimensions, max_dimensions,
    #       requires_series, max_series_cardinality
    data_constraints: dict = field(default_factory=dict)

    # 类型专属配置 Schema — 一石三鸟:
    #   ① desc_cn/desc_en → 拼成 LLM 提示词文本, 注入 chart.user 模板
    #   ② type/icon/label_cn/default → 前端工具栏按钮动态渲染
    #   ③ key + value → 渲染类 applySettings() 映射到 G2/S2 options
    settings_schema: Dict[str, dict] = field(default_factory=dict)

    # ── 运行时元数据 ──
    compatible_with: List[str] = field(default_factory=list)  # 可切换到的类型
    base_class: str = "BaseG2Chart"     # BaseG2Chart | BaseChart
    has_ssr: bool = True
    uses_axis: bool = True              # True=axis 结构, False=columns 结构 (table)


# ═══════════════════════════════════════════════════════════════════════════════
#  注册表 — 新增图表类型只需在此添加记录
# ═══════════════════════════════════════════════════════════════════════════════

CHART_TYPE_REGISTRY: Dict[str, ChartTypeDef] = {

    "table": ChartTypeDef(
        type_id="table",
        keywords=["表格", "明细", "列表", "数据表", "table", "grid"],
        display_name_cn="明细表",
        display_name_en="Table",
        icon="table",
        category="table",
        selection_rule_cn="原始数据查看、多列明细展示用 table",
        selection_rule_en="raw data or multi-column detail → table",
        data_constraints={
            "min_metrics": 0, "max_metrics": 999,
            "min_dimensions": 0, "max_dimensions": 999,
        },
        settings_schema={
            "sort_column": {
                "desc_cn": "string, 默认排序列名, 默认第一个数值列",
                "type": "select", "default": "",
                "options": [],  # 动态填入列名
                "icon": "sort", "label_cn": "排序列",
            },
            "sort_order": {
                "desc_cn": "string, 排序方向 asc=升序 desc=降序, 默认 desc",
                "type": "select", "default": "desc",
                "options": ["asc", "desc"],
                "icon": "sort", "label_cn": "排序",
            },
            "page_size": {
                "desc_cn": "int, 分页行数, 20/50/100, 默认 50",
                "type": "select", "default": 50,
                "options": [20, 50, 100],
                "icon": "page", "label_cn": "分页",
            },
            "number_format": {
                "desc_cn": "string, 数值格式: full=完整 abbreviated=缩写(K/M/B) percent=百分比, 默认 full",
                "type": "select", "default": "full",
                "options": ["full", "abbreviated", "percent"],
                "icon": "number", "label_cn": "数值格式",
            },
        },
        compatible_with=[],
        base_class="BaseChart", has_ssr=False, uses_axis=False,
    ),

    "column": ChartTypeDef(
        type_id="column",
        keywords=["柱状图", "柱状", "柱形图", "column", "bar chart", "histogram"],
        display_name_cn="柱状图",
        display_name_en="Column Chart",
        icon="column",
        category="comparison",
        selection_rule_cn="分类对比用 column/bar；X 轴必须是有业务意义的分类维度（如店铺、SKU、供应商），禁止将时间列映射到 X 轴；Y 轴为数值。",
        selection_rule_en="category comparison → column/bar; X must be a meaningful categorical dimension (store, SKU, supplier), never a time column; Y is numeric.",
        data_constraints={
            "min_metrics": 1, "min_dimensions": 1,
            "recommended_x_type": "categorical",
            "required_channels": {
                "x": {"type": "dimension", "forbidden": ["temporal"], "max_cardinality": 60},
                "y": {"type": "metric", "min": 1, "max": 1},
                "color": {"type": "dimension", "optional": True, "max_cardinality": 10},
            },
        },
        settings_schema={
            "color_palette": {
                "desc_cn": "string, 配色方案, 默认 default",
                "type": "select", "default": "default",
                "options": ["default", "warm", "cool", "business"],
                "icon": "palette", "label_cn": "配色",
            },
            "grid": {
                "desc_cn": "bool, 是否显示网格线, 默认 true",
                "type": "bool", "default": True,
                "icon": "grid", "label_cn": "网格",
            },
            "stack": {
                "desc_cn": "bool, 是否堆叠显示多系列, 默认 true（多系列时堆叠，用户可关闭）",
                "type": "bool", "default": True,
                "icon": "stack", "label_cn": "堆叠",
                "show_when": {"has_series": True},
            },
            "sort": {
                "desc_cn": "string, Y轴排序, none=原始 asc=升序 desc=降序, 默认 none",
                "type": "select", "default": "none",
                "options": ["none", "asc", "desc"],
                "icon": "sort", "label_cn": "排序",
            },
            "show_label": {
                "desc_cn": "bool, 是否在柱上显示数值, 默认 false",
                "type": "bool", "default": False,
                "icon": "label", "label_cn": "标签",
            },
            "number_format": {
                "desc_cn": "string, 数值格式: full=完整 abbreviated=缩写(K/M/B) percent=百分比, 默认 full",
                "type": "select", "default": "full",
                "options": ["full", "abbreviated", "percent"],
                "icon": "number", "label_cn": "数值格式",
            },
            "legend": {
                "desc_cn": "bool, 是否显示图例, 默认 true",
                "type": "bool", "default": True,
                "icon": "legend", "label_cn": "图例",
                "show_when": {"has_series": True},
            },
        },
        compatible_with=["bar", "line", "pie"],
        base_class="BaseG2Chart", has_ssr=True, uses_axis=True,
    ),

    "bar": ChartTypeDef(
        type_id="bar",
        keywords=["条形图", "条形", "横向柱状", "bar", "horizontal"],
        display_name_cn="条形图",
        display_name_en="Bar Chart",
        icon="bar",
        category="comparison",
        selection_rule_cn="分类对比用 column/bar；X 轴必须是有业务意义的分类维度（如店铺、SKU、供应商），禁止将时间列映射到 X 轴；Y 轴为数值。长标签或类别多时优先用 bar。",
        selection_rule_en="category comparison → column/bar; X must be a meaningful categorical dimension, never a time column; Y is numeric. Prefer bar for long labels or many categories.",
        data_constraints={
            "min_metrics": 1, "min_dimensions": 1,
            "recommended_x_type": "categorical",
            "required_channels": {
                "x": {"type": "dimension", "forbidden": ["temporal"], "max_cardinality": 60},
                "y": {"type": "metric", "min": 1, "max": 1},
                "color": {"type": "dimension", "optional": True, "max_cardinality": 10},
            },
        },
        settings_schema={
            "color_palette": {
                "desc_cn": "string, 配色方案, 默认 default",
                "type": "select", "default": "default",
                "options": ["default", "warm", "cool", "business"],
                "icon": "palette", "label_cn": "配色",
            },
            "grid": {
                "desc_cn": "bool, 是否显示网格线, 默认 true",
                "type": "bool", "default": True,
                "icon": "grid", "label_cn": "网格",
            },
            "legend": {
                "desc_cn": "bool, 是否显示图例, 默认 true",
                "type": "bool", "default": True,
                "icon": "legend", "label_cn": "图例",
                "show_when": {"has_series": True},
            },
            "stack": {
                "desc_cn": "bool, 是否堆叠显示多系列, 默认 true（多系列时堆叠，用户可关闭）",
                "type": "bool", "default": True,
                "icon": "stack", "label_cn": "堆叠",
                "show_when": {"has_series": True},
            },
            "sort": {
                "desc_cn": "string, y轴排序, none=原始顺序 asc=升序 desc=降序, 默认 none",
                "type": "select", "default": "none",
                "options": ["none", "asc", "desc"],
                "icon": "sort", "label_cn": "排序",
            },
            "show_label": {
                "desc_cn": "bool, 是否在柱上显示数值, 默认 false",
                "type": "bool", "default": False,
                "icon": "label", "label_cn": "标签",
            },
            "number_format": {
                "desc_cn": "string, 数值格式: full=完整 abbreviated=缩写(K/M/B) percent=百分比, 默认 full",
                "type": "select", "default": "full",
                "options": ["full", "abbreviated", "percent"],
                "icon": "number", "label_cn": "数值格式",
            },
        },
        compatible_with=["column", "line", "pie"],
        base_class="BaseG2Chart", has_ssr=True, uses_axis=True,
    ),

    "line": ChartTypeDef(
        type_id="line",
        keywords=["折线图", "折线", "趋势", "走势", "变化", "line", "trend"],
        display_name_cn="折线图",
        display_name_en="Line Chart",
        icon="line",
        category="trend",
        selection_rule_cn="趋势 over time 用 line；X 轴应为时间或有序维度，禁止将无序分类维度（如店铺名）映射到 X 轴；Y 轴为连续数值。",
        selection_rule_en="trend over time → line; X must be temporal or ordinal, never an unordered categorical dimension like store name; Y is continuous numeric.",
        data_constraints={
            "min_metrics": 1, "min_dimensions": 1,
            "recommended_x_type": "temporal|ordinal",
            "required_channels": {
                "x": {"type": "dimension", "preferred": ["temporal", "ordinal"], "max_cardinality": 500},
                "y": {"type": "metric", "min": 1, "max": 1},
                "color": {"type": "dimension", "optional": True, "max_cardinality": 20},
            },
        },
        settings_schema={
            "color_palette": {
                "desc_cn": "string, 配色方案, 默认 default",
                "type": "select", "default": "default",
                "options": ["default", "warm", "cool", "business"],
                "icon": "palette", "label_cn": "配色",
            },
            "grid": {
                "desc_cn": "bool, 是否显示网格线, 默认 true",
                "type": "bool", "default": True,
                "icon": "grid", "label_cn": "网格",
            },
            "legend": {
                "desc_cn": "bool, 是否显示图例, 默认 true",
                "type": "bool", "default": True,
                "icon": "legend", "label_cn": "图例",
                "show_when": {"has_series": True},
            },
            "smooth": {
                "desc_cn": "bool, 是否平滑曲线, 默认 false",
                "type": "bool", "default": False,
                "icon": "smooth", "label_cn": "平滑",
            },
            "show_area": {
                "desc_cn": "bool, 是否填充线下区域, 默认 false (true=面积图效果)",
                "type": "bool", "default": False,
                "icon": "area", "label_cn": "面积",
            },
            "show_points": {
                "desc_cn": "bool, 是否显示数据点标记, 默认 false",
                "type": "bool", "default": False,
                "icon": "point", "label_cn": "数据点",
            },
            "show_label": {
                "desc_cn": "bool, 是否显示数值标签, 默认 false",
                "type": "bool", "default": False,
                "icon": "label", "label_cn": "标签",
            },
            "number_format": {
                "desc_cn": "string, 数值格式: full=完整 abbreviated=缩写(K/M/B) percent=百分比, 默认 full",
                "type": "select", "default": "full",
                "options": ["full", "abbreviated", "percent"],
                "icon": "number", "label_cn": "数值格式",
            },
            "y_axis_zero": {
                "desc_cn": "bool, Y轴是否强制从0开始, 默认 true (false=放大局部差异)",
                "type": "bool", "default": True,
                "icon": "axis", "label_cn": "Y轴归零",
            },
        },
        compatible_with=["column", "bar", "pie"],
        base_class="BaseG2Chart", has_ssr=True, uses_axis=True,
    ),

    "pie": ChartTypeDef(
        type_id="pie",
        keywords=["饼图", "饼", "占比", "扇形", "比例", "份额", "百分比",
                  "pie", "donut", "proportion"],
        display_name_cn="饼图",
        display_name_en="Pie Chart",
        icon="pie",
        category="proportion",
        selection_rule_cn="占比/份额用 pie；series 必须是分类维度且唯一值 ≤10，Y 轴必须全为正数。角度通道不支持负值，禁止用带正负的度量（如差异金额）直接画饼图；利润率、增长率等已经是百分比含义的字段不应画饼图（会误读为整体占比），应选 column/bar。",
        selection_rule_en="proportion/share → pie; series must be categorical with ≤10 unique values, Y must be all positive. Angle channel does not support negative values.",
        data_constraints={
            "min_metrics": 1, "max_metrics": 1,
            "min_dimensions": 1, "max_dimensions": 1,
            "requires_series": True,
            "max_series_cardinality": 10,
            "positive_metric": True,
            "required_channels": {
                "color": {"type": "dimension", "max_cardinality": 10},
                "theta": {"type": "metric", "positive": True, "ratio_forbidden": True},
            },
        },
        settings_schema={
            "color_palette": {
                "desc_cn": "string, 配色方案, 默认 default",
                "type": "select", "default": "default",
                "options": ["default", "warm", "cool", "business"],
                "icon": "palette", "label_cn": "配色",
            },
            "donut": {
                "desc_cn": "bool, 环形图 (内径 0.5), 默认 false (=饼图)",
                "type": "bool", "default": False,
                "icon": "donut", "label_cn": "环形",
            },
            "sort": {
                "desc_cn": "string, 扇区排序, none=原始顺序 asc=升序 desc=降序, 默认 none",
                "type": "select", "default": "none",
                "options": ["none", "asc", "desc"],
                "icon": "sort", "label_cn": "排序",
            },
            "show_label": {
                "desc_cn": "bool, 是否显示标签, 默认 true",
                "type": "bool", "default": True,
                "icon": "label", "label_cn": "标签",
            },
            "label_format": {
                "desc_cn": "string, 标签格式: name_value=名称+数值 name_percent=名称+百分比 value_percent=数值+百分比, 默认 name_value",
                "type": "select", "default": "name_value",
                "options": ["name_value", "name_percent", "value_percent"],
                "icon": "percent", "label_cn": "标签格式",
            },
            "legend": {
                "desc_cn": "bool, 是否显示图例, 默认 true",
                "type": "bool", "default": True,
                "icon": "legend", "label_cn": "图例",
            },
        },
        compatible_with=[],
        base_class="BaseG2Chart", has_ssr=True, uses_axis=True,
    ),

    # ═════════════════════════════════════════════════════════════════════════
    #  扩展示例 — 取消注释即可启用
    # ═════════════════════════════════════════════════════════════════════════

    # "area": ChartTypeDef(
    #     type_id="area",
    #     keywords=["面积图", "面积", "区域图", "area", "area chart"],
    #     display_name_cn="面积图",
    #     display_name_en="Area Chart",
    #     icon="area",
    #     category="trend",
    #     selection_rule_cn="累积趋势或数量级对比用 area",
    #     selection_rule_en="cumulative trend / magnitude → area",
    #     data_constraints={"min_metrics": 1, "min_dimensions": 1},
    #     settings_schema={
    #         "stack": "bool, 是否堆叠, 默认 true",
    #         "smooth": "bool, 是否平滑, 默认 false",
    #         "percent": "bool, 是否百分比堆叠, 默认 false",
    #     },
    #     compatible_with=["line", "column"],
    # ),

    # "scatter": ChartTypeDef(
    #     type_id="scatter",
    #     keywords=["散点图", "散点", "分布", "scatter", "bubble"],
    #     display_name_cn="散点图",
    #     display_name_en="Scatter Plot",
    #     icon="scatter",
    #     category="correlation",
    #     selection_rule_cn="相关性/分布/聚类用 scatter（恰好 2 个数值字段）",
    #     selection_rule_en="correlation/distribution → scatter (exactly 2 metrics)",
    #     data_constraints={
    #         "min_metrics": 2, "max_metrics": 2,
    #         "min_dimensions": 0, "max_dimensions": 1,
    #     },
    #     settings_schema={
    #         "point_size": "int, 点大小, 默认 3",
    #         "show_trendline": "bool, 是否显示趋势线, 默认 false",
    #     },
    #     compatible_with=[],
    # ),
}


# ═══════════════════════════════════════════════════════════════════════════════
#  派生函数 — 新增图表类型时以下代码全部不改
# ═══════════════════════════════════════════════════════════════════════════════

# ── 基础查询 ──────────────────────────────────────────────────────────────────

def get_all_types() -> List[str]:
    return list(CHART_TYPE_REGISTRY.keys())


def get_chart_def(type_id: str) -> Optional[ChartTypeDef]:
    return CHART_TYPE_REGISTRY.get(type_id)


def validate_chart_type(type_id: str) -> bool:
    return type_id in CHART_TYPE_REGISTRY


def get_compatible_types(type_id: str) -> List[str]:
    defn = CHART_TYPE_REGISTRY.get(type_id)
    return list(defn.compatible_with) if defn else []


# ── 按分类查询 ────────────────────────────────────────────────────────────────

def get_types_by_category(category: str) -> List[str]:
    return [t.type_id for t in CHART_TYPE_REGISTRY.values()
            if t.category == category]


def get_categories() -> Dict[str, List[str]]:
    cats: Dict[str, List[str]] = {}
    for t in CHART_TYPE_REGISTRY.values():
        cats.setdefault(t.category, []).append(t.type_id)
    return cats


# ── 关键词匹配 (用户意图信号) ─────────────────────────────────────────────────

def get_chart_type_from_keywords(question: str) -> str:
    """从用户提问匹配图表类型关键词。替代 executor.py:728-733 的 if/elif。

    返回匹配到的 type_id，无匹配返回空字符串。
    注意: 这是"用户意图"信号，不是最终决定——最终由 LLM 判断。
    """
    q = question.lower()
    for type_id, defn in CHART_TYPE_REGISTRY.items():
        for kw in defn.keywords:
            if kw.lower() in q:
                return type_id
    return ""


# ── LLM 提示词生成 ────────────────────────────────────────────────────────────

def get_supported_types_string(lang: str = "zh-CN") -> str:
    """图表类型列表字符串，注入 template.yaml。
    输出: "表格(table)、柱状图(column)、条形图(bar)、折线图(line)、饼图(pie)"
    """
    is_cn = "zh" in lang.lower()
    items = []
    for type_id, defn in CHART_TYPE_REGISTRY.items():
        name = defn.display_name_cn if is_cn else defn.display_name_en
        items.append(f"{name}({type_id})")
    return "、".join(items) if is_cn else ", ".join(items)


def get_selection_rules(lang: str = "zh-CN") -> str:
    """图表类型选择原则字符串，注入 template.yaml。
    输出: "趋势 over time 用 line；分类对比用 column/bar；占比用 pie；..."
    """
    is_cn = "zh" in lang.lower()
    rules = []
    for type_id, defn in CHART_TYPE_REGISTRY.items():
        rule = defn.selection_rule_cn if is_cn else defn.selection_rule_en
        if rule:
            rules.append(rule)
    separator = "；" if is_cn else "; "
    return separator.join(rules)


def get_settings_schema_text(type_id: str, lang: str = "zh-CN") -> str:
    """指定类型的专属配置 Schema 文本，注入 template.yaml chart.user。
    LLM 在生成图表 JSON 时可按需在 "settings" 字段中填充。

    从 settings_schema 的 desc_cn/desc_en 字段拼出 LLM 可读描述。
    """
    defn = CHART_TYPE_REGISTRY.get(type_id)
    if not defn or not defn.settings_schema:
        return ""

    is_cn = "zh" in lang.lower()
    name = defn.display_name_cn if is_cn else defn.display_name_en
    lines = [f"{name} 专属配置项 (可选，放在 JSON 的 \"settings\" 字段中):"]
    for key, s in defn.settings_schema.items():
        desc = s.get("desc_cn", "") if is_cn else s.get("desc_en", s.get("desc_cn", ""))
        if desc:
            lines.append(f"  - {key}: {desc}")
    return "\n".join(lines)


def get_data_constraints_text(lang: str = "zh-CN") -> str:
    """所有图表类型的数据形状约束汇总，注入 LLM 提示词供自检。"""
    is_cn = "zh" in lang.lower()
    lines = []
    for type_id, defn in CHART_TYPE_REGISTRY.items():
        dc = defn.data_constraints
        if not dc:
            continue
        name = defn.display_name_cn if is_cn else defn.display_name_en
        parts = [f"{name}({type_id}) 数据要求:"]
        if dc.get("requires_series"):
            parts.append("必须有分类字段(series)")
        if dc.get("max_series_cardinality"):
            parts.append(f"分类字段唯一值 ≤{dc['max_series_cardinality']}")
        if dc.get("min_metrics"):
            parts.append(f"至少 {dc['min_metrics']} 个数值字段")
        if dc.get("max_metrics") and dc["max_metrics"] < 999:
            parts.append(f"最多 {dc['max_metrics']} 个数值字段")
        if dc.get("min_dimensions"):
            parts.append(f"至少 {dc['min_dimensions']} 个维度字段")
        lines.append("；".join(parts))
    return "\n".join(lines) if lines else ""


# ── 智能类型提示 (融合关键词 + 数据形状) ──────────────────────────────────────

def get_chart_type_hint(question: str, query_data=None) -> str:
    """
    为 _generate_chart() 生成传给 LLM 的 chart_type 提示文本。

    输入:
      question: 用户原始提问
      query_data: SQL 执行结果 (有 fields 和 data 属性的对象), 可选

    逻辑:
      1. 关键词匹配 → 用户意图
      2. 如果匹配到, 直接返回该类型 (用户明确说了就用)
      3. 如果未匹配且有 query_data, 分析列类型推荐类型
      4. 都没有 → 返回空字符串 (LLM 自主决定)

    返回的字符串直接填入 template.yaml chart.user 的 {chart_type} 占位符。
    """
    # Step 1: 用户意图 (关键词)
    keyword_type = get_chart_type_from_keywords(question)
    if keyword_type:
        return keyword_type

    # Step 2: 数据形状推荐 (如果有关联结果)
    if query_data is not None:
        shape_type = _infer_type_from_data_shape(query_data)
        if shape_type:
            return shape_type

    return ""


def _is_temporal_field(name: str, col_type: str) -> bool:
    """判断字段是否为时间列（优先列名暗示）。"""
    if any(t in col_type for t in ("date", "time", "timestamp", "datetime")):
        return True
    return any(t in name.lower() for t in ("time", "date", "时间", "日期", "年", "月", "日"))


def _is_ordinal_field(name: str) -> bool:
    """通过列名判断是否为有序分类维度。"""
    return any(k in name.lower() for k in
               ("阶段", "序号", "排名", "位次", "周次", "轮次", "期数", "步骤", "级别",
                "年级", "版本", "迭代", "次序", "order", "rank", "stage", "phase",
                "step", "level", "round"))


def _infer_type_from_data_shape(query_data) -> str:
    """从 SQL 返回结果的列类型推导图表类型。"""
    try:
        fields = getattr(query_data, "fields", []) or []
        rows = getattr(query_data, "data", []) or []
    except Exception:
        return ""

    metrics = []     # 数值列
    dimensions = []  # 分类列
    time_cols = []   # 时间列
    ordinal_cols = []  # 序数列（按列名推断）

    for col in fields:
        name = getattr(col, "name", "")
        col_type = str(getattr(col, "type", "")).lower()

        is_numeric = any(t in col_type for t in
                         ("int", "float", "double", "decimal", "numeric", "number"))
        is_temporal = _is_temporal_field(name, col_type)

        if is_numeric:
            metrics.append(col)
        else:
            dimensions.append(col)
            if is_temporal:
                time_cols.append(col)
            elif _is_ordinal_field(name):
                ordinal_cols.append(col)

    n_metrics = len(metrics)
    n_dims = len(dimensions)
    n_time = len(time_cols)

    # 无指标
    if n_metrics == 0:
        return "table"

    # 单指标无维度 / 多指标无维度
    if n_metrics >= 1 and n_dims == 0:
        return "table"

    # 多指标无维度
    if n_metrics >= 2 and n_dims == 0:
        return "table"

    # 有时间维度 + 单指标 + 非时间维度：默认按分类对比，仅强趋势意图或维度基数极小时才用 line
    if n_time > 0 and n_metrics == 1:
        non_time_dims = [c for c in dimensions if c not in time_cols]
        if non_time_dims:
            dim_name = getattr(non_time_dims[0], "name", "").lower()
            dim_field = getattr(non_time_dims[0], "name", "")
            unique_vals = set()
            sample_size = min(len(rows), 200)
            for r in rows[:sample_size]:
                unique_vals.add(r.get(dim_field) if isinstance(r, dict) else getattr(r, dim_field, None))
            unique_count = len({v for v in unique_vals if v is not None})

            trend_keywords = ("趋势", "走势", "变化", "trend", "change", "over time", "随时间")
            has_trend_intent = any(kw in dim_name for kw in trend_keywords)

            # 非时间维度基数很小（≤2）或明确趋势意图 → line；否则 column 更适合对比
            if unique_count <= 2 or has_trend_intent:
                return "line"
            return "column"

        # 只有时间维度 + 数值 → line
        return "line"

    # 有时间维度但多指标：仍倾向趋势
    if n_time > 0:
        return "line"

    # 单指标单维度
    if n_metrics == 1 and n_dims == 1:
        dim_field = getattr(dimensions[0], "name", "")
        metric_name = getattr(metrics[0], "name", "")
        unique_vals = set()
        sample_size = min(len(rows), 200)
        for r in rows[:sample_size]:
            unique_vals.add(r.get(dim_field) if isinstance(r, dict) else getattr(r, dim_field, None))
        unique_count = len({v for v in unique_vals if v is not None})

        # 饼图候选：基数 2~10、指标非比率、无负值
        if 2 <= unique_count <= 10:
            is_ratio = any(k in metric_name.lower() for k in
                           ("率", "%", "percent", "ratio", "占比", "份额"))
            metric_field = getattr(metrics[0], "name", "")
            has_negative = False
            for r in rows[:sample_size]:
                v = r.get(metric_field) if isinstance(r, dict) else getattr(r, metric_field, None)
                try:
                    if v is not None and float(v) < 0:
                        has_negative = True
                        break
                except (TypeError, ValueError):
                    continue
            if not is_ratio and not has_negative:
                return "pie"
        return "column"

    # 一般情况
    if n_metrics >= 1 and n_dims >= 1:
        return "column"

    return ""


# ── 前端 API ──────────────────────────────────────────────────────────────────

def get_frontend_config(lang: str = "zh-CN") -> dict:
    """生成前端所需的完整图表类型配置 (供 GET /api/chat/chart-types 端点)。

    返回:
    {
        "chartTypes": {
            "pie": {
                "typeId": "pie",
                "displayName": "饼图",
                "icon": "pie",
                "category": "proportion",
                "compatibleWith": [],
                "dataConstraints": {"min_metrics": 1, "requires_series": true, ...},
                "settingsSchema": {"inner_radius": "float 0-1, ...", ...},
                "baseClass": "BaseG2Chart",
                "hasSsr": true,
                "usesAxis": true,
            }, ...
        },
        "allTypeIds": ["table", "column", "bar", "line", "pie"],
        "categories": {"trend": ["line"], "comparison": ["column", "bar"], ...},
        "defaultType": "table",
    }
    """
    is_cn = "zh" in lang.lower()
    types = {}
    for type_id, defn in CHART_TYPE_REGISTRY.items():
        types[type_id] = {
            "typeId": type_id,
            "displayName": defn.display_name_cn if is_cn else defn.display_name_en,
            "icon": defn.icon,
            "category": defn.category,
            "compatibleWith": defn.compatible_with,
            "dataConstraints": defn.data_constraints,
            "settingsSchema": defn.settings_schema,  # 结构化 dict，前端直接使用
            "baseClass": defn.base_class,
            "hasSsr": defn.has_ssr,
            "usesAxis": defn.uses_axis,
        }

    return {
        "chartTypes": types,
        "allTypeIds": list(CHART_TYPE_REGISTRY.keys()),
        "categories": get_categories(),
        "defaultType": "table",
    }


# ── g2-ssr ────────────────────────────────────────────────────────────────────

def get_ssr_chart_types() -> List[str]:
    return [tid for tid, d in CHART_TYPE_REGISTRY.items() if d.has_ssr]
