"""
Register all Phase 1 agent tools with the ToolRegistry.

Called once at application startup (or lazily on first agent execution).
Uses lazy imports so that tool modules (which depend on datasource/db
infrastructure) are only loaded when the full application context is available.
"""

from apps.chat.agent.tools.registry import ToolRegistry, ToolDef


def _lazy_import_tools() -> dict:
    """Lazy-load tool functions. Called once by register_all_tools()."""
    from apps.chat.agent.tools.schema_tools import (
        search_relevant_tables,
        get_table_metadata,
        get_table_sample_data,
    )
    from apps.chat.agent.tools.sql_tools import (
        create_sql_query,
        edit_sql_query,
    )
    from apps.chat.agent.tools.chart_tools import (
        create_chart,
        edit_chart,
    )
    from apps.chat.agent.tools.query_tools import (
        get_field_values,
        execute_sql_query,
    )
    from apps.chat.agent.tools.advanced_tools import (
        replace_sql_fragment,
        ask_for_clarification,
        analyze_query_result,
        load_skill,
    )
    return {
        "search_relevant_tables": search_relevant_tables,
        "get_table_metadata": get_table_metadata,
        "get_table_sample_data": get_table_sample_data,
        "create_sql_query": create_sql_query,
        "edit_sql_query": edit_sql_query,
        "create_chart": create_chart,
        "edit_chart": edit_chart,
        "get_field_values": get_field_values,
        "execute_sql_query": execute_sql_query,
        "replace_sql_fragment": replace_sql_fragment,
        "ask_for_clarification": ask_for_clarification,
        "analyze_query_result": analyze_query_result,
        "load_skill": load_skill,
    }


# Tool definitions stored as plain dicts to avoid eager imports.
# fn_key maps to a function loaded by _lazy_import_tools() at registration time.
_TOOL_DEFS: list[dict] = [
    # ── explore ──────────────────────────────────────────
    dict(name="search_relevant_tables", fn_key="search_relevant_tables", terminal=False, category="explore",
         description="搜索与用户问题最相关的表。返回表名和相关度分数，不含字段细节。在对话开始时调用以发现候选表。如果搜索返回空，用跨语言知识展开概念后重试。",
         parameters={"type": "object",
             "properties": {
                 "query": {"type": "string", "description": "搜索查询，使用用户问题的核心概念"},
                 "top_n": {"type": "integer", "description": "返回的最相关表数量，默认 5"},
             },
             "required": ["query", "top_n"]}),
    dict(name="get_table_metadata", fn_key="get_table_metadata", terminal=False, category="explore",
         description="获取单张表的完整字段结构（列名、类型、注释）。生成 SQL 前必须调用此工具获取所需表的字段信息，不要猜测字段名。如果表是 DataEase 数据集（is_dataset=true），还会返回底层可执行 SQL。",
         parameters={"type": "object",
             "properties": {
                 "table_name": {"type": "string", "description": "表名（精确匹配 search_relevant_tables 返回的名称）"},
             },
             "required": ["table_name"]}),
    dict(name="get_table_sample_data", fn_key="get_table_sample_data", terminal=False, category="explore",
         description="获取单张表的 3 行样本数据，帮助理解数据格式和实际值。",
         parameters={"type": "object",
             "properties": {"table_name": {"type": "string", "description": "表名"}},
             "required": ["table_name"]}),
    dict(name="get_field_values", fn_key="get_field_values", terminal=False, category="explore",
         description="获取字段的去重样本值（最多 20 个）。在写 WHERE 条件前使用——猜错值的格式会导致查询错误。",
         parameters={"type": "object",
             "properties": {
                 "table_name": {"type": "string", "description": "表名"},
                 "field_name": {"type": "string", "description": "字段名"},
             },
             "required": ["table_name", "field_name"]}),
    # ── create ──────────────────────────────────────────
    dict(name="create_sql_query", fn_key="create_sql_query", terminal=True, category="create",
         description="创建并验证一条新的 SQL 查询。验证 SQL 语法 → 检查引用的表是否已获取结构 → 确保只读。成功后返回 record_id，系统自动执行查询并生成图表。验证失败时返回具体错误——请修正后重新调用。",
         parameters={"type": "object",
             "properties": {"sql": {"type": "string", "description": "完整的 SQL SELECT 语句"}},
             "required": ["sql"]}),
    dict(name="create_chart", fn_key="create_chart", terminal=False, category="create",
         description="为已执行的查询创建图表配置。",
         parameters={"type": "object",
             "properties": {
                 "record_id": {"type": "string", "description": "关联的查询 record_id"},
                 "chart_config": {"type": "object", "description": "图表配置 JSON"},
             },
             "required": ["record_id", "chart_config"]}),
    # ── edit ────────────────────────────────────────────
    dict(name="edit_sql_query", fn_key="edit_sql_query", terminal=True, category="edit",
         description="通过字符串替换编辑已有 SQL 查询。提供 edits 列表，每项包含 old_string、new_string、replace_all（可选）。工具逐个应用替换后验证 SQL 有效性。",
         parameters={"type": "object",
             "properties": {
                 "record_id": {"type": "string", "description": "要编辑的查询 ID"},
                 "edits": {"type": "array", "items": {"type": "object",
                     "properties": {
                         "old_string": {"type": "string", "description": "要替换的文本，必须精确匹配"},
                         "new_string": {"type": "string", "description": "替换后的文本"},
                         "replace_all": {"type": "boolean", "description": "是否替换全部出现，默认 false（仅替换第一次）"},
                     },
                     "required": ["old_string", "new_string"]},
                     "description": "编辑操作列表"},
             },
             "required": ["record_id", "edits"]}),
    dict(name="edit_chart", fn_key="edit_chart", terminal=True, category="edit",
         description="修改已有图表的配置。仅更新指定的键，其余配置保持不变。用于追问'用柱状图'、'改标题为...'等场景——不会重新执行 SQL。",
         parameters={"type": "object",
             "properties": {
                 "chart_ref": {"type": "string", "description": "要编辑的图表引用 ID"},
                 "modifications": {"type": "object", "description": "要更新的配置项，如 {\"type\": \"column\"}"},
             },
             "required": ["chart_ref", "modifications"]}),
    # ── execute ──────────────────────────────────────────
    dict(name="execute_sql_query", fn_key="execute_sql_query", terminal=False, category="execute",
         description="执行已有的 SQL 查询并返回结果。结果会缓存到查询记录中。",
         parameters={"type": "object",
             "properties": {"record_id": {"type": "string", "description": "要执行的查询 record_id"}},
             "required": ["record_id"]}),
    # ── Phase 4 enhancements ────────────────────────────
    dict(name="replace_sql_fragment", fn_key="replace_sql_fragment", terminal=True, category="edit",
         description="替换 SQL 查询中的单个片段。适用于只改一处的情况，比 edit_sql_query 更简单。只需提供 old_string 和 new_string。",
         parameters={"type": "object",
             "properties": {
                 "record_id": {"type": "string", "description": "要编辑的查询 ID"},
                 "old_string": {"type": "string", "description": "要替换的文本，必须精确匹配"},
                 "new_string": {"type": "string", "description": "替换后的文本"},
                 "replace_all": {"type": "boolean", "description": "是否替换全部出现，默认 false（仅替换第一次）"},
             },
             "required": ["record_id", "old_string", "new_string"]}),
    dict(name="ask_for_clarification", fn_key="ask_for_clarification", terminal=True, category="explore",
         description="当用户的问题存在结构性模糊时，向用户提问澄清。例如'销售额'是指含税还是不含税。注意：能通过工具发现的信息不要问。",
         parameters={"type": "object",
             "properties": {"question": {"type": "string", "description": "向用户提出的澄清问题"}},
             "required": ["question"]}),
    dict(name="analyze_query_result", fn_key="analyze_query_result", terminal=False, category="execute",
         description="分析已执行的查询结果，产出数据洞察。需要在 execute_sql_query 之后使用。",
         parameters={"type": "object",
             "properties": {"record_id": {"type": "string", "description": "要分析的查询 record_id"}},
             "required": ["record_id"]}),
    dict(name="load_skill", fn_key="load_skill", terminal=False, category="explore",
         description="加载特定数据库方言的 SQL 编写指南。在生成复杂 SQL 前，根据数据源类型加载对应的技能。可用技能包括: sql-postgresql, sql-mysql, sql-clickhouse, sql-sqlserver, sql-oracle, sql-hive, sql-doris, sql-dm。",
         parameters={"type": "object",
             "properties": {"skill_id": {"type": "string", "description": "技能 ID，如 sql-postgresql"}},
             "required": ["skill_id"]}),
]


def register_all_tools() -> int:
    """Register all Phase 1 tools. Returns count of registered tools.

    Also pre-warms the embedding model to avoid 2+ minute cold-start
    on the first search_relevant_tables call.
    """
    try:
        fns = _lazy_import_tools()
    except ImportError as e:
        raise RuntimeError(
            "Cannot register Agent tools: missing dependencies. "
            "Ensure the full SQLBot backend is running (sqlalchemy, DB drivers, etc.). "
            f"Original error: {e}"
        ) from e

    for td in _TOOL_DEFS:
        fn = fns.get(td["fn_key"])
        if fn is None:
            raise ValueError(f"Unknown tool function key: {td['fn_key']}")

        tool = ToolDef(
            name=td["name"],
            description=td["description"],
            parameters=td["parameters"],
            fn=fn,
            terminal=td.get("terminal", False),
            category=td.get("category", ""),
        )
        ToolRegistry.register(tool)

    # Pre-warm embedding model (loads once, cached by EmbeddingModelCache)
    _warm_embedding_model()

    return len(_TOOL_DEFS)


def _warm_embedding_model() -> None:
    """Pre-load the embedding model at startup to avoid cold-start latency."""
    try:
        from apps.datasource.embedding.table_embedding import EmbeddingModelCache
        EmbeddingModelCache.get_model()
        from common.utils.utils import SQLBotLogUtil
        SQLBotLogUtil.info("[Agent] Embedding model pre-warmed")
    except Exception:
        pass  # Non-critical — first search_relevant_tables will load it
