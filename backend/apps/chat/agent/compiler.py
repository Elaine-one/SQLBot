"""
Dataset SQL Compiler — table-name → subquery replacement.

When the agent connects to a DataEase dataset, table names may be logical
views backed by pre-built SQL (``table.sql``).  This compiler replaces
dataset logical names with derived-table subqueries so the SQL can execute
against the physical database.

The compiler does ONE thing and does it with plain string replacement:

    FROM "数据集 2：商品销售明细"  →  FROM (backing_sql) AS ds_0

It does NOT do column-name mapping — ``get_table_metadata`` already
returns the correct SQL column names in ``fields[].name``, and the LLM is
instructed to use those names verbatim.

**Compatibility guarantee:**
When ``_is_assistant_mode(memory)`` is ``False`` (standalone SQLBot),
``self.mappings`` is empty and every public method is a no-op / passthrough.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apps.chat.agent.memory import AgentMemory


# ── mode detection ──────────────────────────────────────────

def _is_assistant_mode(memory: AgentMemory) -> bool:
    return memory.out_ds_instance is not None


# ── data class ──────────────────────────────────────────────

@dataclass
class DatasetMapping:
    """Resolved mapping for a single DataEase dataset."""

    sql: str          # backend SQL (from table.sql, semicolons already stripped)
    alias: str        # e.g. "ds_0"


# ── compiler ────────────────────────────────────────────────

class DatasetSQLCompiler:
    """Replace DataEase dataset logical names with derived-table subqueries.

    Pure string replacement — no sqlglot, no column mapping, no fallback chains.
    """

    def __init__(self, memory: AgentMemory):
        self._memory = memory
        self.mappings: dict[str, DatasetMapping] = {}
        if not _is_assistant_mode(memory):
            return  # standalone SQLBot — empty mappings, all methods no-op
        self._build_mappings(memory)

    # ── public API ──────────────────────────────────────────

    def compile(self, llm_sql: str) -> str:
        """Replace dataset names with subqueries.

        Returns the input unchanged when there are no dataset mappings.
        """
        if not self.mappings:
            return llm_sql
        return _replace_dataset_refs(llm_sql, self.mappings)

    def compile_sample_query(self, table_name: str) -> str | None:
        """Return ``SELECT * FROM (subquery) LIMIT 3``, or None."""
        m = self.mappings.get(table_name)
        if not m:
            return None
        return f"SELECT * FROM ({m.sql}) AS {m.alias} LIMIT 3"

    def is_dataset(self, table_name: str) -> bool:
        """Check whether *table_name* refers to a DataEase dataset."""
        return table_name in self.mappings

    # ── private ─────────────────────────────────────────────

    def _build_mappings(self, memory: AgentMemory) -> None:
        """Walk ``memory.ds.tables`` and build a mapping for every dataset."""
        tables = getattr(memory.ds, "tables", None)
        if not tables:
            return

        alias_idx = 0
        for table in tables:
            sql = getattr(table, "sql", None)
            if not sql or not isinstance(sql, str) or not sql.strip():
                continue  # physical table — nothing to map
            # Strip trailing semicolons — DataEase may include them.
            sql = sql.strip().rstrip(";").strip()

            name = (getattr(table, "name", None) or "").strip()
            if not name:
                continue

            self.mappings[name] = DatasetMapping(
                sql=sql,
                alias=f"ds_{alias_idx}",
            )
            alias_idx += 1


# ── replacement engine ──────────────────────────────────────

# Delimiters that mark the end of a table reference after FROM/JOIN.
_TABLE_END = r"(?=\s|$|,|;|\)|ON\b|WHERE\b|GROUP\b|ORDER\b|LIMIT\b|HAVING\b|UNION\b)"

# SQL keywords that must NOT be consumed as a table alias after the name.
_ALIAS_BLACKLIST = {
    "join", "inner", "outer", "left", "right", "full", "cross",
    "on", "where", "group", "order", "by", "having", "limit", "offset",
    "union", "select", "from", "as", "and", "or", "not", "in", "is", "null",
}


def _replace_dataset_refs(sql: str, mappings: dict[str, DatasetMapping]) -> str:
    """Replace dataset logical names with derived-table subqueries.

    Replacement only happens when the name appears after FROM / JOIN
    keywords — never in SELECT columns, WHERE values, or column aliases.

    Both quoted and unquoted forms are handled via the same context-aware
    regex to avoid false matches.
    """
    for table_name, mapping in mappings.items():
        subq = f"({mapping.sql}) AS {mapping.alias}"
        name_escaped = re.escape(table_name)

        replaced = False
        # Try quoted forms first (most common for Chinese names with spaces).
        # The optional alias group uses a negative lookahead to avoid eating
        # SQL keywords (JOIN, ON, WHERE, etc.) as if they were table aliases.
        for q in ('"', "'", "`"):
            q_name = re.escape(f"{q}{table_name}{q}")
            pattern = re.compile(
                rf"\b(FROM|JOIN)\s+{q_name}"
                rf"(?:\s+(?:AS\s+)?(?!{'|'.join(_ALIAS_BLACKLIST)}\b)\w+)?"  # alias, but not a keyword
                rf"{_TABLE_END}",
                re.IGNORECASE,
            )
            new_sql = pattern.sub(rf"\1 {subq}", sql)
            if new_sql != sql:
                sql = new_sql
                replaced = True
                break

        if not replaced:
            # Unquoted — same logic
            pattern = re.compile(
                rf"\b(FROM|JOIN)\s+{name_escaped}"
                rf"(?:\s+(?:AS\s+)?(?!{'|'.join(_ALIAS_BLACKLIST)}\b)\w+)?"
                rf"{_TABLE_END}",
                re.IGNORECASE,
            )
            sql = pattern.sub(rf"\1 {subq}", sql)

    return sql
