"""
SQL-generation tools for the SQLBot Agent.

create_sql_query — terminal tool: validates SQL, creates a QueryRecord.
edit_sql_query   — terminal tool: string-replacement edits to an existing query.

Both tools are terminal (success → Agent stops iterating).
Failed calls do NOT terminate — the LLM can correct and retry.

Inspired by Metabase:
  - create.clj  → validate → produce query-id
  - edit.clj    → string-replacement edits with validation
"""

from __future__ import annotations

import re

from apps.chat.agent.memory import AgentMemory, QueryRecord
# External imports are lazy (inside function bodies) to avoid circular
# import chains with sqlbot_xpack.

# ── SQL validation helpers ─────────────────────────────────

# Keywords that are never allowed in Agent-generated SQL.
_DENIED_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
    "TRUNCATE", "MERGE", "REPLACE", "GRANT", "REVOKE",
}


def _first_keyword(sql: str) -> str:
    cleaned = sql.strip().lstrip("(").strip()
    return cleaned.split(None, 1)[0].upper() if cleaned else ""


def _resolve_dialect(ds_type: str) -> str | None:
    """Map datasource type to a sqlglot dialect name.

    Delegates to the authoritative ``DatabaseDialect`` registry so that
    dialect mappings are defined in exactly one place.
    """
    from apps.db.dialect import get_dialect
    return get_dialect(ds_type).sqlglot_dialect


def _validate_sql_syntax(sql: str, ds_type: str) -> tuple[bool, str]:
    """Basic syntax + safety validation. Returns (valid, error_message)."""
    keyword = _first_keyword(sql)
    if not keyword:
        return False, "无法解析 SQL 语句"
    if keyword in _DENIED_KEYWORDS:
        return False, (
            f"禁止的 SQL 操作「{keyword}」。SQLBot 是数据查询助手，仅支持 SELECT / WITH 只读查询，"
            f"不支持 INSERT / DELETE / DROP 等写操作。请重新输入查询需求。"
        )

    dialect = _resolve_dialect(ds_type)
    import sqlglot
    try:
        statements = sqlglot.parse(sql, dialect=dialect)
        if not statements:
            return False, "SQL 语法无法解析"
    except Exception as exc:
        return False, f"SQL 语法错误: {exc}"

    return True, ""


def _check_join_conditions(sql: str, dialect: str | None = None) -> list[str]:
    """检查 JOIN ON 条件是否存在常见问题。返回警告信息列表。

    不依赖业务知识，仅做通用结构检查:
      1. ON 条件两端字段名是否含 'id'/'key' 等唯一标识暗示
      2. ON 是否使用了 LIKE / ILIKE / 文本函数
      3. 同一个 JOIN 是否只有非唯一字段关联

    这是一个软检查: 不阻止SQL生成，只返回警告供 LLM 自我修正。

    *dialect* is the sqlglot dialect name (e.g. "tsql", "mysql") so that
    database-specific syntax (CROSS APPLY, (+) joins, etc.) is parsed
    correctly rather than silently dropped.
    """
    import sqlglot.expressions as exp

    warnings: list[str] = []
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
        if not tree:
            return warnings

        for join in tree.find_all(exp.Join):
            on_clause = join.args.get("on")
            if not on_clause:
                continue
            on_sql = on_clause.sql().upper()

            # 表别名/名称
            table_node = join.args.get("this")
            table_label = table_node.alias_or_name if table_node else "?"

            # 检查: LIKE/ILIKE 作 JOIN 条件
            if "LIKE" in on_sql:
                warnings.append(
                    f"JOIN {table_label} 使用了模糊匹配({on_clause.sql()[:60]})，"
                    f"可能导致行数爆炸或性能问题"
                )
                continue

            # 提取 ON 中的等值比较字段（A.col = B.col）
            eq_pairs = _extract_eq_columns(on_clause)

            if not eq_pairs:
                continue

            # 检查: 所有等值对是否都没有 ID 类字段
            has_id_field = any(
                _looks_like_unique_key(left) or _looks_like_unique_key(right)
                for left, right in eq_pairs
            )
            if not has_id_field:
                pairs_str = ", ".join(f"{l}={r}" for l, r in eq_pairs[:2])
                warnings.append(
                    f"JOIN {table_label} ON {pairs_str}: "
                    f"关联字段疑似非唯一标识（缺少 id/key/code 等），可能产生多对多行数爆炸"
                )

    except Exception:
        pass

    return warnings


def _extract_eq_columns(on_clause) -> list[tuple[str, str]]:
    """从 ON 子句中提取等值比较的字段对。"""
    import sqlglot.expressions as exp

    pairs: list[tuple[str, str]] = []

    def _collect(node):
        if isinstance(node, exp.EQ):
            left = node.left.sql().strip('"').strip('`').strip("'").lower()
            right = node.right.sql().strip('"').strip('`').strip("'").lower()
            pairs.append((left, right))
        elif isinstance(node, (exp.And, exp.Or)):
            _collect(node.left)
            _collect(node.right)

    _collect(on_clause)
    return pairs


def _looks_like_unique_key(col: str) -> bool:
    """判断字段名是否像唯一标识（含 id/key/code/no/sn/uuid/pk）。"""
    col_lower = col.lower().split(".")[-1]  # 去表前缀
    key_patterns = ("id", "_id", "key", "_key", "code", "_code", "no", "_no",
                    "sn", "_sn", "uuid", "pk_", "_pk")
    return col_lower.endswith(key_patterns) or "id_" in col_lower


def _extract_table_names(sql: str) -> list[str]:
    """Extract table names from FROM / JOIN clauses (Unicode-aware).

    Handles quoted identifiers with spaces (e.g. ``"数据集 2：商品"``)
    as well as MySQL backticks and SQL Server brackets.
    """
    tables: list[str] = []
    # Match each quote style separately so spaces inside quotes are preserved.
    # Order: double-quoted | backtick-quoted | bracket-quoted | unquoted-word
    pattern = re.compile(
        r'\b(?:FROM|JOIN)\s+'
        r'(?:'
        r'"([^"]+)"'
        r'|`([^`]+)`'
        r'|\[([^\]]+)\]'
        r'|(\w[\w.]*)'     # unquoted: word, may contain dots (schema.table)
        r')'
        r'(?:\s+(?:AS\s+)?(?:\w+))?'   # consume optional alias
        r'(?=\s|$|,|;|\)|ON\b|WHERE\b|GROUP\b|ORDER\b|LIMIT\b|HAVING\b)',
        re.IGNORECASE | re.UNICODE,
    )
    _KW = frozenset({
        "select", "where", "on", "and", "or", "as",
        "join", "left", "right", "inner", "outer", "full", "cross",
        "group", "order", "by", "having", "limit", "offset", "union",
    })
    for m in pattern.finditer(sql):
        # Exactly one of the four groups is non-None
        name = m.group(1) or m.group(2) or m.group(3) or m.group(4)
        if name and name.lower() not in _KW:
            tables.append(name)
    return list(dict.fromkeys(tables))


# ── global counter for record/chart IDs ────────────────────

_counter: int = 0


def _next_seq(memory: AgentMemory | None = None) -> int:
    """Generate the next record_id sequence number.

    Uses the existing query count in memory if available (handles
    DB-restored memory), otherwise falls back to the global counter.
    """
    if memory and memory.queries:
        # Extract numeric suffixes from existing record_ids (e.g. "r_1", "r_5")
        existing = []
        for rid in memory.queries.keys():
            try:
                existing.append(int(rid.split("_")[-1]))
            except (ValueError, IndexError):
                pass
        if existing:
            return max(existing) + 1

    global _counter
    _counter += 1
    return _counter


# ── tools ──────────────────────────────────────────────────

async def create_sql_query(
    sql: str,
    memory: AgentMemory,
) -> dict:
    """
    Create a new SQL query (TERMINAL tool).

    Validates syntax, safety, and that referenced tables have been explored.
    On success → creates a QueryRecord → memory.terminal_triggered = True.
    On failure → returns error details → Agent continues with corrections.

    DataEase datasets (logical views) are transparently expanded to
    derived-table subqueries for validation / execution.  The original
    LLM-authored SQL is preserved in the QueryRecord so that
    edit_sql_query works with the LLM's own identifiers.
    """
    original_sql = sql
    ds_type = memory.datasource_type or ""

    # 1. Compile DataEase dataset references (table names + column names)
    #    into executable SQL.  In standalone SQLBot this is a no-op.
    from apps.chat.agent.compiler import DatasetSQLCompiler
    compiler = DatasetSQLCompiler(memory)
    compiled = compiler.compile(sql)

    # 2. Syntax check
    valid, error = _validate_sql_syntax(compiled, ds_type)
    if not valid:
        memory.sql_retry_count += 1
        return {"success": False, "error": error}

    # 3. Read-only check
    from apps.db.db import check_sql_read
    try:
        is_safe, reason = check_sql_read(compiled, memory.ds)
        if not is_safe:
            return {"success": False, "error": reason}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # 4. JOIN condition review — 通用检查：ON 条件两端是否都没有 ID 类字段
    join_warnings = _check_join_conditions(compiled, dialect=_resolve_dialect(ds_type))
    if join_warnings:
        memory.sql_retry_count += 1
        return {
            "success": False,
            "error": "JOIN 条件可能存在问题: " + "；".join(join_warnings),
            "hint": "请检查关联字段是否为唯一标识字段。如果是汇总表，需同时匹配ID+日期。",
        }

    # 5. Table-existence check (against original SQL — dataset names are
    #    expected; physical table names must be in explored_tables)
    used_original = _extract_table_names(original_sql)
    unknown = [
        t for t in used_original
        if t not in memory.explored_tables and not compiler.is_dataset(t)
    ]
    if unknown:
        return {
            "success": False,
            "error": f"表 {unknown} 的字段结构尚未获取",
            "action_required": "请对每张表调用 get_table_metadata(table_name=...) 获取字段结构",
        }

    # 6. DB-level dry-run validation against compiled SQL.
    #    Uses each database's native mechanism:
    #      MySQL/PG/CK  → EXPLAIN
    #      SQL Server   → SET NOEXEC ON  (compile without executing)
    #      Oracle       → skipped (EXPLAIN PLAN FOR has different syntax)
    #      DM/es/hive   → skipped (no lightweight equivalent)
    from apps.db.dialect import get_dialect
    db_dialect = get_dialect(ds_type)
    if db_dialect.supports_explain and db_dialect.explain_prefix:
        try:
            from apps.db.db import exec_sql as _exec_raw
            explain_sql = f"{db_dialect.explain_prefix}{compiled}"
            _exec_raw(ds=memory.ds, sql=explain_sql)
        except Exception as exc:
            err_msg = str(exc).split("\\n")[0][:300] if "\\n" in str(exc) else str(exc)[:300]
            return {
                "success": False,
                "error": f"SQL 校验失败: {err_msg}",
                "hint": "请根据错误信息修正 SQL 中的列名或表名，然后重新调用 create_sql_query",
            }
    elif db_dialect.db_type == "sqlServer":
        # SQL Server: SET NOEXEC ON compiles the query (full name resolution,
        # type checking) without actually executing it — equivalent to EXPLAIN.
        try:
            from apps.db.db import get_engine
            from sqlalchemy import text
            engine = get_engine(memory.ds, timeout=10)
            with engine.connect() as conn:
                conn.execute(text("SET NOEXEC ON"))
                try:
                    conn.execute(text(compiled))
                finally:
                    conn.execute(text("SET NOEXEC OFF"))  # always reset
        except Exception as exc:
            err_msg = str(exc).split("\\n")[0][:300] if "\\n" in str(exc) else str(exc)[:300]
            return {
                "success": False,
                "error": f"SQL 校验失败: {err_msg}",
                "hint": "请根据错误信息修正 SQL 中的列名或表名，然后重新调用 create_sql_query",
            }

    # 6. Extract table names from ORIGINAL SQL for tables_used.
    #    Dataset names are valid here — they represent DataEase views whose
    #    backing physical tables are resolved at execution time.
    used = _extract_table_names(original_sql)

    # 7. Success → store original + compiled SQL
    record_id = f"r_{_next_seq(memory)}"
    memory.queries[record_id] = QueryRecord(
        record_id=record_id,
        sql=original_sql,
        compiled_sql=compiled,
        tables_used=used,
        status="created",
    )
    memory.terminal_triggered = True
    return {
        "success": True,
        "record_id": record_id,
        "sql": original_sql,
        "tables_used": used,
    }


async def edit_sql_query(
    record_id: str,
    edits: list[dict],
    memory: AgentMemory,
) -> dict:
    """
    Edit an existing SQL query via string replacements (TERMINAL tool).

    edits: [{"old_string": "...", "new_string": "...", "replace_all": false}, ...]

    Each edit is applied sequentially.  After all edits, the resulting SQL
    is validated.  Inspired by Metabase's edit.clj.

    Edits operate on the *original* LLM-authored SQL (stored with logical
    table/column names).  Dataset expansion happens transparently during
    validation and execution.

    On success → memory.terminal_triggered = True.
    """
    # 1. Resolve query
    query = memory.queries.get(record_id)
    if not query:
        return {
            "success": False,
            "error": f"查询 {record_id} 不存在",
            "available_queries": memory.available_query_ids(),
        }

    sql = query.sql

    # 2. Apply edits to original SQL
    for i, edit in enumerate(edits):
        old = edit.get("old_string", "")
        new = edit.get("new_string", "")
        replace_all = edit.get("replace_all", False)

        if not old:
            return {"success": False, "error": f"第 {i+1} 步: old_string 不能为空"}
        if old not in sql:
            return {
                "success": False,
                "error": f"第 {i+1} 步: '{old[:100]}' 在 SQL 中未找到",
            }
        if not replace_all and sql.count(old) > 1:
            return {
                "success": False,
                "error": f"'{old[:100]}' 出现 {sql.count(old)} 次。设置 replace_all=true 或给出更精确的 old_string",
            }

        sql = sql.replace(old, new) if replace_all else sql.replace(old, new, 1)

    # 3. Compile dataset references → validate compiled SQL
    from apps.chat.agent.compiler import DatasetSQLCompiler
    compiler = DatasetSQLCompiler(memory)
    compiled = compiler.compile(sql)

    ds_type = memory.datasource_type or ""
    valid, error = _validate_sql_syntax(compiled, ds_type)
    if not valid:
        return {"success": False, "error": f"编辑后的 SQL 语法有误: {error}"}

    from apps.db.db import check_sql_read
    try:
        is_safe, reason = check_sql_read(compiled, memory.ds)
        if not is_safe:
            return {"success": False, "error": reason}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # 4. Success — store edited original + compiled SQL
    query.add_version(query.sql)
    query.sql = sql
    query.compiled_sql = compiled
    query.status = "edited"
    memory.terminal_triggered = True
    return {"success": True, "record_id": record_id}
