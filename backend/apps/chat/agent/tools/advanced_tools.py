"""
Phase 4 enhanced tools for the SQLBot Agent.

replace_sql_fragment    — single-fragment replacement (simpler than edit_sql_query).
ask_for_clarification   — ask the user to disambiguate the question.
analyze_query_result    — analyze data and provide insights (/analysis).
load_skill              — load SQL dialect knowledge on demand.
get_data_summary        — statistical summary of data fields (mem, no I/O).
get_data_preview        — paginated data rows (mem, no I/O).
search_web              — async web search for external info (predict only).
preview_sql             — execute a read-only SQL for internal exploration (non-terminal).
"""

from __future__ import annotations

import os
import re
import time
import asyncio

from apps.chat.agent.memory import AgentMemory


# ── preview_sql ─────────────────────────────────────────────

async def preview_sql(
    sql: str,
    memory: AgentMemory = None,
    max_rows: int = 20,
) -> dict:
    """Execute a read-only SQL query for internal agent exploration.

    NON-TERMINAL — the agent continues exploring after calling this.
    Results are returned to the agent ONLY; nothing is shown to the user.

    Use this when you need to verify a hypothesis about the data,
    check value distributions, or preview what a query would return —
    BEFORE committing to a final user-facing create_sql_query.

    DO NOT use this for the final answer — use create_sql_query for that.
    """
    from apps.chat.agent.tools.sql_tools import (
        _validate_sql_syntax, _extract_table_names,
    )
    from apps.chat.agent.compiler import DatasetSQLCompiler

    original_sql = sql
    ds_type = memory.datasource_type or ""

    # 1. Compile DataEase dataset references
    compiler = DatasetSQLCompiler(memory)
    try:
        compiled = compiler.compile(sql)
    except Exception as exc:
        return {"success": False, "error": f"SQL 编译失败: {exc}"}

    # 2. Syntax check
    valid, error = _validate_sql_syntax(compiled, ds_type)
    if not valid:
        return {"success": False, "error": error}

    # 3. Read-only check
    from apps.db.db import check_sql_read
    try:
        is_safe, reason = check_sql_read(compiled, memory.ds)
        if not is_safe:
            return {"success": False, "error": reason}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # 4. Table-existence check
    used = _extract_table_names(original_sql)
    unknown = [
        t for t in used
        if t not in memory.explored_tables and not compiler.is_dataset(t)
    ]
    if unknown:
        return {
            "success": False,
            "error": f"表 {unknown} 的字段结构尚未获取。请先调用 get_table_metadata",
        }

    # 5. Execute with row limit
    from apps.db.db import exec_sql
    try:
        result = exec_sql(ds=memory.ds, sql=compiled)
        rows = result.get("data", [])
        fields = result.get("fields", [])
        total = len(rows)

        # Truncate to max_rows to protect context window
        truncated = rows[:max_rows] if total > max_rows else rows

        return {
            "success": True,
            "row_count": total,
            "returned": len(truncated),
            "truncated": total > max_rows,
            "columns": fields,
            "rows": truncated,
        }
    except Exception as exc:
        return {"success": False, "error": f"SQL 执行失败: {exc}"}


# ── replace_sql_fragment ──────────────────────────────────

async def replace_sql_fragment(
    record_id: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    memory: AgentMemory = None,
) -> dict:
    """
    Replace a specific fragment in an existing SQL query.

    Simplifies the common case of a single replacement vs. the
    list-based edit_sql_query.  Internally delegates to edit_sql_query.
    """
    from apps.chat.agent.tools.sql_tools import edit_sql_query

    return await edit_sql_query(
        record_id=record_id,
        edits=[{
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        }],
        memory=memory,
    )


# ── ask_for_clarification ─────────────────────────────────

async def ask_for_clarification(
    question: str,
    memory: AgentMemory = None,
) -> dict:
    """
    Ask the user a clarifying question (TERMINAL tool).

    Stops the agent loop.  The frontend should display the question
    prominently and let the user type an answer.  The answer is sent
    as a new message in the same chat, starting a fresh agent run.

    Example: "销售额是指含税还是不含税？"
    """
    memory.terminal_triggered = True
    return {
        "success": True,
        "clarification": question,
        "action": "user_input_required",
    }


# ── analyze_query_result ──────────────────────────────────

async def analyze_query_result(
    record_id: str,
    memory: AgentMemory,
    llm=None,
) -> dict:
    """
    Analyze the executed query results and produce data insights.

    The LLM is injected via memory._llm by AgentExecutor, so the tool
    does not depend on the Agent passing it as a function-call argument.
    """
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在"}
    if not query.data:
        return {"success": False, "error": "请先执行查询再分析"}

    fields = query.data.get("fields", [])
    data_rows = query.data.get("data", [])

    if not data_rows:
        return {"success": False, "error": "查询结果为空，无法分析"}

    # LLM is injected via memory, not via tool-call args
    if llm is None:
        llm = getattr(memory, '_llm', None)
    if llm is None:
        return {"success": False, "error": "LLM not available for analysis"}

    from apps.template.generate_analysis.generator import get_analysis_template
    system_tpl = get_analysis_template()
    sqlbot_name = getattr(memory, "sqlbot_name", None) or "SQLBot"
    system_msg = system_tpl["system"].format(
        lang="zh-CN",
        terminologies="",
        custom_prompt="",
        sqlbot_name=sqlbot_name,
    )
    user_msg = system_tpl["user"].format(
        fields=str(fields),
        data=str(data_rows[:50]),
    )

    from langchain_core.messages import SystemMessage as LCSystem, HumanMessage as LCHuman

    response = llm.invoke([
        LCSystem(content=system_msg),
        LCHuman(content=user_msg),
    ])
    content = response.content if hasattr(response, "content") else str(response)
    return {"success": True, "record_id": record_id, "analysis": content}


# ── get_data_summary ───────────────────────────────────────

async def get_data_summary(
    record_id: str,
    memory: AgentMemory,
) -> dict:
    """返回数据字段的统计摘要（纯内存，无 I/O）。

    对数值字段计算 min/max/avg/sum，分类字段计算去重数，
    日期字段给出时间范围。
    """
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在"}
    if not query.data:
        return {"success": False, "error": "查询结果为空"}

    raw_fields = query.data.get("fields", [])
    raw_rows = query.data.get("data", [])
    if not raw_rows:
        return {"success": False, "error": "查询结果为空，无法统计"}

    # ── Normalize fields to list[str] ──
    fields: list[str] = []
    for f in raw_fields:
        if isinstance(f, dict):
            fields.append(f.get("name", "") or str(f))
        else:
            fields.append(str(f) if f is not None else "")

    if not fields:
        return {"success": False, "error": "字段列表为空"}

    # ── Normalize rows to list[list] ──
    rows: list[list] = []
    for r in raw_rows:
        if isinstance(r, (list, tuple)):
            rows.append(list(r))
        elif isinstance(r, dict):
            rows.append([r.get(f, None) for f in fields])
        else:
            continue  # skip non-row entries (e.g. header strings mixed in)

    if not rows:
        return {"success": False, "error": "数据行格式无法解析"}

    # ── Per-column stats ──
    summaries: list[dict] = []
    for ci, name in enumerate(fields):
        col = _column_values(rows, ci)
        summaries.append(_summarize_column(name, col))

    # ── Chart type hint ──
    chart_type = "table"
    if memory.charts:
        for cr in memory.charts.values():
            if cr.record_id == record_id and cr.chart_config:
                cfg = cr.chart_config
                if isinstance(cfg, dict):
                    chart_type = cfg.get("type", "table")
                break

    return {
        "success": True,
        "record_id": record_id,
        "row_count": len(rows),
        "field_count": len(summaries),
        "fields": summaries,
        "chart_type": chart_type,
    }


def _column_values(rows: list[list], col_idx: int) -> list:
    """Safely extract column values from rows, skipping nulls."""
    vals = []
    for r in rows:
        if col_idx < len(r):
            v = r[col_idx]
            if v is not None and v != "":
                vals.append(v)
    return vals


def _summarize_column(name: str, vals: list) -> dict:
    """Compute stats for a single column."""
    if not vals:
        return {"name": name, "type": "unknown", "note": "全为 null 或空"}

    # ── Try numeric ──
    nums = []
    for v in vals:
        if isinstance(v, (int, float)):
            nums.append(float(v))
        elif isinstance(v, str):
            try:
                nums.append(float(v))
            except (ValueError, TypeError):
                pass

    if len(nums) >= len(vals) * 0.7:
        return {
            "name": name, "type": "numeric",
            "count": len(nums),
            "min": round(min(nums), 2),
            "max": round(max(nums), 2),
            "avg": round(sum(nums) / len(nums), 2),
            "sum": round(sum(nums), 2),
        }

    # ── Try date ──
    date_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}')
    str_vals = [v for v in vals if isinstance(v, str)]
    date_vals = [v for v in str_vals if date_pattern.match(v)]
    if len(date_vals) >= len(str_vals) * 0.5 and date_vals:
        date_vals.sort()
        return {
            "name": name, "type": "date",
            "count": len(date_vals),
            "min": date_vals[0],
            "max": date_vals[-1],
            "distinct": len(set(date_vals)),
        }

    # ── Categorical ──
    cat_vals = [str(v) for v in vals]
    uniq = list(dict.fromkeys(cat_vals))  # order-preserving dedup
    return {
        "name": name, "type": "varchar",
        "distinct": len(uniq),
        "sample": uniq[:10],
    }


# ── get_data_preview ───────────────────────────────────────

async def get_data_preview(
    record_id: str,
    offset: int,
    limit: int,
    memory: AgentMemory,
) -> dict:
    """分页返回数据行（纯内存，无 I/O）。

    Agent 用它在分析过程中按需翻阅具体数据，
    每次取一页（默认 20 行），避免一次性把所有数据塞进上下文。
    """
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在"}
    if not query.data:
        return {"success": False, "error": "查询结果为空"}

    rows = query.data.get("data", [])
    total = len(rows)

    if offset < 0:
        offset = 0
    if offset >= total:
        return {"success": True, "rows": [], "offset": offset, "returned": 0,
                "total": total, "has_more": False}
    if limit <= 0:
        limit = 20
    if limit > 100:
        limit = 100

    slice_rows = rows[offset:offset + limit]

    # Truncate each row for context safety (200 chars per cell)
    safe_rows = []
    for row in slice_rows:
        safe_rows.append([str(v)[:200] if v is not None else None for v in row])

    return {
        "success": True,
        "rows": safe_rows,
        "offset": offset,
        "returned": len(safe_rows),
        "total": total,
        "has_more": (offset + limit) < total,
    }


# ── load_skill ────────────────────────────────────────────

# Subdirectory for business context files (loaded by load_skill with "business-" prefix)
_BUSINESS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                             "templates", "business")

# Map of skill IDs to template files and descriptions.
_SKILL_CATALOG: dict[str, dict] = {
    # ── business context ──
    "business-erp_cross_border": {
        "file": "erp_cross_border.yaml",
        "dir": "business",
        "desc": "跨境电商 ERP 业务上下文：数据域分类、字段语义、利润计算公式、常见陷阱",
    },
    "business-erp_metrics": {
        "file": "erp_metrics.json",
        "dir": "business",
        "desc": "ERP 基础指标 SQL 片段：收入、成本、费用、订单数、销量等（含别名和表关联）",
    },
    "business-erp_derived_metrics": {
        "file": "erp_derived_metrics.json",
        "dir": "business",
        "desc": "ERP 派生指标公式：毛利率、净利润、净利润率、客单价、件单价",
    },
    "business-erp_segments": {
        "file": "erp_segments.json",
        "dir": "business",
        "desc": "ERP 可复用筛选条件：有效订单、中国以外、已完成发货",
    },
    # ── SQL dialects ──
    "sql-postgresql": {
        "file": "PostgreSQL.yaml",
        "desc": "PostgreSQL 特有语法：双引号引用、::类型转换、DISTINCT ON、窗口函数、JSONB 操作",
    },
    "sql-mysql": {
        "file": "MySQL.yaml",
        "desc": "MySQL 特有语法：反引号引用、日期函数(DATE_FORMAT)、GROUP_CONCAT、IFNULL",
    },
    "sql-clickhouse": {
        "file": "ClickHouse.yaml",
        "desc": "ClickHouse 特有语法：数组函数、聚合组合器、ARRAY JOIN、LIMIT BY",
    },
    "sql-sqlserver": {
        "file": "Microsoft_SQL_Server.yaml",
        "desc": "SQL Server 特有语法：方括号引用、TOP N、GETDATE()、DATEADD",
    },
    "sql-oracle": {
        "file": "Oracle.yaml",
        "desc": "Oracle 特有语法：双引号引用、ROWNUM、TO_DATE()、CONNECT BY",
    },
    "sql-hive": {
        "file": "Hive.yaml",
        "desc": "Hive 特有语法：反引号引用、PARTITION BY、LATERAL VIEW、EXPLODE",
    },
    "sql-doris": {
        "file": "Doris.yaml",
        "desc": "Doris 特有语法：反引号引用、BITMAP 类型、DUAL 表、聚合模型",
    },
    "sql-dm": {
        "file": "DM.yaml",
        "desc": "达梦数据库特有语法：双引号引用、ROWNUM、TO_CHAR()、层次查询",
    },
}

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                            "templates", "sql_examples")


def get_skill_catalog() -> list[dict]:
    """Return the available skills catalog (for system prompt injection)."""
    return [
        {"id": sid, "description": info["desc"]}
        for sid, info in _SKILL_CATALOG.items()
    ]


async def load_skill(
    skill_id: str,
    memory: AgentMemory = None,
) -> dict:
    """
    Load SQL dialect knowledge on demand.

    Instead of embedding all SQL examples in the initial prompt,
    the LLM calls this tool when it needs dialect-specific guidance.

    Inspired by Metabase's Skills system.
    """
    info = _SKILL_CATALOG.get(skill_id)
    if not info:
        return {
            "success": False,
            "error": f"未知技能: {skill_id}",
            "available_skills": list(_SKILL_CATALOG.keys()),
        }

    # Support "business" subdirectory for business context files
    base_dir = _BUSINESS_DIR if info.get("dir") == "business" else _SKILLS_DIR
    filepath = os.path.join(base_dir, info["file"])
    if not os.path.exists(filepath):
        return {"success": False, "error": f"技能文件不存在: {info['file']}"}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "success": True,
            "skill_id": skill_id,
            "description": info["desc"],
            "content": content[:3000],  # Truncate to avoid context overflow
            "truncated": len(content) > 3000,
        }
    except Exception as exc:
        return {"success": False, "error": f"加载技能失败: {exc}"}


# ── search_web ─────────────────────────────────────────────

# TTL cache: {query_lower: (timestamp, result_dict)}
_search_cache: dict[str, tuple[float, dict]] = {}
_SEARCH_CACHE_TTL: float = 300.0  # 5 minutes

# User-Agent pool for Bing scraping (avoids fingerprinting)
_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.1.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
]


async def search_web(
    query: str,
    memory: AgentMemory = None,
    max_results: int = 5,
) -> dict:
    """搜索互联网获取外部信息（预测 Agent 专用）。

    使用 Bing 国内版 (cn.bing.com) 免费搜索，无需 API Key、无需代理。
    结果缓存 5 分钟。失败时返回空结果 + 错误描述，
    Agent 可用现有数据继续预测，不阻塞整体流程。
    """
    if not query or not query.strip():
        return {"success": False, "error": "搜索关键词不能为空"}

    query = query.strip()[:200]
    cache_key = query.lower()

    # ── Check cache ────────────────────
    if cache_key in _search_cache:
        ts, cached = _search_cache[cache_key]
        if time.monotonic() - ts < _SEARCH_CACHE_TTL:
            return {**cached, "cached": True}

    t0 = time.monotonic()

    try:
        import random
        import requests
        from bs4 import BeautifulSoup

        # ── Resolve config (graceful fallback for tests) ──
        try:
            from common.core.config import settings
            timeout = int(getattr(settings, "BING_SEARCH_TIMEOUT", 10))
            req_max = min(max_results, int(getattr(settings, "BING_SEARCH_MAX_RESULTS", 5)))
        except Exception:
            timeout = 10
            req_max = min(max_results, 5)

        headers = {
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        }

        resp = requests.get(
            "https://cn.bing.com/search",
            params={"q": query, "count": req_max * 2},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        raw_results: list[dict] = []

        # ── Primary parser: b_algo list items ──
        for item in soup.find_all("li", class_="b_algo"):
            try:
                title_tag = item.find("h2")
                link_tag = item.find("a")
                caption_tag = (
                    item.find("p")
                    or item.find("div", class_="b_caption")
                    or item.find("div", class_="b_snippet")
                )
                if title_tag and link_tag:
                    raw_results.append({
                        "title": title_tag.get_text().strip(),
                        "url": link_tag.get("href", ""),
                        "snippet": caption_tag.get_text().strip() if caption_tag else "",
                    })
            except Exception:
                continue

        # ── Fallback: any H2 + link pairs (for Bing UI variations) ──
        if len(raw_results) < 2:
            for item in soup.select("li h2 a"):
                try:
                    title = item.get_text().strip()
                    link = item.get("href", "")
                    if link and link.startswith("http") and title:
                        raw_results.append({
                            "title": title,
                            "url": link,
                            "snippet": "",
                        })
                except Exception:
                    pass

        # ── Deduplicate by URL ──
        seen: set[str] = set()
        results: list[dict] = []
        for r in raw_results:
            if r["url"] not in seen:
                seen.add(r["url"])
                results.append({
                    "title": r["title"][:200],
                    "snippet": r["snippet"][:500],
                    "url": r["url"][:500],
                })
                if len(results) >= req_max:
                    break

        elapsed = time.monotonic() - t0

        if not results:
            outcome = {
                "success": True, "query": query, "results": [],
                "note": "未找到相关结果，请尝试换一个搜索词",
                "search_time": f"{elapsed:.1f}s",
            }
        else:
            outcome = {
                "success": True, "query": query, "results": results,
                "count": len(results), "search_time": f"{elapsed:.1f}s",
            }

        _search_cache[cache_key] = (time.monotonic(), outcome)
        return outcome

    except Exception as e:
        elapsed = time.monotonic() - t0
        err_msg = str(e)
        if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
            return {
                "success": False,
                "error": f"搜索超时({elapsed:.0f}s)：请减小搜索范围或用现有数据继续预测",
            }
        if "ConnectionError" in type(e).__name__:
            return {
                "success": False,
                "error": "无法连接搜索服务，请检查网络连接",
            }
        return {
            "success": False,
            "error": f"搜索失败({elapsed:.0f}s): 网络或服务异常，可用现有数据继续预测",
        }
