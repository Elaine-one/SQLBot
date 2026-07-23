"""
Chart tools for the SQLBot Agent.

create_chart — stores a chart linked to a QueryRecord.
edit_chart   — updates chart config in-place (TERMINAL tool).

Chart generation via LLM is handled by the AgentExecutor post-processing,
not by these tools directly.  These tools manage the structured state.
"""

from __future__ import annotations

from apps.chat.agent.memory import AgentMemory, ChartRecord


async def create_chart(
    record_id: str,
    chart_config: dict | str,
    memory: AgentMemory,
) -> dict:
    """
    Store a chart configuration linked to an existing query.

    chart_config may be a dict or a JSON string (LLMs sometimes serialize).
    Normalizes to dict on entry.
    """
    import json as _json

    if isinstance(chart_config, str):
        try:
            chart_config = _json.loads(chart_config)
        except Exception:
            return {"success": False, "error": "chart_config JSON 解析失败"}

    if not isinstance(chart_config, dict):
        return {"success": False, "error": "chart_config 必须是 dict 或 JSON 字符串"}

    # Normalize LLM-generated flat keys → frontend-compatible columns+axis format
    chart_config = _normalize_chart_config_dict(chart_config)

    if record_id not in memory.queries:
        return {
            "success": False,
            "error": f"查询 {record_id} 不存在，请先创建查询",
            "available_queries": memory.available_query_ids(),
        }

    chart_ref = _next_chart_ref()
    memory.charts[chart_ref] = ChartRecord(
        chart_ref=chart_ref,
        record_id=record_id,
        chart_config=chart_config,
    )
    memory.mark_chart_this_turn(chart_ref)
    return {
        "success": True,
        "chart_ref": chart_ref,
        "record_id": record_id,
        "type": chart_config.get("type", "unknown"),
    }


async def edit_chart(
    chart_ref: str,
    modifications: dict,
    memory: AgentMemory,
) -> dict:
    """
    Update an existing chart's configuration (TERMINAL tool).

    modifications: dict of config keys to update, e.g. {"type": "column"}.
    Only the specified keys are changed; the rest of the config is preserved.

    On success → memory.terminal_triggered = True.
    The executor will NOT re-execute SQL when only the chart is edited.
    """
    chart = memory.charts.get(chart_ref)
    if not chart:
        return {
            "success": False,
            "error": f"图表 {chart_ref} 不存在",
            "available_charts": memory.available_chart_refs(),
        }

    # Normalize modifications (LLM may pass flat keys like xField/yField)
    modifications = _normalize_chart_config_dict(dict(modifications))
    chart.chart_config.update(modifications)

    # Mark for this turn so the executor re-emits the edited chart
    memory.mark_chart_this_turn(chart_ref)
    memory.terminal_triggered = True
    return {
        "success": True,
        "chart_ref": chart_ref,
        "updated": list(modifications.keys()),
    }


# ── helpers ────────────────────────────────────────────────

_chart_counter: int = 0


def _next_chart_ref() -> str:
    global _chart_counter
    _chart_counter += 1
    return f"chart_{_chart_counter:03d}"


def _normalize_chart_config_dict(cfg: dict) -> dict:
    """Convert LLM-generated xField/yField/seriesField to frontend format.

    DisplayChartBlock expects: columns + axis.{x,y,series}
    LLMs often produce: xField, yField, seriesField (flat keys)

    If xField/yField/seriesField are present, they REPLACE any existing
    columns (LLM may pass both formats, but the flat keys take priority).
    Deduplication ensures column values are unique.
    """
    if not isinstance(cfg, dict):
        return cfg

    has_flat = bool(cfg.get("xField") or cfg.get("yField") or cfg.get("seriesField"))
    # Already normalized and no flat keys to convert
    if cfg.get("columns") and cfg.get("axis") and not has_flat:
        return cfg

    axis = dict(cfg.get("axis") or {})

    # Start fresh if flat keys are present (they override any existing columns)
    fields = [] if has_flat else list(cfg.get("columns") or [])
    seen = {f["value"] for f in fields if isinstance(f, dict) and f.get("value")}

    xf = cfg.pop("xField", None)
    if xf and xf not in seen:
        fields.append({"name": xf, "value": xf})
        seen.add(xf)
        axis.setdefault("x", {"name": xf, "value": xf})

    yf = cfg.pop("yField", None)
    if yf and yf not in seen:
        fields.append({"name": yf, "value": yf})
        seen.add(yf)
        axis.setdefault("y", {"title": yf})

    sf = cfg.pop("seriesField", None)
    if sf and sf not in seen:
        fields.append({"name": sf, "value": sf})
        seen.add(sf)
        axis.setdefault("series", {"name": sf, "value": sf})

    # Pie charts: axis.x is actually the category → move to axis.series
    if cfg.get("type") == "pie" and axis.get("x") and not axis.get("series"):
        axis["series"] = axis.pop("x")

    # Build columns from axis if missing (DisplayChartBlock needs columns)
    if not fields:
        for ax_key in ("x", "y", "series"):
            ax_val = axis.get(ax_key)
            if isinstance(ax_val, dict) and ax_val.get("value"):
                fields.append({"name": ax_val.get("name", ax_val["value"]), "value": ax_val["value"]})
            elif isinstance(ax_val, list):
                for item in ax_val:
                    if isinstance(item, dict) and item.get("value"):
                        fields.append({"name": item.get("name", item["value"]), "value": item["value"]})

    if not fields:
        return cfg

    cfg["columns"] = fields
    cfg["axis"] = axis
    return cfg
