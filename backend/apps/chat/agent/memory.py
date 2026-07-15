"""
Structured memory for the SQLBot Agent.

Inspired by Metabase's memory.clj — tracks queries and charts by ID
so that subsequent tool calls can reference, edit, and compose them.

State does NOT enter the LLM context directly; only lightweight
summaries are injected via get_context_for_llm().
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class QueryRecord:
    """A single SQL query tracked by the agent.

    data is the full query result (fields + rows), used within a single
    turn for chart generation.  For cross-turn persistence, only metadata
    (row_count + result_fields) is serialized to keep memory_state small.
    The full data lives in ChatRecord.data for frontend display.
    """

    record_id: str
    sql: str                              # original LLM-authored SQL
    compiled_sql: str = ""                # executable SQL (datasets expanded)
    tables_used: list[str] = field(default_factory=list)
    status: str = "created"  # created | executed | edited | failed
    sql_history: list[dict] = field(default_factory=list)
    data: Optional[dict] = None           # full result (not persisted)
    row_count: int = 0                    # persisted metadata
    result_fields: list[str] = field(default_factory=list)  # persisted metadata
    executed_at: Optional[datetime] = None

    def add_version(self, previous_sql: str) -> None:
        self.sql_history.append({
            "version": len(self.sql_history) + 1,
            "previous_sql": previous_sql,
            "modified_at": datetime.now().isoformat(),
        })


@dataclass
class ChartRecord:
    """A single chart tracked by the agent."""

    chart_ref: str
    record_id: str  # FK → QueryRecord
    chart_config: dict = field(default_factory=dict)
    image_url: Optional[str] = None


@dataclass
class AgentMemory:
    """
    Structured agent memory.

    queries  — {record_id: QueryRecord}
    charts   — {chart_ref: ChartRecord}
    explored_tables — {table_name: {fields: [...], ...}} (cross-turn cache)

    This state is NOT appended to the LLM context.  Only the result of
    get_context_for_llm() is injected, and only when the agent is handling
    a follow-up question.
    """

    # ── produced artifacts ──────────────────────────────────
    queries: dict[str, QueryRecord] = field(default_factory=dict)
    charts: dict[str, ChartRecord] = field(default_factory=dict)

    # ── schema exploration cache ────────────────────────────
    explored_tables: dict[str, dict] = field(default_factory=dict)
    _search_cache: dict = field(default_factory=dict)  # query → search results

    # ── conversation context ────────────────────────────────
    conversation_summary: str = ""
    conversation_history: str = ""  # injected by adapter from DB history
    is_followup: bool = False  # set by adapter for the second+ message in a chat

    # ── iteration control ───────────────────────────────────
    iteration: int = 0
    max_iterations: int = 50   # 对标 LangGraph recursion_limit，复杂多表查询需要更多轮次
    terminal_triggered: bool = False
    sql_retry_count: int = 0
    max_sql_retries: int = 3

    # ── datasource ──────────────────────────────────────────
    datasource_id: Optional[int] = None
    datasource_type: Optional[str] = None

    # ── runtime context (injected by executor, not persisted) ─
    session: object = None      # sqlmodel.Session
    current_user: object = None  # CurrentUser
    ds: object = None           # CoreDatasource | AssistantOutDsSchema
    out_ds_instance: object = None  # AssistantOutDs | None
    user_question: str = ""     # set by adapter before execution
    chat_id: int | None = None  # set by adapter, used to persist memory

    # ── helpers ─────────────────────────────────────────────

    def get_latest_query(self) -> Optional[QueryRecord]:
        if not self.queries:
            return None
        return list(self.queries.values())[-1]

    def get_latest_chart(self) -> Optional[ChartRecord]:
        if not self.charts:
            return None
        return list(self.charts.values())[-1]

    def get_context_for_llm(self) -> str:
        """
        Produce a compact summary for the LLM context.
        Keeps the context window small — typically <200 tokens.
        """
        parts = []

        if self.conversation_summary:
            parts.append(
                f"<conversation-history>{self.conversation_summary}</conversation-history>"
            )

        latest_q = self.get_latest_query()
        if latest_q:
            sql_preview = latest_q.sql[:300] + "..." if len(latest_q.sql) > 300 else latest_q.sql
            parts.append(
                f"<last-query record_id='{latest_q.record_id}' status='{latest_q.status}'>\n"
                f"{sql_preview}\n"
                f"</last-query>"
            )

        latest_c = self.get_latest_chart()
        if latest_c:
            parts.append(
                f"<last-chart chart_ref='{latest_c.chart_ref}' "
                f"type='{latest_c.chart_config.get('type', 'unknown')}' "
                f"record_id='{latest_c.record_id}'/>"
            )

        return "\n".join(parts) if parts else ""

    def available_query_ids(self) -> list[str]:
        """Return sorted list of query IDs (for error messages)."""
        return sorted(self.queries.keys())

    def available_chart_refs(self) -> list[str]:
        """Return sorted list of chart refs (for error messages)."""
        return sorted(self.charts.keys())

    # ── persistence ────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialize persistent fields to a JSON-safe dict.

        Only metadata is stored for queries (row_count, result_fields),
        NOT the full data rows.  The full data lives in ChatRecord.data
        for frontend display.  This keeps the JSON column small.
        """
        data: dict = {"version": 1}

        data["queries"] = {
            k: {
                "record_id": v.record_id,
                "sql": v.sql,
                "compiled_sql": v.compiled_sql,
                "tables_used": v.tables_used,
                "status": v.status,
                "sql_history": v.sql_history,
                "row_count": v.row_count,
                "result_fields": v.result_fields,
                "executed_at": v.executed_at.isoformat() if v.executed_at else None,
            }
            for k, v in self.queries.items()
        }

        data["charts"] = {
            k: {
                "chart_ref": v.chart_ref,
                "record_id": v.record_id,
                "chart_config": v.chart_config,
                "image_url": v.image_url,
            }
            for k, v in self.charts.items()
        }

        data["explored_tables"] = self.explored_tables
        data["_search_cache"] = self._search_cache
        data["conversation_summary"] = self.conversation_summary

        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "AgentMemory":
        """Restore persistent fields from a dict (reverse of to_dict).

        Returns a fresh AgentMemory if data is None or empty.
        Runtime fields must be re-injected by the caller.
        """
        memory = cls()
        if not data:
            return memory

        for k, v in data.get("queries", {}).items():
            executed_at = None
            if v.get("executed_at"):
                try:
                    executed_at = datetime.fromisoformat(v["executed_at"])
                except (ValueError, TypeError):
                    pass
            memory.queries[k] = QueryRecord(
                record_id=v.get("record_id", k),
                sql=v.get("sql", ""),
                compiled_sql=v.get("compiled_sql", ""),
                tables_used=v.get("tables_used", []),
                status=v.get("status", "created"),
                sql_history=v.get("sql_history", []),
                data=None,  # full data lives in ChatRecord, not memory_state
                row_count=v.get("row_count", 0),
                result_fields=v.get("result_fields", []),
                executed_at=executed_at,
            )

        for k, v in data.get("charts", {}).items():
            memory.charts[k] = ChartRecord(
                chart_ref=v.get("chart_ref", k),
                record_id=v.get("record_id", ""),
                chart_config=v.get("chart_config", {}),
                image_url=v.get("image_url"),
            )

        memory.explored_tables = data.get("explored_tables", {})
        memory._search_cache = data.get("_search_cache", {})
        memory.conversation_summary = data.get("conversation_summary", "")

        return memory
