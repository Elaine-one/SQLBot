"""
AgentProfile and dispatch entry point for the SQLBot Agent engine.

Three profiles:
  qa        — main Q&A: explore tables → write SQL → execute → chart
  analysis  — data analysis: pre-loaded data → interpret → text output
  predict   — data prediction: pre-loaded data → forecast → text output

Usage (chat.py):
  from apps.chat.agent.engine import (
      AgentProfile,
      build_qa_profile,
      build_analysis_profile,
      build_predict_profile,
      dispatch,
  )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional


@dataclass
class AgentProfile:
    """Agent role definition.

    Defines the system prompt, tool set, iteration budget, and
    post-processing strategy for a single agent profile.
    """

    name: str                               # "qa" | "analysis" | "predict"
    system_prompt: str                      # dedicated system prompt ("" = use graph.py default)
    tool_names: list[str] = field(default_factory=list)  # tools available to this profile
    terminal_tools: list[str] = field(default_factory=list)  # which of those are terminal
    max_iterations: int = 50
    post_process: str = "execute_and_chart"  # "execute_and_chart" | "text_and_chart"


# ── Profile factories ────────────────────────────────────────

def build_qa_profile() -> AgentProfile:
    """QA Agent — explore tables → write SQL → execute → chart."""
    return AgentProfile(
        name="qa",
        system_prompt="",   # graph.py builds it from template
        tool_names=[
            "search_relevant_tables",
            "get_table_metadata",
            "get_table_sample_data",
            "get_field_values",
            "load_skill",
            "preview_sql",
            "execute_sql_query",
            "analyze_query_result",
            "create_sql_query",
            "create_chart",
            "edit_sql_query",
            "edit_chart",
            "replace_sql_fragment",
            "ask_for_clarification",
        ],
        terminal_tools=[
            "create_sql_query",
            "edit_sql_query",
            "replace_sql_fragment",
            "edit_chart",
            "ask_for_clarification",
        ],
        max_iterations=50,
        post_process="execute_and_chart",
    )


def build_analysis_profile(base_record) -> AgentProfile:
    """Analysis Agent — data pre-loaded, explore with tools, emit charts."""
    prompt = _build_analysis_or_predict_prompt(base_record, mode="analysis")
    return AgentProfile(
        name="analysis",
        system_prompt=prompt,
        tool_names=[
            "get_data_summary",
            "get_data_preview",
            "analyze_query_result",
            "ask_for_clarification",
        ],
        terminal_tools=[
            "ask_for_clarification",
        ],
        max_iterations=10,
        post_process="text_and_chart",
    )


def build_predict_profile(base_record) -> AgentProfile:
    """Predict Agent — data pre-loaded, web search + charts."""
    prompt = _build_analysis_or_predict_prompt(base_record, mode="predict")
    return AgentProfile(
        name="predict",
        system_prompt=prompt,
        tool_names=[
            "get_data_summary",
            "get_data_preview",
            "analyze_query_result",
            "search_web",
            "ask_for_clarification",
        ],
        terminal_tools=[
            "ask_for_clarification",
        ],
        max_iterations=15,
        post_process="text_and_chart",
    )


# ── Data helpers ─────────────────────────────────────────────

def _compute_column_stats(rows: list, fields: list) -> dict:
    """Compute lightweight stats per column for prompt compression.

    Returns {field_name: {type: "number"|"category", stats: {...}}}
    so the LLM sees data characteristics without scanning raw rows.
    """
    import math

    if not rows or not fields:
        return {}

    stats: dict = {}
    for fi, fld in enumerate(fields):
        name = fld if isinstance(fld, str) else fld.get("name", f"col_{fi}")
        vals = []
        for r in rows:
            if isinstance(r, (list, tuple)):
                v = r[fi] if fi < len(r) else None
            elif isinstance(r, dict):
                v = r.get(name)
            else:
                v = None
            if v is not None and v != "":
                vals.append(v)

        if not vals:
            stats[name] = {"type": "empty", "note": "无有效值"}
            continue

        # Try numeric
        nums = []
        for v in vals:
            try:
                nums.append(float(v))
            except (ValueError, TypeError):
                pass

        if len(nums) > len(vals) * 0.7:
            stats[name] = {
                "type": "numeric",
                "count": len(nums),
                "min": round(min(nums), 2),
                "max": round(max(nums), 2),
                "avg": round(sum(nums) / len(nums), 2),
            }
        else:
            uniq = list(dict.fromkeys(vals))
            stats[name] = {
                "type": "category",
                "count": len(uniq),
                "samples": uniq[:5],
            }

    return stats


# ── System Prompt builder ────────────────────────────────────

def _build_analysis_or_predict_prompt(base_record, mode: str) -> str:
    """Build the system prompt for analysis/predict.

    Computes data stats + formats rows as Markdown table.
    For predict mode, guides the agent to use search_web for
    industry trends and external context.
    """
    import orjson
    from common.utils.utils import SQLBotLogUtil as _log

    # ── Parse data ──────────────────────────────────────────
    raw_data = base_record.data or {}
    if isinstance(raw_data, str):
        try:
            raw_data = orjson.loads(raw_data)
        except Exception:
            raw_data = {}
    data = raw_data if isinstance(raw_data, dict) else {}
    fields = data.get("fields", [])
    rows = data.get("data", [])
    sql = getattr(base_record, "sql", None)
    chart = getattr(base_record, "chart", None)

    # Parse chart type
    chart_type = "table"
    if chart:
        try:
            obj = orjson.loads(chart) if isinstance(chart, str) else chart
            chart_type = obj.get("type", "table") if isinstance(obj, dict) else "table"
        except Exception:
            pass

    # Compute stats (compact — detailed stats come from get_data_summary tool)
    col_stats = _compute_column_stats(rows, fields)
    sql_str = (sql or "")[:500]

    _log.info(f"[Agent:prompt] mode={mode} sql_len={len(sql or '')} "
              f"chart_type={chart_type} fields={len(fields)} rows={len(rows)} "
              f"stats_cols={len(col_stats)}")

    # ── Role-specific sections ───────────────────────────────
    if mode == "analysis":
        role = "数据分析师"
        task = """## 分析任务

1. **整体概况**：从统计数据中总结数据量级、时间覆盖、关键指标的总量/均值
2. **特征与趋势**：对比不同分类维度的差异，发现数值指标的分布规律
3. **异常发现**：检查极值、空值、不合理组合，标注需关注的异常点
4. **业务建议**：基于数据特征，给出 2-3 条可执行建议

## 工具使用

| 工具 | 场景 |
|------|------|
| `get_data_summary` | **第一步**：查看每个字段的统计信息（min/max/avg/distinct） |
| `get_data_preview` | 翻阅具体数据行（每页 20 行，按需多次调用） |
| `analyze_query_result` | 对特定维度做 AI 深度分析 |

> 你的分析完成后，系统会自动基于 SQL 和数据生成图表。
> 因此你不需要在文字中描述数据分布或罗列数值——图表会展示这些。
> 你的文字应该聚焦在：**为什么**会出现这些数据、**意味着**什么、**下一步**该做什么。

## 输出要求

- 每一步只做一件事：调工具 → 看结果 → 输出分析 → 决定下一步
- 关键数字用 **粗体** 标注
- 不罗列原始数据（图表已展示），提炼洞察和业务含义"""
    else:
        role = "数据预测师"
        task = """## 预测任务

你必须严格按以下步骤执行，**每一步都必须调用工具**，不可跳过：

1. **数据理解**：调用 `get_data_summary` 了解每个字段的统计特征
2. **数据翻阅**：感兴趣时用 `get_data_preview` 翻阅具体数据，识别数据模式
3. **外部搜索（必须执行）**：调用 `search_web` 搜索行业趋势和市场动态。必须至少调用一次，用数据中的关键维度组合搜索词。如果第一次搜索结果不够相关，换关键词重试。
4. **深度分析**：需要时用 `analyze_query_result` 做深度分析
5. **未来值预测**：综合内部数据和外部搜索结果，给出具体预测值或区间，标明预测周期
6. **风险与置信度**：说明预测的前提假设、不确定性来源、关键拐点

## 工具使用

| 工具 | 场景 |
|------|------|
| `get_data_summary` | **第一步**：查看每个字段的统计特征 |
| `get_data_preview` | 翻阅具体数据，识别数据模式 |
| `search_web` | **【必须调用】** 搜索行业趋势、市场报告、最新政策。不调用则预测缺乏外部依据 |
| `analyze_query_result` | 对特定维度做深度分析 |

> 你的分析完成后，系统会自动基于数据生成预测趋势图。
> 文字应聚焦在预测逻辑和业务判断，不重复图表中的数据趋势。

## `search_web` 使用指南

**必须至少调用一次 `search_web`**，否则预测结果不可信：

- 行业关键词 + "2026 趋势" / "增长率" / "市场规模"
- 数据中关键产品/类目 + "行情" / "预测"
- 时间特征 + "旺季" / "淡季"
- 如果第一次搜索结果不够相关，换关键词重试

## 输出要求

- **先列出搜索到的外部信息要点**（必须包含）
- 再分析历史数据趋势
- 然后给出预测及依据（结合外部信息说明）
- 最后说明预测的局限性和置信度
- 使用 Markdown 格式，层次清晰"""

    row_count = len(rows)
    field_list = ", ".join(
        f if isinstance(f, str) else f.get("name", "?") for f in fields[:20]
    ) if fields else "（无字段信息）"

    # Compact stats for quick orientation (1 line per field)
    compact_stats = []
    for name, s in col_stats.items():
        if s["type"] == "numeric":
            compact_stats.append(f"{name}: min={s['min']}, max={s['max']}, avg={s['avg']}")
        elif s["type"] == "category":
            compact_stats.append(f"{name}: {s['count']}种取值")
    stats_preview = "; ".join(compact_stats) if compact_stats else "（无统计）"

    record_id = f"r_base_{base_record.id}"

    return f"""你是{role}。用户的查询结果已经就绪。

## 查询

- 记录 ID: `{record_id}`（**所有工具调用必须使用此 ID**）
- SQL: {sql_str or "（无）"}
- 图表类型: {chart_type}
- 字段: {field_list}
- 总行数: {row_count}
- 快速统计: {stats_preview}

## 工作方式

不要一次性看完所有数据然后直接输出。像分析师一样逐步探索：

1. 先调用 `get_data_summary(record_id="{record_id}")` 了解每个字段的详细统计
2. 感兴趣时用 `get_data_preview(record_id="{record_id}", offset=0, limit=20)` 翻阅数据
3. 需要深入分析时用 `analyze_query_result(record_id="{record_id}")`

每调用一个工具后，基于工具返回的结果输出这一轮的分析发现，然后决定是否继续探索。

{task}

## 重要

- 数据为空时告知用户"查询结果为空"
- 不要调用与 SQL 生成/表探索相关的工具（如 create_sql_query、search_relevant_tables）
- 所有涉及数据分析的工具必须使用 record_id=`{record_id}`
- 中文输出，Markdown 格式，关键数字 **粗体**"""


# ── Dispatch entry point ─────────────────────────────────────

async def dispatch(
    profile: AgentProfile,
    llm_service,
    session,
    question: str = "",
    base_record=None,
) -> AsyncGenerator[str, None]:
    """Run an agent with the given profile, yielding SSE events.

    For analysis/predict (base_record is not None):
      - Injects the base_record's SQL + data into AgentMemory as a
        pre-executed QueryRecord so the agent doesn't need to explore.

    For QA (base_record is None):
      - Runs with the existing QA profile (all tools, full exploration).
    """
    import asyncio

    import orjson

    from common.utils.utils import SQLBotLogUtil as _log
    from apps.chat.agent.memory import AgentMemory, QueryRecord
    from apps.chat.agent.executor import AgentExecutor
    from apps.chat.agent.adapter import (
        init_agent_memory,
        _load_memory_from_db,
        _save_memory_to_db,
        _load_prompt_context,
    )
    from apps.chat.agent.tools.register_all import register_all_tools

    # Lazy-init tools
    if not hasattr(dispatch, "_tools_registered"):
        register_all_tools()
        dispatch._tools_registered = True

    llm = llm_service.llm
    record = getattr(llm_service, "record", None)
    chat_id = getattr(llm_service.chat_question, "chat_id", None)

    _log.info(f"[Agent:dispatch] START profile={profile.name} "
              f"has_base_record={base_record is not None} "
              f"chat_id={chat_id} record_id={getattr(record, 'id', None)} "
              f"tools={profile.tool_names or 'ALL'} "
              f"max_iter={profile.max_iterations} "
              f"post_process={profile.post_process}")

    # ── Build memory ──────────────────────────────────────
    base_memory = init_agent_memory(llm_service)

    if chat_id:
        memory = _load_memory_from_db(session, chat_id)
        memory.datasource_id = base_memory.datasource_id
        memory.datasource_type = base_memory.datasource_type
        memory.session = session
        memory.current_user = base_memory.current_user
        memory.ds = base_memory.ds
        memory.out_ds_instance = base_memory.out_ds_instance
        memory.sqlbot_name = base_memory.sqlbot_name
        memory.enable_sql_row_limit = base_memory.enable_sql_row_limit
        memory.context_record_count = base_memory.context_record_count
        memory.expand_thinking_block = base_memory.expand_thinking_block
    else:
        memory = base_memory
        memory.session = session

    # Per-turn fields
    memory.user_question = question
    memory.chat_id = chat_id
    memory.is_followup = False
    memory.record = record
    memory.terminal_triggered = False
    memory.iteration = 0
    memory.sql_retry_count = 0

    # Load prompt enrichment context
    _load_prompt_context(
        session, memory, question,
        oid=getattr(llm_service.current_user, "oid", 1),
        datasource_id=getattr(memory, "datasource_id", None),
        advanced_app_id=getattr(
            getattr(llm_service, "current_assistant", None), "id", None
        ) if getattr(llm_service, "current_assistant", None) else None,
    )

    # For analysis/predict: inject base_record data as a pre-executed query
    if base_record:
        rid = f"r_base_{base_record.id}"
        raw_data = base_record.data or {}
        if isinstance(raw_data, str):
            try:
                raw_data = orjson.loads(raw_data)
            except Exception:
                _log.info(f"[Agent:dispatch] failed to parse base_record.data as JSON")
                raw_data = {}
        row_count = len(raw_data.get("data", [])) if isinstance(raw_data, dict) else 0
        field_count = len(raw_data.get("fields", [])) if isinstance(raw_data, dict) else 0
        _log.info(f"[Agent:dispatch] inject base_record id={base_record.id} "
                  f"rows={row_count} fields={field_count}")
        memory.queries[rid] = QueryRecord(
            record_id=rid,
            sql=getattr(base_record, "sql", "") or "",
            tables_used=[],
            status="executed",
            data=raw_data,
            row_count=row_count,
            result_fields=raw_data.get("fields", []) if isinstance(raw_data, dict) else [],
        )

    # ── Emit record ID ────────────────────────────────────
    if memory.record:
        yield "data:" + orjson.dumps({
            "type": "id", "id": memory.record.id
        }).decode() + "\n\n"
        yield "data:" + orjson.dumps({
            "type": "question", "question": question
        }).decode() + "\n\n"

    # ── Run agent in background thread ─────────────────────
    queue: asyncio.Queue = asyncio.Queue()
    executor = AgentExecutor(llm, memory, queue, profile=profile)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, executor.run)

    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk

    # ── Save persistent state ─────────────────────────────
    if chat_id:
        _save_memory_to_db(session, chat_id, memory)
