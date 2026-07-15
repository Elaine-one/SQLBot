"""
Phase 4 enhanced tools for the SQLBot Agent.

replace_sql_fragment    — single-fragment replacement (simpler than edit_sql_query).
ask_for_clarification   — ask the user to disambiguate the question.
analyze_query_result    — analyze data and provide insights (/analysis).
load_skill              — load SQL dialect knowledge on demand.
"""

from __future__ import annotations

import os

from apps.chat.agent.memory import AgentMemory


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

    Reuses the existing analysis prompt template from template.yaml
    via the ChatQuestion.analysis_sys_question() / analysis_user_question() methods.
    """
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在"}
    if not query.data:
        return {"success": False, "error": "请先执行查询再分析"}

    # Build analysis prompt
    from apps.template.generate_analysis.generator import get_analysis_template
    fields = query.data.get("fields", [])
    data_rows = query.data.get("data", [])

    if not data_rows:
        return {"success": False, "error": "查询结果为空，无法分析"}

    system_tpl = get_analysis_template()
    system_msg = system_tpl["system"].format(
        lang="zh-CN",
        terminologies="",
        custom_prompt="",
        sqlbot_name="SQLBot",
    )
    user_msg = system_tpl["user"].format(
        fields=str(fields),
        data=str(data_rows[:50]),  # limit to 50 rows
    )

    if llm is None:
        return {"success": False, "error": "LLM not available for analysis"}

    messages = [
        type("SystemMessage", (), {"content": system_msg, "type": "system"})(),
        type("HumanMessage", (), {"content": user_msg, "type": "human"})(),
    ]
    response = llm.invoke([
        type("msg", (), {"content": system_msg, "type": "system", "sqlbot_system": True}),
        type("msg", (), {"content": user_msg, "type": "human", "sqlbot_system": False}),
    ])
    content = response.content if hasattr(response, "content") else str(response)
    return {"success": True, "record_id": record_id, "analysis": content}


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
