"""
Schema-discovery tools for the SQLBot Agent.

Handles BOTH datasource types transparently:
  - type=0 (基础应用 / CoreDatasource)     → get_table_obj_by_ds() + embedding
  - type=1 (高级应用 / AssistantOutDsSchema) → ds.tables from DataEase API

The discriminator is memory.out_ds_instance — set by adapter.py when
the assistant type is in dynamic_ds_types [1, 3].
"""

from __future__ import annotations

from apps.chat.agent.memory import AgentMemory
# External imports are lazy (inside function bodies) to avoid circular
# import chains with sqlbot_xpack → permission → datasource.


# ── helpers: CoreDatasource (type=0) ────────────────────────

def _build_field_dict_core(field) -> dict:
    """Build field info from a CoreField model object."""
    info: dict = {
        "name": field.field_name,
        "type": field.field_type or "unknown",
    }
    if field.custom_comment:
        info["comment"] = field.custom_comment.strip()
    elif field.field_comment:
        info["comment"] = field.field_comment.strip()
    return info


def _build_table_schema_core(table_obj) -> dict:
    """Build table schema from a TableObj."""
    fields = [_build_field_dict_core(f) for f in (table_obj.fields or [])]
    result: dict = {"table_name": table_obj.table.table_name, "fields": fields}
    if table_obj.table.custom_comment:
        result["comment"] = table_obj.table.custom_comment.strip()
    return result


# ── helpers: AssistantOutDsSchema (type=1) ──────────────────

def _build_field_dict_assistant(field) -> dict:
    """Build field info from an AssistantFieldSchema (DataEase API)."""
    info: dict = {
        "name": field.name,
        "type": field.type or "unknown",
    }
    if field.comment and field.comment != field.name:
        info["comment"] = field.comment
        info["business_name"] = field.comment  # business name vs SQL column name
    elif field.comment:
        info["comment"] = field.comment
    return info


def _build_table_schema_assistant(table) -> dict:
    """Build table schema from an AssistantTableSchema.

    Includes the backing SQL when this is a DataEase dataset (logical view)
    rather than a direct physical table reference.  The SQL is used by
    create_sql_query to auto-expand dataset references into subqueries.
    """
    fields = [_build_field_dict_assistant(f) for f in (table.fields or [])]
    result: dict = {"table_name": table.name, "fields": fields}
    if table.comment:
        result["comment"] = table.comment.strip()

    # DataEase datasets carry a backing SQL that must be used as a derived
    # table subquery — the logical name alone is NOT a physical table.
    sql = getattr(table, "sql", None)
    if sql and isinstance(sql, str) and sql.strip():
        result["sql"] = sql.strip()
        result["is_dataset"] = True
    else:
        result["is_dataset"] = False

    return result


# ── helpers: backing SQL resolution ─────────────────────────

def _resolve_backing_sql(table_name: str, memory: AgentMemory) -> str | None:
    """Return the backing SQL for a DataEase dataset, or None.

    Tries ``memory.explored_tables`` cache first, then falls back to
    scanning ``memory.ds.tables`` directly (cold-cache case).
    """
    # Hot path: cached via get_table_metadata
    cached = memory.explored_tables.get(table_name)
    if cached and cached.get("sql"):
        return cached["sql"]

    # Cold path: lookup directly from ds.tables
    ds = memory.ds
    for t in (getattr(ds, "tables", None) or []):
        if getattr(t, "name", None) == table_name:
            sql = getattr(t, "sql", None)
            if sql and isinstance(sql, str) and sql.strip():
                return sql.strip()
            return None
    return None


# ── mode detection ──────────────────────────────────────────

def _is_assistant_mode(memory: AgentMemory) -> bool:
    return memory.out_ds_instance is not None


# ── tools ──────────────────────────────────────────────────

async def search_relevant_tables(
    query: str,
    top_n: int,
    memory: AgentMemory,
) -> dict:
    """
    Search for tables relevant to the user's question.

    type=0: embedding-based semantic search over CoreDatasource tables.
    type=1: returns all pre-selected tables from the DataEase dataset
            (no embedding needed — the dataset already scopes the tables).

    Results are cached per (query, top_n) to avoid redundant embedding calls
    when the LLM re-searches with similar wording ("Don't go in circles").
    """
    cache_key = f"{query}:{top_n}"
    if cache_key in memory._search_cache:
        return memory._search_cache[cache_key]

    if _is_assistant_mode(memory):
        ds = memory.ds
        tables = ds.tables or []

        # The DataEase dataset is already a curated set (typically 1-10 tables).
        # Unlike type=0 where we need embedding to find relevant tables among hundreds,
        # here the LLM itself is the best judge of relevance — it can do cross-language
        # concept matching that no keyword or embedding ranker can replicate.
        #
        # This mirrors Metabase's concept-driven discovery:
        #   1. LLM reads the table list (this call)
        #   2. LLM uses its own cross-language knowledge to pick candidates
        #   3. LLM calls get_table_metadata to verify column-level matches
        #
        # We return ALL tables (unsorted, untruncated) because the set is small.
        # The LLM decides which ones to drill into.

        result = {
            "tables": [
                {
                    "name": t.name,
                    "comment": t.comment or "",
                    "is_dataset": bool(getattr(t, "sql", None)),
                }
                for t in tables
            ],
            "total_tables": len(tables),
            "hint": (
                f"数据集共 {len(tables)} 张表。"
                "使用你的跨语言知识判断哪些表与用户问题相关，"
                "然后对候选表调用 get_table_metadata 验证列名是否匹配。"
                "不要凭表名就构建查询——读到实际的列名后再确认。"
                "标记为 is_dataset=true 的表是 DataEase 逻辑视图，"
                "get_table_metadata 会返回底层 SQL。"
            ),
        }
        memory._search_cache[cache_key] = result
        return result

    # type=0: CoreDatasource path
    from apps.datasource.crud.datasource import get_table_obj_by_ds
    from apps.datasource.embedding.table_embedding import calc_table_embedding
    from common.core.config import settings

    table_objs = get_table_obj_by_ds(
        session=memory.session,
        current_user=memory.current_user,
        ds=memory.ds,
    )

    candidates = []
    for obj in table_objs:
        schema_table = f"# Table: {obj.table.table_name}"
        if obj.table.custom_comment:
            schema_table += f", {obj.table.custom_comment.strip()}"
        candidates.append({
            "id": obj.table.id,
            "table_name": obj.table.table_name,
            "schema_table": schema_table,
            "embedding": obj.table.embedding,
        })

    if not candidates:
        return {"tables": [], "hint": "没有可用的表"}

    if settings.TABLE_EMBEDDING_ENABLED:
        ranked = calc_table_embedding(candidates, query)[:top_n]
    else:
        ranked = candidates[:top_n]

    result = {
        "tables": [
            {
                "name": t["table_name"],
                "score": round(t.get("cosine_similarity", 0.0), 4),
                "comment": t.get("schema_table", "").split(", ", 1)[-1]
                if ", " in t.get("schema_table", "") else "",
            }
            for t in ranked
        ],
        "hint": "使用 get_table_metadata 获取感兴趣表的完整字段信息",
    }
    memory._search_cache[cache_key] = result
    return result


async def get_table_metadata(
    table_name: str,
    memory: AgentMemory,
) -> dict:
    """
    Get the complete field structure for a single table.

    Results are cached in memory.explored_tables for cross-turn reuse.

    type=0: queries CoreDatasource via get_table_obj_by_ds().
    type=1: queries AssistantOutDsSchema.tables from DataEase API.
    """
    # Check cache first (shared across modes)
    if table_name in memory.explored_tables:
        cached = memory.explored_tables[table_name]
        result: dict = {
            "table_name": table_name,
            "cached": True,
            "fields": cached["fields"],
            "is_dataset": cached.get("is_dataset", False),
        }
        if cached.get("sql"):
            result["sql"] = cached["sql"]
            result["hint"] = (
                "此表是 DataEase 数据集逻辑视图。字段的 name 是 SQL 实际列名，"
                "直接用 name 写 SELECT/WHERE/GROUP BY。comment 是业务说明。"
                "系统会自动展开数据集，不需手动处理底层 SQL。"
            )
        return result

    if _is_assistant_mode(memory):
        # type=1: DataEase API mode — ds is AssistantOutDsSchema
        ds = memory.ds
        for table in (ds.tables or []):
            if table.name == table_name:
                schema = _build_table_schema_assistant(table)
                memory.explored_tables[table_name] = schema
                result = {
                    "table_name": table_name,
                    "cached": False,
                    "fields": schema["fields"],
                    "is_dataset": schema.get("is_dataset", False),
                }
                if schema.get("sql"):
                    result["sql"] = schema["sql"]
                    result["hint"] = (
                        "此表是 DataEase 数据集逻辑视图。字段的 name 是 SQL 实际列名，"
                        "直接用 name 写 SELECT/WHERE/GROUP BY。comment 是业务说明。"
                        "系统会自动展开数据集，不需手动处理底层 SQL。"
                    )
                return result
        return {"success": False, "error": f"表 '{table_name}' 未在数据集中找到"}

    # type=0: CoreDatasource mode
    from apps.datasource.crud.datasource import get_table_obj_by_ds
    table_objs = get_table_obj_by_ds(
        session=memory.session,
        current_user=memory.current_user,
        ds=memory.ds,
    )
    for obj in table_objs:
        if obj.table.table_name == table_name:
            schema = _build_table_schema_core(obj)
            memory.explored_tables[table_name] = schema
            return {"table_name": table_name, "cached": False, "fields": schema["fields"]}

    return {"success": False, "error": f"表 '{table_name}' 未找到"}


async def get_table_sample_data(
    table_name: str,
    memory: AgentMemory,
) -> dict:
    """
    Get 3 sample rows from a single table.

    For DataEase datasets (logical views with backing SQL), the query is
    automatically wrapped as ``SELECT * FROM (backing_sql) LIMIT 3``.
    For physical tables, TABLESAMPLE is used where available.
    """
    if _is_assistant_mode(memory):
        from apps.db.db import exec_sql
        ds = memory.ds
        ds_type = ds.type or ""
        if ds_type.lower() in ("mysql", "doris", "starrocks"):
            q = "`"
        elif ds_type.lower() == "sqlserver":
            q = '"'
        else:
            q = '"'

        # Check for backing SQL (dataset) — cold-cache safe
        backing_sql = _resolve_backing_sql(table_name, memory)

        if backing_sql:
            sql = f"SELECT * FROM ({backing_sql}) AS _sqlbot_sample LIMIT 3"
        else:
            sql = f"SELECT * FROM {q}{table_name}{q} TABLESAMPLE SYSTEM(0.1) LIMIT 3"

        try:
            try:
                result = exec_sql(ds=ds, sql=sql, origin_column=True)
            except Exception:
                if backing_sql:
                    raise  # subquery failure — invalid backing SQL
                sql = f"SELECT * FROM {q}{table_name}{q} LIMIT 3"
                result = exec_sql(ds=ds, sql=sql, origin_column=True)
            rows = result.get("data", [])[:3]
            if rows:
                import json
                return {
                    "table_name": table_name,
                    "sample_data": json.dumps(rows, ensure_ascii=False, indent=2),
                }
            return {"table_name": table_name, "sample_data": None,
                    "hint": "表为空"}
        except Exception as exc:
            return {"table_name": table_name, "sample_data": None,
                    "hint": f"无法获取样本: {exc}"}

    # type=0: CoreDatasource path
    from apps.datasource.crud.datasource import get_tables_sample_data
    sample = get_tables_sample_data(
        session=memory.session,
        current_user=memory.current_user,
        ds=memory.ds,
        table_list=[table_name],
    )
    if not sample:
        return {"table_name": table_name, "sample_data": None,
                "hint": "表为空或无法获取样本数据"}
    return {"table_name": table_name, "sample_data": sample}
