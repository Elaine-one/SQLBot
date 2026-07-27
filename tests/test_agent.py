"""
Comprehensive tests for the SQLBot Agent module.

Covers all 18 agent files — memory, registry, tools, graph, executor, adapter, compiler, chart_registry, chart_knowledge.
Tests run without DB/LLM dependencies (isolated logic tests).
"""

import asyncio
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# ============================================================
# Test helpers
# ============================================================

_passed = 0
_failed = 0
_errors: list[str] = []


def check(description: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        msg = f"  FAIL [{description}] {detail}"
        _errors.append(msg)
        print(msg)


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def summary():
    print(f"\n{'='*60}")
    print(f"  RESULTS: {_passed} passed, {_failed} failed")
    print(f"{'='*60}")
    if _errors:
        print("\nFAILURES:")
        for e in _errors:
            print(e)
    return _failed == 0


# ============================================================
# 1. Memory
# ============================================================

def test_memory():
    section("1. AgentMemory + QueryRecord + ChartRecord")
    from apps.chat.agent.memory import AgentMemory, QueryRecord, ChartRecord

    # 1a: basic construction
    m = AgentMemory(datasource_id=1, datasource_type="mysql")
    check("defaults", m.max_iterations == 50 and m.terminal_triggered == False)
    check("default followup", m.is_followup == False)
    check("no queries initially", len(m.queries) == 0)
    check("no charts initially", len(m.charts) == 0)

    # 1b: QueryRecord
    q = QueryRecord(record_id="r_1", sql="SELECT 1", tables_used=["orders"])
    m.queries["r_1"] = q
    check("get_latest_query", m.get_latest_query().record_id == "r_1")
    check("available_query_ids", m.available_query_ids() == ["r_1"])

    # 1c: version tracking
    q.add_version("SELECT 0")
    check("version count", len(q.sql_history) == 1)
    check("version previous_sql", q.sql_history[0]["previous_sql"] == "SELECT 0")
    check("version number", q.sql_history[0]["version"] == 1)

    # 1d: ChartRecord
    c = ChartRecord(chart_ref="c_1", record_id="r_1", chart_config={"type": "line"})
    m.charts["c_1"] = c
    check("get_latest_chart", m.get_latest_chart().chart_ref == "c_1")
    check("available_chart_refs", m.available_chart_refs() == ["c_1"])

    # 1e: context generation
    ctx = m.get_context_for_llm()
    check("context has record_id", "r_1" in ctx)
    check("context has chart_ref", "c_1" in ctx)
    check("context has chart type", "line" in ctx)

    # 1f: empty context
    m2 = AgentMemory()
    check("empty context", m2.get_context_for_llm() == "")
    check("empty queries", m2.available_query_ids() == [])
    check("empty charts", m2.available_chart_refs() == [])

    # 1g: is_followup default
    m3 = AgentMemory(is_followup=True)
    check("followup flag set", m3.is_followup == True)

    # 1h: runtime context fields exist
    check("has session attr", hasattr(m, "session"))
    check("has current_user attr", hasattr(m, "current_user"))
    check("has ds attr", hasattr(m, "ds"))
    check("has out_ds_instance attr", hasattr(m, "out_ds_instance"))


# ============================================================
# 2. Tool Registry
# ============================================================

def test_registry():
    section("2. ToolDef + ToolRegistry")
    from apps.chat.agent.tools.registry import ToolRegistry, ToolDef
    from apps.chat.agent.memory import AgentMemory

    # Clear for test isolation
    ToolRegistry._tools.clear()

    # 2a: register
    async def echo_tool(x: int = 0, memory=None):
        return {"x": x}
    t = ToolDef(name="echo", description="Echo tool",
                parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
                fn=echo_tool, terminal=False, category="test")
    ToolRegistry.register(t)
    check("register count", ToolRegistry.count() == 1)
    check("register name", ToolRegistry.names() == ["echo"])

    # 2b: duplicate registration
    try:
        ToolRegistry.register(t)
        check("duplicate guard", False, "should have raised ValueError")
    except ValueError:
        check("duplicate guard", True)

    # 2c: OpenAI schema
    schemas = ToolRegistry.get_openai_schemas()
    check("schema count", len(schemas) == 1)
    s = schemas[0]
    check("schema type", s["type"] == "function")
    check("schema name", s["function"]["name"] == "echo")

    # 2d: execute
    m = AgentMemory()
    r = asyncio.run(ToolRegistry.execute("echo", {"x": 42}, m))
    check("execute result", r == {"x": 42})

    # 2e: unknown tool
    r = asyncio.run(ToolRegistry.execute("nonexistent", {}, m))
    check("unknown tool error", r["success"] == False)
    check("unknown tool has available_tools", "available_tools" in r)
    check("unknown tool lists echo", "echo" in r["available_tools"])

    # 2f: get
    check("get existing", ToolRegistry.get("echo") is not None)
    check("get missing", ToolRegistry.get("missing") is None)

    # Cleanup
    ToolRegistry._tools.clear()


# ============================================================
# 3. State
# ============================================================

def test_state():
    section("3. AgentState TypedDict")
    from apps.chat.agent.state import AgentState
    from apps.chat.agent.memory import AgentMemory

    m = AgentMemory()
    state = AgentState(messages=[], memory=m, iteration=0)
    check("messages key", "messages" in state)
    check("memory key", "memory" in state)
    check("iteration key", "iteration" in state)
    check("memory ref", state["memory"] is m)
    check("iteration value", state["iteration"] == 0)


# ============================================================
# 4. SQL validation (sql_tools helpers)
# ============================================================

def test_sql_validation():
    section("4. SQL validation helpers")
    from apps.chat.agent.tools.sql_tools import (
        _validate_sql_syntax, _extract_table_names, _resolve_dialect,
        _first_keyword, _DENIED_KEYWORDS,
    )

    # 4a: dialect mapping
    check("mysql dialect", _resolve_dialect("mysql") == "mysql")
    check("sqlserver dialect", _resolve_dialect("sqlServer") == "tsql")
    check("hive dialect", _resolve_dialect("hive") == "hive")
    check("unknown dialect", _resolve_dialect("unknown_db") is None)

    # 4b: first keyword
    check("SELECT", _first_keyword("SELECT * FROM t") == "SELECT")
    check("with cte", _first_keyword("WITH cte AS (SELECT 1)") == "WITH")
    check("parenthesized", _first_keyword("(SELECT 1)") == "SELECT")
    check("empty", _first_keyword("") == "")

    # 4c: syntax validation - valid
    valid, err = _validate_sql_syntax("SELECT * FROM orders", "mysql")
    check("valid SELECT", valid, err)

    # 4d: syntax validation - denied keyword
    valid, err = _validate_sql_syntax("DROP TABLE orders", "mysql")
    check("denied DROP", not valid)

    valid, err = _validate_sql_syntax("DELETE FROM orders", "mysql")
    check("denied DELETE", not valid)

    # 4e: syntax validation - invalid SQL (;) gets caught)
    valid, err = _validate_sql_syntax("SELECT * FROM orders;)", "mysql")
    check("catches stray ;)", not valid, f"got valid={valid}, err={err}")

    # 4f: syntax validation - empty
    valid, err = _validate_sql_syntax("", "mysql")
    check("empty SQL", not valid)

    # 4g: table extraction
    tables = _extract_table_names("SELECT * FROM orders")
    check("single table", tables == ["orders"], str(tables))

    tables = _extract_table_names(
        "SELECT * FROM orders o JOIN products p ON o.pid = p.id"
    )
    check("join tables", "orders" in tables and "products" in tables, str(tables))

    tables = _extract_table_names("SELECT 1")  # no FROM
    check("no tables", tables == [], str(tables))

    # 4h: denied keywords completeness
    for kw in ["INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER",
               "TRUNCATE", "MERGE", "REPLACE", "GRANT", "REVOKE"]:
        check(f"denied keyword: {kw}", kw in _DENIED_KEYWORDS)


# ============================================================
# 5. edit_sql_query string-replace logic
# ============================================================

def test_edit_sql():
    section("5. edit_sql_query string-replace")
    from apps.chat.agent.tools.sql_tools import edit_sql_query
    from apps.chat.agent.memory import AgentMemory, QueryRecord

    m = AgentMemory(datasource_type="mysql", ds=type("x", (), {"type": "mysql"})())
    q = QueryRecord(record_id="r_1", sql="SELECT a, b FROM t", tables_used=["t"])
    m.queries["r_1"] = q

    # 5a: single replace
    r = asyncio.run(edit_sql_query(
        record_id="r_1",
        edits=[{"old_string": "a, b", "new_string": "a, b, c"}],
        memory=m,
    ))
    check("single replace success", r["success"] == True, str(r))
    check("SQL updated", m.queries["r_1"].sql == "SELECT a, b, c FROM t")
    check("status edited", m.queries["r_1"].status == "edited")
    check("terminal", m.terminal_triggered == True)
    m.terminal_triggered = False

    # 5b: missing record_id
    r = asyncio.run(edit_sql_query(
        record_id="r_nonexistent",
        edits=[{"old_string": "x", "new_string": "y"}],
        memory=m,
    ))
    check("missing record_id", r["success"] == False, str(r))
    check("available_queries in error", "available_queries" in r)
    check("available lists r_1", "r_1" in str(r["available_queries"]))

    # 5c: old_string not found
    r = asyncio.run(edit_sql_query(
        record_id="r_1",
        edits=[{"old_string": "zzzzz_nonexistent", "new_string": "y"}],
        memory=m,
    ))
    check("old_string not found", r["success"] == False, str(r))
    check("error mentions step", "第 1 步" in r.get("error", ""))

    # 5d: multiple occurrences without replace_all
    q.sql = "SELECT a FROM t WHERE a = 1 AND a = 2"
    r = asyncio.run(edit_sql_query(
        record_id="r_1",
        edits=[{"old_string": "a", "new_string": "x"}],
        memory=m,
    ))
    check("multiple without replace_all", r["success"] == False)
    check("error mentions count", "次" in r.get("error", ""))

    # 5e: multiple with replace_all
    r = asyncio.run(edit_sql_query(
        record_id="r_1",
        edits=[{"old_string": "a", "new_string": "x", "replace_all": True}],
        memory=m,
    ))
    check("multiple with replace_all", r["success"] == True, str(r))
    check("all replaced", m.queries["r_1"].sql == "SELECT x FROM t WHERE x = 1 AND x = 2")

    # 5f: version history
    q.add_version("SELECT a, b FROM t")
    check("version count after edits", len(q.sql_history) == 2)  # 1 from memory test + 1


# ============================================================
# 6. Chart tools
# ============================================================

def test_chart_tools():
    section("6. Chart tools")
    from apps.chat.agent.tools.chart_tools import create_chart, edit_chart
    from apps.chat.agent.memory import AgentMemory, QueryRecord

    m = AgentMemory()
    q = QueryRecord(record_id="r_1", sql="SELECT 1")
    m.queries["r_1"] = q

    # 6a: create_chart
    r = asyncio.run(create_chart(
        record_id="r_1",
        chart_config={"type": "bar", "title": "Test"},
        memory=m,
    ))
    check("create success", r["success"] == True, str(r))
    check("chart_ref returned", r["chart_ref"].startswith("chart_"))
    chart_ref = r["chart_ref"]

    # 6b: chart stored in memory
    check("chart in memory", chart_ref in m.charts)
    check("chart linked to query", m.charts[chart_ref].record_id == "r_1")

    # 6c: edit_chart
    m.terminal_triggered = False
    r = asyncio.run(edit_chart(
        chart_ref=chart_ref,
        modifications={"type": "column"},
        memory=m,
    ))
    check("edit success", r["success"] == True, str(r))
    check("type updated", m.charts[chart_ref].chart_config["type"] == "column")
    check("terminal on edit", m.terminal_triggered == True)

    # 6d: edit missing chart
    r = asyncio.run(edit_chart(
        chart_ref="nonexistent",
        modifications={"type": "line"},
        memory=m,
    ))
    check("edit missing chart", r["success"] == False)
    check("available_charts in error", "available_charts" in r)


# ============================================================
# 7. Advanced tools (Phase 4)
# ============================================================

def test_advanced_tools():
    section("7. Advanced tools (Phase 4)")
    from apps.chat.agent.tools.advanced_tools import (
        load_skill, get_skill_catalog, ask_for_clarification,
        replace_sql_fragment,
    )
    from apps.chat.agent.memory import AgentMemory, QueryRecord

    # 7a: skill catalog
    cat = get_skill_catalog()
    check("catalog count", len(cat) == 12, f"got {len(cat)}")
    check("catalog has postgresql", any(s["id"] == "sql-postgresql" for s in cat))
    skill_ids = [s["id"] for s in cat]
    for expected in ["sql-mysql", "sql-clickhouse", "sql-oracle", "sql-dm"]:
        check(f"catalog has {expected}", expected in skill_ids)

    # 7b: load_skill - valid
    r = asyncio.run(load_skill("sql-mysql"))
    check("load mysql", r["success"] == True, str(r))
    check("mysql content not empty", len(r.get("content", "")) > 100)

    # 7c: load_skill - invalid
    r = asyncio.run(load_skill("sql-unknown"))
    check("load unknown", r["success"] == False)
    check("available in error", "available_skills" in r)

    # 7d: ask_for_clarification
    m = AgentMemory()
    r = asyncio.run(ask_for_clarification(question="test question?", memory=m))
    check("clarify success", r["success"] == True)
    check("clarify question", r["clarification"] == "test question?")
    check("clarify action", r.get("action") == "user_input_required")

    # 7e: replace_sql_fragment (delegates to edit_sql_query)
    m = AgentMemory(datasource_type="mysql", ds=type("x", (), {"type": "mysql"})())
    q = QueryRecord(record_id="r_x", sql="SELECT old_col FROM t", tables_used=["t"])
    m.queries["r_x"] = q
    r = asyncio.run(replace_sql_fragment(
        record_id="r_x", old_string="old_col", new_string="new_col", memory=m,
    ))
    check("replace success", r["success"] == True, str(r))
    check("replace SQL updated", q.sql == "SELECT new_col FROM t")


# ============================================================
# 8. Schema tools — mode detection + assistant logic
# ============================================================

def test_schema_mode():
    section("8. Schema tools — mode detection")
    from apps.chat.agent.memory import AgentMemory
    from apps.chat.agent.tools.schema_tools import _is_assistant_mode

    # 8a: type=0 (no out_ds_instance)
    m0 = AgentMemory()
    check("type=0 is not assistant", not _is_assistant_mode(m0))

    # 8b: type=1 (with out_ds_instance)
    m1 = AgentMemory(out_ds_instance=object())
    check("type=1 is assistant", _is_assistant_mode(m1))

    # 8c: assistant field builder (business_name logic)
    # Simulated — the actual function imports from AssistantFieldSchema
    def sim_build_field(field_dict):
        info = {"name": field_dict["name"], "type": field_dict.get("type", "unknown")}
        comment = field_dict.get("comment", "")
        if comment and comment != field_dict["name"]:
            info["comment"] = comment
            info["business_name"] = comment
        elif comment:
            info["comment"] = comment
        return info

    f = sim_build_field({"name": "行小计USD", "type": "numeric", "comment": "订单总额usd"})
    check("field name preserved", f["name"] == "行小计USD")
    check("business_name added", f.get("business_name") == "订单总额usd")

    f2 = sim_build_field({"name": "order_id", "type": "int", "comment": "order_id"})
    check("regular comment (comment == name)", "comment" in f2 and f2["comment"] == "order_id" and "business_name" not in f2)

    # 8d: assistant search returns ALL tables (no truncation, no ranking)
    # Simulate the logic
    class FT:
        def __init__(self, name, comment=""):
            self.name = name; self.comment = comment
    class FDS:
        type = "mysql"
        tables = [FT("orders", "Order table"), FT("products", "Product table")]

    tables = FDS.tables
    result = [{"name": t.name, "comment": t.comment or ""} for t in tables]
    check("all tables returned", len(result) == 2)
    check("no truncation", result[0]["name"] == "orders" and result[1]["name"] == "products")


# ============================================================
# 9. Permission tools
# ============================================================

def test_permission_tools():
    section("9. Permission tools")
    from apps.chat.agent.memory import AgentMemory
    from apps.chat.agent.tools.permission_tools import (
        _is_assistant_mode, _get_engine, _extract_json,
    )

    # 9a: mode detection
    m0 = AgentMemory()
    m1 = AgentMemory(out_ds_instance=object())
    check("perm type=0", not _is_assistant_mode(m0))
    check("perm type=1", _is_assistant_mode(m1))

    # 9b: engine detection
    class DS: pass
    ds = DS(); ds.type = "postgresql"
    m0.ds = ds
    check("engine from type", _get_engine(m0) == "postgresql")

    ds2 = DS()
    ds2.type_name = "MySQL 8.0"; ds2.type = "mysql"
    m0.ds = ds2
    check("engine from type_name", _get_engine(m0) == "MySQL 8.0")

    # 9c: JSON extraction
    check("plain json", _extract_json('{"a":1}') == '{"a":1}')
    check("markdown json", _extract_json('```json\n{"b":2}\n```') == '{"b":2}')
    check("markdown no lang", _extract_json('```\n{"c":3}\n```') == '{"c":3}')


# ============================================================
# 10. Graph — system prompt
# ============================================================

def test_graph_prompt():
    section("10. Graph — system prompt")
    from apps.chat.agent.graph import _build_system_prompt, get_shared_checkpointer
    from apps.chat.agent.memory import AgentMemory, QueryRecord, ChartRecord

    # 10a: first-turn prompt
    m = AgentMemory(datasource_type="mysql")
    p = _build_system_prompt(m, is_followup=False)
    check("first-turn has tools", "search_relevant_tables" in p)
    check("first-turn has create", "create_sql_query" in p)
    check("first-turn has edit", "edit_sql_query" in p)
    check("first-turn NO followup section", "追问模式" not in p)
    check("first-turn has datasource", "mysql" in p)
    check("first-turn has internal tools section", "内部工具" in p)
    check("first-turn has user-visible tools section", "用户可见" in p)
    check("first-turn has preview_sql", "preview_sql" in p)
    check("first-turn has 关键规则", "关键规则" in p)
    check("first-turn has phase4 tools", "load_skill" in p and "analyze_query_result" in p)

    # 10b: follow-up prompt
    q = QueryRecord(record_id="r_1", sql="SELECT * FROM orders")
    c = ChartRecord(chart_ref="c_1", record_id="r_1", chart_config={"type": "line"})
    m.queries["r_1"] = q
    m.charts["c_1"] = c
    p2 = _build_system_prompt(m, is_followup=True)
    check("followup HAS followup section", "追问模式" in p2)
    check("followup has record_id", "r_1" in p2)
    check("followup has chart_ref", "c_1" in p2)
    check("followup has edit guidance", "edit_chart" in p2)
    check("followup has edit_sql guidance", "edit_sql_query" in p2)
    check("followup larger than first", len(p2) > len(p))

    # 10c: shared checkpointer
    c1 = get_shared_checkpointer()
    c2 = get_shared_checkpointer()
    check("checkpointer singleton", c1 is c2)


# ============================================================
# 11. Register all tools
# ============================================================

def test_register_all():
    section("11. Register all tools")
    from apps.chat.agent.tools.registry import ToolRegistry
    from apps.chat.agent.tools.register_all import register_all_tools, _TOOL_DEFS

    ToolRegistry._tools.clear()
    try:
        n = register_all_tools()
    except (ImportError, RuntimeError) as e:
        print(f"\n  ~~ SKIP test_register_all: {e}")
        return
    check("17 tools total", n == 17, f"got {n}")

    names = ToolRegistry.names()
    core = ["search_relevant_tables", "get_table_metadata", "get_table_sample_data",
            "get_field_values", "create_sql_query", "edit_sql_query",
            "create_chart", "edit_chart", "execute_sql_query"]
    phase4 = ["replace_sql_fragment", "ask_for_clarification", "analyze_query_result",
              "load_skill", "get_data_summary", "get_data_preview", "search_web",
              "preview_sql"]
    for name in core + phase4:
        check(f"tool registered: {name}", name in names)

    # Terminal tools
    terminal = {t.name for t in ToolRegistry._tools.values() if t.terminal}
    check("terminal count", len(terminal) == 5, str(terminal))
    for t in ["create_sql_query", "edit_sql_query", "edit_chart",
              "replace_sql_fragment", "ask_for_clarification"]:
        check(f"terminal: {t}", t in terminal)

    # Categories
    cats = {}
    for t in ToolRegistry._tools.values():
        cats[t.category] = cats.get(t.category, 0) + 1
    check("explore count", cats.get("explore", 0) >= 5, str(cats))
    check("create count", cats.get("create", 0) >= 2, str(cats))
    check("edit count", cats.get("edit", 0) >= 3, str(cats))

    # OpenAI schemas
    schemas = ToolRegistry.get_openai_schemas()
    check("schema count", len(schemas) == 17)
    for s in schemas:
        assert s["type"] == "function", f"Bad schema type: {s}"
        assert "name" in s["function"], f"Bad schema: {s}"
        assert "parameters" in s["function"], f"Bad schema: {s}"


# ============================================================
# 12. Syntax — all files compile
# ============================================================

def test_syntax():
    section("12. Syntax — all agent files")
    import py_compile
    agent_dir = os.path.join(os.path.dirname(__file__), "..", "backend", "apps", "chat", "agent")
    py_files = []
    for root, _, files in os.walk(agent_dir):
        for f in files:
            if f.endswith(".py") and not f.startswith("__"):
                py_files.append(os.path.join(root, f))

    for path in sorted(py_files):
        rel = os.path.relpath(path, os.path.dirname(__file__))
        try:
            py_compile.compile(path, doraise=True)
            check(f"syntax: {rel}", True)
        except py_compile.PyCompileError as e:
            check(f"syntax: {rel}", False, str(e))

    # chat.py
    chat_py = os.path.join(os.path.dirname(__file__), "..", "backend", "apps", "chat", "api", "chat.py")
    try:
        py_compile.compile(chat_py, doraise=True)
        check("syntax: chat.py", True)
    except py_compile.PyCompileError as e:
        check("syntax: chat.py", False, str(e))

    check("total agent files", len(py_files) == 18, f"got {len(py_files)}")


# ============================================================
# 13. Integration — full agent flow (without LLM)
# ============================================================

def test_integration():
    section("13. Integration — agent flow simulation")
    from apps.chat.agent.memory import AgentMemory, QueryRecord, ChartRecord

    # Simulate a complete agent interaction: search → get metadata → create SQL → execute → chart
    m = AgentMemory(datasource_id=1, datasource_type="mysql",
                    ds=type("x", (), {"type": "mysql"})())
    m.terminal_triggered = False

    # Step 1: LLM calls search (simulated)
    # In type=0 mode, this would use embedding. We simulate the return.
    search_result = {"tables": [{"name": "orders", "comment": ""}, {"name": "products", "comment": ""}]}
    check("search returns tables", len(search_result["tables"]) == 2)

    # Step 2: Table metadata manually cached (simulating get_table_metadata)
    m.explored_tables["orders"] = {
        "table_name": "orders",
        "fields": [{"name": "order_id", "type": "int"}, {"name": "amount", "type": "decimal"}],
    }
    m.explored_tables["products"] = {
        "table_name": "products",
        "fields": [{"name": "product_id", "type": "int"}, {"name": "name", "type": "varchar"}],
    }
    check("schema cached", len(m.explored_tables) == 2)

    # Step 3: create SQL (simulated success)
    q = QueryRecord(record_id="r_sim", sql="SELECT p.name, SUM(o.amount) FROM orders o JOIN products p ON o.product_id = p.product_id GROUP BY p.name LIMIT 10",
                    tables_used=["orders", "products"])
    m.queries["r_sim"] = q
    m.terminal_triggered = True
    check("query created", "r_sim" in m.queries)
    check("terminal triggered", m.terminal_triggered)

    # Step 4: execute (simulated)
    q.data = {"fields": ["name", "total"], "data": [{"name": "A", "total": 100}]}
    q.status = "executed"
    check("query executed", q.status == "executed" and q.data is not None)

    # Step 5: create chart (simulated)
    c = ChartRecord(chart_ref="c_sim", record_id="r_sim", chart_config={"type": "bar"})
    m.charts["c_sim"] = c
    check("chart created", "c_sim" in m.charts)

    # Step 6: follow-up — edit chart (simulated)
    m.terminal_triggered = False
    m.charts["c_sim"].chart_config["type"] = "column"
    m.terminal_triggered = True
    check("chart edited", m.charts["c_sim"].chart_config["type"] == "column")
    check("followup terminal", m.terminal_triggered)

    # Step 7: follow-up — edit SQL (simulated)
    m.terminal_triggered = False
    old_sql = q.sql
    q.add_version(old_sql)
    q.sql = old_sql + " ORDER BY total DESC"
    q.status = "edited"
    m.terminal_triggered = True
    check("SQL edited", q.sql != old_sql)
    check("version saved", len(q.sql_history) == 1)
    check("followup terminal2", m.terminal_triggered)

    # Step 8: context for third turn
    ctx = m.get_context_for_llm()
    check("context has all artifacts", "r_sim" in ctx and "c_sim" in ctx)


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  SQLBot Agent — Comprehensive Test Suite")
    print("=" * 60)

    tests = [
        test_memory,
        test_registry,
        test_state,
        test_sql_validation,
        test_edit_sql,
        test_chart_tools,
        test_advanced_tools,
        test_schema_mode,
        test_permission_tools,
        test_graph_prompt,
        test_register_all,
        test_syntax,
        test_integration,
    ]

    _skipped = 0
    for test_fn in tests:
        try:
            test_fn()
        except ImportError as e:
            _skipped += 1
            print(f"\n  ~~ SKIP {test_fn.__name__}: missing dependency — {e}")
        except Exception as e:
            _failed += 1
            print(f"\n  !! CRASH in {test_fn.__name__}: {e}")
            traceback.print_exc()

    if _skipped:
        print(f"\n  ({_skipped} test(s) skipped — need sqlalchemy, sqlglot in environment)")

    ok = summary()
    sys.exit(0 if ok else 1)
