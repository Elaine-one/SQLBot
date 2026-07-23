"""
Query-execution tools for the SQLBot Agent.

get_field_values   — DISTINCT sample values for precise WHERE clauses.
execute_sql_query  — run a QueryRecord's SQL and cache the result.
"""

from __future__ import annotations

from apps.chat.agent.memory import AgentMemory
# External imports are lazy (inside function bodies) to avoid circular chains.


async def get_field_values(
    table_name: str,
    field_name: str,
    memory: AgentMemory,
    limit: int = 20,
) -> dict:
    """
    Get distinct sample values for a field.

    Helps the LLM write accurate WHERE clauses by revealing
    the actual value format (e.g., "US" vs "United States").

    DataEase datasets are transparently expanded via the compiler
    so that both table name and column name are resolved correctly.

    Inspired by Metabase's get_field_values tool.
    """
    from common.utils.utils import equals_ignore_case
    ds_type = (memory.datasource_type or "").lower()
    ds = memory.ds

    if equals_ignore_case(ds_type, "mysql", "doris", "starrocks"):
        quote = "`"
    elif equals_ignore_case(ds_type, "sqlserver"):
        quote = '"'
    else:
        quote = '"'

    # Resolve backing SQL (cold-cache safe) and build FROM source.
    # No column-name mapping needed — LLM uses fields[].name which is
    # already the correct SQL column name.
    from apps.chat.agent.tools.schema_tools import _resolve_backing_sql
    backing_sql = _resolve_backing_sql(table_name, memory)

    if backing_sql:
        source = f"({backing_sql}) AS {quote}{table_name}{quote}"
    else:
        source = f"{quote}{table_name}{quote}"

    if equals_ignore_case(ds_type, "sqlserver"):
        query = (
            f"SELECT DISTINCT TOP {limit} {quote}{field_name}{quote} "
            f"FROM {source} "
            f"WHERE {quote}{field_name}{quote} IS NOT NULL"
        )
    elif equals_ignore_case(ds_type, "oracle", "dm"):
        query = (
            f"SELECT DISTINCT {quote}{field_name}{quote} "
            f"FROM {source} "
            f"WHERE {quote}{field_name}{quote} IS NOT NULL AND ROWNUM <= {limit}"
        )
    else:
        query = (
            f"SELECT DISTINCT {quote}{field_name}{quote} "
            f"FROM {source} "
            f"WHERE {quote}{field_name}{quote} IS NOT NULL "
            f"LIMIT {limit}"
        )

    from apps.db.db import exec_sql
    try:
        result = exec_sql(ds=ds, sql=query, origin_column=True)
        rows = result.get("data", [])
        values = [row[field_name] for row in rows if row.get(field_name) is not None]
        return {
            "field": f"{table_name}.{field_name}",
            "distinct_count": len(values),
            "sample_values": values[:limit],
            "hint": "使用 sample_values 中的精确值构建 WHERE 条件",
        }
    except Exception as exc:
        return {"success": False, "error": f"获取字段值失败: {exc}"}


async def execute_sql_query(
    record_id: str,
    memory: AgentMemory,
) -> dict:
    """
    Execute a previously created SQL query and cache the result.

    After execution, results are stored in QueryRecord.data
    and accessible for chart generation.

    A safety row limit is applied if the SQL has no LIMIT clause,
    preventing accidental full-table scans from overwhelming memory.
    """
    import re

    query = memory.queries.get(record_id)
    if not query:
        return {
            "success": False,
            "error": f"查询 {record_id} 不存在",
            "available_queries": memory.available_query_ids(),
        }

    from apps.db.db import exec_sql
    try:
        # Use pre-compiled SQL if available (create_sql_query already compiled it).
        executable = query.compiled_sql or query.sql
        if not query.compiled_sql:
            from apps.chat.agent.compiler import DatasetSQLCompiler
            compiler = DatasetSQLCompiler(memory)
            executable = compiler.compile(query.sql)

        from common.utils.utils import SQLBotLogUtil

        # ── Safety row limit ──────────────────────────────────
        # If the SQL has no LIMIT clause, wrap it to prevent OOM.
        # The system prompt tells the LLM to use LIMIT 1000 for detail
        # queries, but this is a belt-and-suspenders guard for when
        # the LLM forgets.  Aggregation queries (GROUP BY without LIMIT)
        # also get the safety limit applied — GROUP BY on high-cardinality
        # columns can still produce millions of rows.
        _SAFETY_LIMIT = 500
        has_limit = re.search(
            r'\bLIMIT\s+\d+', executable, re.IGNORECASE
        ) is not None
        if not has_limit:
            executable = (
                f"SELECT * FROM ({executable}) AS _sqlbot_safety LIMIT {_SAFETY_LIMIT}"
            )
            SQLBotLogUtil.info(
                f"[Agent] safety limit {_SAFETY_LIMIT} applied to {record_id}"
            )

        SQLBotLogUtil.info(
            f"[Agent] compiled_sql={'Y' if query.compiled_sql else 'N'} "
            f"sql_preview={executable[:200]}..."
        )

        result = exec_sql(ds=memory.ds, sql=executable)
        query.data = result
        query.status = "executed"
        from datetime import datetime
        query.executed_at = datetime.now()
        # Store metadata for cross-turn persistence (data itself is not persisted)
        query.row_count = len(result.get("data", [])) if isinstance(result, dict) else 0
        # exec_sql returns fields as list[str], not dict — use directly
        query.result_fields = list(result["fields"]) if isinstance(result, dict) and "fields" in result else []
        return {
            "success": True,
            "record_id": record_id,
            "row_count": query.row_count,
            "columns": result.get("fields", []),
            "safety_limit_applied": not has_limit,
        }
    except Exception as exc:
        query.status = "failed"
        return {"success": False, "error": f"SQL 执行失败: {exc}"}
