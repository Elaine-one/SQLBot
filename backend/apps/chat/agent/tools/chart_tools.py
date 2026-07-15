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
    chart_config: dict,
    memory: AgentMemory,
) -> dict:
    """
    Store a chart configuration linked to an existing query.

    The chart_config should follow the existing chart JSON schema
    (type, title, axis, columns, etc.).
    """
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

    chart.chart_config.update(modifications)
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
