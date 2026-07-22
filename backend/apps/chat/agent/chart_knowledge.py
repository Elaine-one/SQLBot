"""
图表通道语义知识库加载器。

加载 chart_channel_knowledge.json，提供:
  1. generate_llm_knowledge_text() → 注入 LLM 提示词的文本
  2. validate_chart_semantics()   → 语义级图表验证（超越列名校验）
  3. build_correction_prompt()    → 结构化纠错反馈
"""

import json
import re
from pathlib import Path
from typing import Any

_KNOWLEDGE: dict[str, Any] | None = None


def _load_knowledge() -> dict[str, Any]:
    """惰性加载知识库 JSON"""
    global _KNOWLEDGE
    if _KNOWLEDGE is not None:
        return _KNOWLEDGE
    this_dir = Path(__file__).parent
    kb_file = this_dir / "chart_channel_knowledge.json"
    try:
        _KNOWLEDGE = json.loads(kb_file.read_text(encoding="utf-8"))
    except Exception:
        _KNOWLEDGE = {}
    return _KNOWLEDGE


# ═══════════════════════════════════════════════════════════════════════════════
#  1. LLM 提示词生成 — 将知识库格式化为 LLM 可理解的结构化文本
# ═══════════════════════════════════════════════════════════════════════════════

def generate_llm_knowledge_text(chart_type: str = "", lang: str = "zh-CN") -> str:
    """生成注入 LLM prompt 的知识库文本。chart_type 为空则输出完整知识库。"""
    kb = _load_knowledge()
    if not kb:
        return ""

    is_cn = "zh" in lang.lower()
    lines: list[str] = []

    # ── 字段名启发规则 ──
    heuristics = kb.get("field_name_heuristics", {}).get("rules", [])
    if heuristics:
        lines.append("## 字段名 → 通道类型 推断规则（按名称猜类型）")
        lines.append("| 字段名匹配模式 | 通道类型 | 适合的图表 | 禁止 |")
        lines.append("|---|---|---|---|")
        for r in heuristics:
            patterns = r.get("patterns_cn", [])
            channel = (r.get("channel", "unknown")
                       .replace("metric.", "指标/")
                       .replace("categorical.", "分类/"))
            suitable = ", ".join(r.get("suitable_for", []) or ["所有"])
            forbidden_parts: list[str] = []
            for f in r.get("hard_forbidden_for", []) or []:
                forbidden_parts.append(f"❌ {f}")
            for w in r.get("warning_for", []) or []:
                forbidden_parts.append(f"⚠️ {w}")
            forbidden = "; ".join(forbidden_parts) if forbidden_parts else "无"
            pattern_str = ", ".join(f"/{p}/" for p in patterns[:3])
            lines.append(f"| {pattern_str} | {channel} | {suitable} | {forbidden} |")
        lines.append("")

    # ── 图表类型通道定义 ──
    chart_types = kb.get("chart_type_channels", {})
    types_to_show = [chart_type] if chart_type and chart_type in chart_types else list(chart_types.keys())
    for ct in types_to_show:
        info = chart_types.get(ct, {})
        if not info:
            continue
        name = info.get("label_cn", ct)
        lines.append(f"## {name} ({ct})")
        lines.append(f"意图关键词: {', '.join(info.get('intent_keywords_cn', []))}")
        lines.append("")

        channels = info.get("channels", {})
        for ch_name, ch_spec in channels.items():
            required = "✅ 必须" if ch_spec.get("required") else "⭕ 可选"
            ch_type = ch_spec.get("type", "?")
            preferred = ch_spec.get("preferred_types", [])
            forbidden = ch_spec.get("forbidden_types", [])
            max_card = ch_spec.get("max_cardinality", "无")
            desc = ch_spec.get("description_cn", "")
            examples = ch_spec.get("example_cn", "")

            lines.append(f"  **{ch_name}** ({required}, 类型: {ch_type})")
            lines.append(f"  - 说明: {desc}")
            if preferred:
                lines.append(f"  - 优先: {', '.join(preferred)}")
            if forbidden:
                lines.append(f"  - ❌ 禁止: {', '.join(forbidden)}")
            if max_card and max_card != "无":
                lines.append(f"  - 最多 {max_card} 个唯一值")
            if examples:
                lines.append(f"  - 示例: {examples}")
            lines.append("")

        # anti-patterns
        anti = info.get("anti_patterns", [])
        if anti:
            lines.append("  **🚫 常见错误（绝不能犯）：**")
            for a in anti:
                lines.append(f"  {a}")
            lines.append("")

        # hard fail conditions
        hard_fails = info.get("hard_fail_conditions", [])
        if hard_fails:
            lines.append("  **💀 硬性失败条件（违反后必须降级为 table 或其他类型）：**")
            for hf in hard_fails:
                lines.append(f"  - {hf}")
            lines.append("")

    # ── 自检清单 ──
    checklist = kb.get("generation_checklist", {}).get("steps", [])
    if checklist:
        lines.append("## 📋 生成前自检清单")
        for s in checklist:
            lines.append(f"  {s['step']}. {s['action']}")
        lines.append("")

    # ── 常见错误示例 ──
    mistakes = kb.get("common_mistakes", {}).get("examples", [])
    if mistakes:
        lines.append("## ⚠️ 警示教育：LLM 常犯错误")
        for i, m in enumerate(mistakes, 1):
            lines.append(f"  {i}. ❌ 错误: {m['mistake_cn']}")
            lines.append(f"     原因: {m['why_wrong']}")
            lines.append(f"     ✅ 正确: {m['correct']}")
            lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. 语义验证 — 超越列名匹配，增加通道类型语义检查
# ═══════════════════════════════════════════════════════════════════════════════

def validate_chart_semantics(chart: dict, query_data: dict) -> list[str]:
    """语义级图表验证。返回错误信息列表（空 = 验证通过）。

    比 _validate_and_fix_chart_json 更深层：
      - 仅校验列名是否存在  → _validate_and_fix_chart_json
      - 校验列的类型语义是否匹配通道要求 → 本函数
    """
    kb = _load_knowledge()
    if not kb:
        return []

    errors: list[str] = []
    chart_type = chart.get("type", "")
    type_info = kb.get("chart_type_channels", {}).get(chart_type, {})
    if not type_info:
        return []  # 未知类型不验证（_validate_and_fix_chart_json 已处理）

    channels = type_info.get("channels", {})
    axis = chart.get("axis", {})
    if not axis:
        return []

    # 从 query_data 提取列的类型元数据
    column_types = _extract_column_types(query_data)

    for ch_name, ch_spec in channels.items():
        if ch_name == "color" and ch_spec.get("required") is not True:
            continue  # 可选通道不验证

        # 找到该通道对应的 axis 字段
        axis_value = _get_axis_value_for_channel(axis, ch_name, chart_type)
        if not axis_value:
            if ch_spec.get("required"):
                errors.append(
                    f"缺少必须的 {ch_name} 通道字段。"
                    f"说明: {ch_spec.get('description_cn', '')}"
                )
            continue

        col_meta = column_types.get(axis_value, {})
        if not col_meta:
            continue  # 列名验证已在 _validate_and_fix_chart_json 处理

        # 检查 channel type
        required_type = ch_spec.get("type")
        if required_type == "metric" and not _is_numeric_column(col_meta.get("samples", [])):
            errors.append(
                f"channel '{ch_name}' 要求 metric 类型，但字段 '{axis_value}' "
                f"不是数值列。请选择数值字段。"
            )

        if required_type == "dimension" and _is_numeric_column(col_meta.get("samples", [])):
            field_name = col_meta.get("name", axis_value)
            if not _is_ratio_field(field_name):  # 比率字段可能小数值被误判为 dimension
                # 数值列做维度通常是错误
                if not _is_temporal_field(col_meta.get("name", "")):
                    errors.append(
                        f"channel '{ch_name}' 要求 dimension 类型，但字段 '{axis_value}' "
                        f"看起来很像是数值指标。请确认这是否真的是分类维度。"
                    )

        # 检查 forbidden_types
        forbidden = ch_spec.get("forbidden_types", [])
        for ftype in forbidden:
            if _matches_channel_type(axis_value, col_meta, ftype):
                errors.append(
                    f"{chart_type} 的 {ch_name} 通道禁止 '{ftype}' 类型字段，"
                    f"但 '{axis_value}' 被检测为 '{ftype}'。"
                )

        # 检查 ratio_forbidden (pie theta)
        if ch_spec.get("ratio_forbidden") and _is_ratio_field(col_meta.get("name", axis_value)):
            errors.append(
                f"pie 的 theta 通道禁止比率/百分比字段。'{axis_value}' "
                f"是比率字段，饼图会将其错误显示为整体占比。请改用 column 或 bar。"
            )

        # 检查 positive (pie theta)
        if ch_spec.get("positive") and _has_negative_values(col_meta.get("samples", [])):
            errors.append(
                f"pie 的 theta 通道要求所有值为正数，"
                f"但字段 '{axis_value}' 包含负值。请改用 column 或 bar。"
            )

        # 检查 max_cardinality
        max_card = ch_spec.get("max_cardinality")
        if max_card:
            unique_count = col_meta.get("unique_count", 0)
            if unique_count > max_card:
                errors.append(
                    f"{chart_type} 的 {ch_name} 通道最多 {max_card} 个唯一值，"
                    f"但 '{axis_value}' 有 {unique_count} 个唯一值。"
                    f"建议: 做 TopN 聚合或改用其他图表类型。"
                )

    return errors


def _extract_column_types(query_data: dict) -> dict[str, dict]:
    """从 query_data 提取每列的名称和采样值"""
    result: dict[str, dict] = {}
    fields = query_data.get("fields", [])
    data_rows = query_data.get("data", [])
    sample_rows = data_rows[:20] if data_rows else []

    for i, field in enumerate(fields):
        if isinstance(field, dict):
            name = field.get("name", f"col_{i}")
        else:
            name = str(field)

        samples: list[Any] = []
        unique_vals: set[str] = set()
        for row in (data_rows or [])[:100]:
            val = None
            if isinstance(row, (list, tuple)) and i < len(row):
                val = row[i]
            elif isinstance(row, dict) and name in row:
                val = row[name]
            if val is not None and val != "":
                unique_vals.add(str(val))

        for row in sample_rows:
            if isinstance(row, (list, tuple)) and i < len(row):
                samples.append(row[i])
            elif isinstance(row, dict) and name in row:
                samples.append(row[name])

        result[name] = {
            "name": name,
            "samples": samples,
            "unique_count": len(unique_vals),
        }

    return result


def _get_axis_value_for_channel(axis: dict, ch_name: str, chart_type: str) -> str | None:
    """将知识库的通道名映射到 axis JSON 的实际字段名"""
    # pie 的特殊映射
    if chart_type == "pie":
        if ch_name == "theta":
            y = axis.get("y")
            if isinstance(y, list) and y:
                return y[0].get("value")
            if isinstance(y, dict):
                return y.get("value")
        if ch_name == "color":
            s = axis.get("series")
            if isinstance(s, dict):
                return s.get("value")
        return None

    # 标准映射
    ch_to_axis = {
        "x": lambda a: (a.get("x") or {}).get("value") if isinstance(a.get("x"), dict) else None,
        "y": lambda a: (
            a["y"][0].get("value") if isinstance(a.get("y"), list) and a["y"]
            else a.get("y", {}).get("value") if isinstance(a.get("y"), dict)
            else None
        ),
        "color": lambda a: (a.get("series") or {}).get("value") if isinstance(a.get("series"), dict) else None,
    }
    getter = ch_to_axis.get(ch_name)
    return getter(axis) if getter else None


def _is_numeric_column(samples: list[Any]) -> bool:
    """≥80% 的非空样本可转为数字"""
    non_empty = [s for s in samples if s is not None and s != ""]
    if not non_empty:
        return False
    numeric = sum(
        1 for s in non_empty
        if not isinstance(s, bool)
        and _can_be_number(str(s))
    )
    return numeric / len(non_empty) >= 0.8


def _can_be_number(s: str) -> bool:
    s = s.replace(",", "").replace("%", "").strip()
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False


def _is_ratio_field(name: str) -> bool:
    return bool(re.search(r"率|%|percent|ratio|占比|份额|百分比", name, re.IGNORECASE))


def _is_temporal_field(name: str) -> bool:
    return bool(re.search(
        r"时间|日期|date|time|年|月|日|timestamp|datetime|year|month|day",
        name, re.IGNORECASE
    ))


def _has_negative_values(samples: list[Any]) -> bool:
    for s in samples:
        try:
            if float(str(s).replace(",", "").strip()) < 0:
                return True
        except (ValueError, TypeError):
            continue
    return False


def _matches_channel_type(field_name: str, col_meta: dict,
                          channel_subtype: str) -> bool:
    """判断列是否匹配给定的通道子类型"""
    samples = col_meta.get("samples", [])
    is_numeric = _is_numeric_column(samples) if samples else False
    name = col_meta.get("name", field_name)

    if channel_subtype == "temporal":
        return _is_temporal_field(name)
    if channel_subtype == "categorical.nominal":
        return not is_numeric and not _is_temporal_field(name)
    if channel_subtype == "categorical.ordinal":
        return not is_numeric and bool(re.search(
            r"阶段|序号|排名|星级|等级|优先级|order|rank|stage|level|grade|tier",
            name, re.IGNORECASE
        ))
    return False


# ═══════════════════════════════════════════════════════════════════════════════
#  3. 结构化纠错反馈 — 比纯文本更精准
# ═══════════════════════════════════════════════════════════════════════════════

def build_correction_prompt_semantic(errors: list[str],
                                     chart_type: str,
                                     valid_columns: set[str],
                                     columns_schema_text: str) -> str:
    """生成语义级纠错提示，比纯文本更精准。

    补充 executor._build_chart_correction_prompt（它只处理列名错误）。
    """
    kb = _load_knowledge()
    type_info = kb.get("chart_type_channels", {}).get(chart_type, {})

    lines = [
        "⚠️ 语义级校验发现以下问题，请修正后重新生成：",
        "",
    ]
    for i, err in enumerate(errors, 1):
        lines.append(f"  {i}. {err}")

    lines.append("")
    lines.append("## 参考信息")
    lines.append(f"- 图表类型: {chart_type} ({type_info.get('label_cn', chart_type)})")
    lines.append(f"- 可用列: {', '.join(sorted(valid_columns))}")
    lines.append("")

    # 注入该类型的通道定义
    channels = type_info.get("channels", {})
    if channels:
        lines.append("## 通道要求")
        for ch_name, ch_spec in channels.items():
            required = "必须" if ch_spec.get("required") else "可选"
            lines.append(f"- {ch_name}: {required}, 类型={ch_spec.get('type')}")
            if ch_spec.get("preferred_types"):
                lines.append(f"  优先: {', '.join(ch_spec['preferred_types'])}")
            if ch_spec.get("forbidden_types"):
                lines.append(f"  禁止: {', '.join(ch_spec['forbidden_types'])}")

    lines.append("")
    lines.append("## 修正要求")
    lines.append("- 若无法找到合适的列满足通道要求，请改用 type='table'")
    lines.append("- 不要编造不存在的列名")
    lines.append("- 请直接输出修正后的 JSON")

    return "\n".join(lines)
