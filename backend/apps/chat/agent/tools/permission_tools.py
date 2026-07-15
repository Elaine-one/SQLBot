"""
Post-processing for row-level permissions and dynamic subqueries.

These are NOT Agent tools — they run transparently after SQL generation,
before execution. The LLM never sees the permission logic.

type=0 (CoreDatasource): apply row-permission WHERE clauses.
type=1 (AssistantOutDsSchema): apply DataEase row rules + dynamic subqueries
                                for Union/Join datasets.
"""

from __future__ import annotations

import json
from typing import Optional

from apps.chat.agent.memory import AgentMemory


async def apply_permissions(
    sql: str,
    tables_used: list[str],
    memory: AgentMemory,
    llm=None,
) -> str:
    """
    Apply row-level permissions to SQL. Returns the modified SQL.

    type=0: injects row-permission WHERE clauses from SQLBot's permission system.
    type=1: injects DataEase row rules + replaces table refs with subqueries
            for Union/Join datasets.
    """
    if _is_assistant_mode(memory):
        return await _apply_assistant_permissions(sql, tables_used, memory, llm)
    else:
        return await _apply_core_permissions(sql, tables_used, memory, llm)


async def _apply_core_permissions(
    sql: str,
    tables_used: list[str],
    memory: AgentMemory,
    llm=None,
) -> str:
    """type=0: inject row-permission filters from SQLBot."""
    from apps.datasource.crud.permission import is_normal_user, get_row_permission_filters

    if not is_normal_user(memory.current_user):
        return sql  # admin — no filtering

    filters = get_row_permission_filters(
        session=memory.session,
        current_user=memory.current_user,
        ds=memory.ds,
        tables=tables_used,
    )
    if not filters:
        return sql

    if llm is None:
        return sql  # can't apply without LLM

    return await _llm_apply_filters(sql, filters, memory, llm)


async def _apply_assistant_permissions(
    sql: str,
    tables_used: list[str],
    memory: AgentMemory,
    llm=None,
) -> str:
    """type=1: apply DataEase row rules + dynamic subqueries."""
    ds = memory.ds  # AssistantOutDsSchema

    # 1. Row-level rule filters from DataEase table metadata
    filters = []
    sub_query_map = {}
    for table in (ds.tables or []):
        if table.name not in tables_used:
            continue
        if getattr(table, "rule", None):
            filters.append({"table": table.name, "filter": table.rule})
        if getattr(table, "sql", None):
            sub_query_map[table.name] = table.sql

    # 2. Apply row filters via LLM
    if filters and llm is not None:
        sql = await _llm_apply_filters(sql, filters, memory, llm)

    # 3. Apply dynamic subqueries (Union/Join datasets)
    if sub_query_map and llm is not None:
        sql = await _llm_apply_subqueries(sql, sub_query_map, memory, llm)

    return sql


async def _llm_apply_filters(
    sql: str,
    filters: list[dict],
    memory: AgentMemory,
    llm,
) -> str:
    """Use LLM to inject WHERE conditions from row-permission filters."""
    engine = _get_engine(memory)
    filter_json = json.dumps(filters, ensure_ascii=False)

    prompt = f"""## 请使用中文回答

在给定的 SQL 基础上，将过滤条件添加到该 SQL 内并生成一句新 SQL。

规则：
- 以原 SQL 为基础，将新过滤条件添加到 SQL 中
- 过滤条件中的字段前加上表别名（如无别名则加表名）
- 生成 SQL 符合 {engine} 语法规范
- 只返回 JSON: {{"success":true,"sql":"生成的SQL"}}
- 如果过滤条件为空或无效 → {{"success":true,"sql":"原SQL"}}

原 SQL:
{sql}

过滤条件:
{filter_json}"""

    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    try:
        result = json.loads(_extract_json(content))
        return result.get("sql", sql)
    except Exception:
        return sql


async def _llm_apply_subqueries(
    sql: str,
    sub_query_map: dict[str, str],
    memory: AgentMemory,
    llm,
) -> str:
    """Use LLM to replace table references with subqueries."""
    engine = _get_engine(memory)
    sub_list = [{"table": t, "query": q} for t, q in sub_query_map.items()]
    sub_json = json.dumps(sub_list, ensure_ascii=False)

    prompt = f"""## 请使用中文回答

将给定 SQL 中的表名替换为对应的子查询。保持原始 SQL 结构不变，只替换表引用。

规则：
- 完全匹配表名（注意大小写）
- 子查询需要括号包围
- 保留原有别名
- 生成 SQL 符合 {engine} 规范
- 只返回 JSON: {{"success":true,"sql":"生成的SQL"}}

原 SQL:
{sql}

子查询映射:
{sub_json}"""

    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    try:
        result = json.loads(_extract_json(content))
        return result.get("sql", sql)
    except Exception:
        return sql


# ── helpers ────────────────────────────────────────────────

def _is_assistant_mode(memory: AgentMemory) -> bool:
    return memory.out_ds_instance is not None


def _get_engine(memory: AgentMemory) -> str:
    ds = memory.ds
    if hasattr(ds, "type_name"):
        return ds.type_name or ds.type or "MySQL"
    return getattr(ds, "type", "MySQL") or "MySQL"


def _extract_json(text: str) -> str:
    """Extract JSON from LLM response (may be wrapped in markdown)."""
    text = text.strip()
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.rindex("```")
        return text[start:end].strip()
    if "```" in text:
        start = text.index("```") + 3
        end = text.rindex("```")
        return text[start:end].strip()
    return text
