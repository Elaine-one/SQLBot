"""
Database Dialect Registry — single source of truth (SSOT).

Every datasource type's SQL-generation / validation / encoding settings
are defined once here.  All other modules (sql_tools, db, query_tools,
schema_tools) de-duplicate by calling ``get_dialect(type)`` instead of
maintaining their own inline mappings.

Adding a new database type only requires one new entry in
``DIALECT_REGISTRY`` — everything else picks it up automatically.

Author: Claude Code
Date: 2026/07/24
"""

from __future__ import annotations

from dataclasses import dataclass

from apps.db.constant import ConnectType


# ── data class ────────────────────────────────────────────────

@dataclass(frozen=True)
class DatabaseDialect:
    """Authoritative per-database dialect configuration.

    Frozen + hashable — safe to use as dict keys or cache entries.
    """

    # ── identity ──────────────────────────────────────────
    db_type: str                      # internal type: "sqlServer", "mysql", …
    display_name: str                 # human-readable: "Microsoft SQL Server"

    # ── sqlglot ───────────────────────────────────────────
    sqlglot_dialect: str | None = None  # "tsql", "mysql", "postgres", …

    # ── SQL validation ────────────────────────────────────
    supports_explain: bool = True       # does the DB support EXPLAIN?
    explain_prefix: str = "EXPLAIN "    # prefix to prepend (MySQL/PG style)

    # ── identifier quoting ────────────────────────────────
    quote_char: str = '"'              # '"', '`', or '['

    # ── connection charset ────────────────────────────────
    # Applied to pymssql / pymysql etc.  When None the driver default is used.
    charset: str | None = None

    # ── connection mode ───────────────────────────────────
    connect_type: ConnectType = ConnectType.sqlalchemy

    # ── extra decoder charsets (for convert_value) ────────
    # Ordered fallback list after UTF-8 fails, before latin-1.
    fallback_charsets: tuple[str, ...] = ()


# ── registry ─────────────────────────────────────────────────

DIALECT_REGISTRY: dict[str, DatabaseDialect] = {
    # ── Microsoft SQL Server ─────────────────────────────
    "sqlServer": DatabaseDialect(
        db_type="sqlServer",
        display_name="Microsoft SQL Server",
        sqlglot_dialect="tsql",
        supports_explain=False,          # SQL Server uses SET SHOWPLAN_XML ON
        explain_prefix=None,
        quote_char='[',                  # TSQL uses [brackets]
        charset="UTF-8",                 # request UTF-8 from pymssql
        fallback_charsets=("gbk", "gb2312", "cp936", "gb18030"),
    ),

    # ── MySQL ────────────────────────────────────────────
    "mysql": DatabaseDialect(
        db_type="mysql",
        display_name="MySQL",
        sqlglot_dialect="mysql",
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char="`",
        charset="utf8mb4",
    ),

    # ── PostgreSQL ───────────────────────────────────────
    "pg": DatabaseDialect(
        db_type="pg",
        display_name="PostgreSQL",
        sqlglot_dialect="postgres",
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
    ),

    # ── Oracle ───────────────────────────────────────────
    "oracle": DatabaseDialect(
        db_type="oracle",
        display_name="Oracle",
        sqlglot_dialect="oracle",
        supports_explain=False,          # Oracle uses EXPLAIN PLAN FOR … syntax
        explain_prefix=None,
        quote_char='"',
    ),

    # ── ClickHouse ───────────────────────────────────────
    "ck": DatabaseDialect(
        db_type="ck",
        display_name="ClickHouse",
        sqlglot_dialect="clickhouse",
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
    ),

    # ── Apache Doris ─────────────────────────────────────
    "doris": DatabaseDialect(
        db_type="doris",
        display_name="Apache Doris",
        sqlglot_dialect="mysql",          # MySQL-compatible
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char="`",
        connect_type=ConnectType.py_driver,
    ),

    # ── StarRocks ────────────────────────────────────────
    "starrocks": DatabaseDialect(
        db_type="starrocks",
        display_name="StarRocks",
        sqlglot_dialect="mysql",          # MySQL-compatible
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char="`",
        connect_type=ConnectType.py_driver,
    ),

    # ── Apache Hive ──────────────────────────────────────
    "hive": DatabaseDialect(
        db_type="hive",
        display_name="Apache Hive",
        sqlglot_dialect="hive",
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char="`",
        connect_type=ConnectType.py_driver,
    ),

    # ── 达梦 DM ──────────────────────────────────────────
    "dm": DatabaseDialect(
        db_type="dm",
        display_name="达梦",
        sqlglot_dialect=None,             # sqlglot has no DM dialect
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
        connect_type=ConnectType.py_driver,
    ),

    # ── Kingbase ─────────────────────────────────────────
    "kingbase": DatabaseDialect(
        db_type="kingbase",
        display_name="Kingbase",
        sqlglot_dialect="postgres",       # PostgreSQL-compatible
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
        connect_type=ConnectType.py_driver,
    ),

    # ── AWS Redshift ─────────────────────────────────────
    "redshift": DatabaseDialect(
        db_type="redshift",
        display_name="AWS Redshift",
        sqlglot_dialect="postgres",       # PostgreSQL-compatible
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
        connect_type=ConnectType.py_driver,
    ),

    # ── Elasticsearch ────────────────────────────────────
    "es": DatabaseDialect(
        db_type="es",
        display_name="Elasticsearch",
        sqlglot_dialect=None,             # Not SQL-based
        supports_explain=False,
        explain_prefix=None,
        quote_char='"',
        connect_type=ConnectType.py_driver,
    ),

    # ── Excel (internal PG engine) ───────────────────────
    "excel": DatabaseDialect(
        db_type="excel",
        display_name="Excel/CSV",
        sqlglot_dialect="postgres",       # Backed by PostgreSQL engine
        supports_explain=True,
        explain_prefix="EXPLAIN ",
        quote_char='"',
    ),
}


# ── default / fallback ───────────────────────────────────────

_DEFAULT_DIALECT = DatabaseDialect(
    db_type="__default__",
    display_name="Unknown",
    sqlglot_dialect=None,                # sqlglot auto-detect
    supports_explain=True,
    explain_prefix="EXPLAIN ",
    quote_char='"',
)


# ── public API ───────────────────────────────────────────────

def get_dialect(db_type: str) -> DatabaseDialect:
    """Return the dialect config for *db_type*, or the default fallback.

    Matching is case-insensitive so callers can pass raw datasource
    type strings without normalising first.
    """
    from common.utils.utils import equals_ignore_case
    for key, dialect in DIALECT_REGISTRY.items():
        if equals_ignore_case(key, db_type):
            return dialect
    return _DEFAULT_DIALECT
