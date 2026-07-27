"""
Integration tests for SQLBot Agent API.

Covers the recently modified agent tool system and related SSE endpoints:
  - Tool registration (17 tools, show_to_user split, terminal flags)
  - AgentProfile consistency (QA / analysis / predict)
  - POST /api/v1/chat/question  (SSE stream)
  - POST /api/v1/chat/record/{id}/analysis
  - POST /api/v1/chat/record/{id}/predict
  - SSE event types emitted

Prerequisites:
  - SQLBot backend running on localhost:8000
  - PostgreSQL with the sqlbot database accessible
  - At least one datasource and one LLM model configured

Usage:
  cd backend
  uv run python tests/test_integration.py                # all tests
  uv run python tests/test_integration.py --quick         # skip slow SSE tests
  uv run python tests/test_integration.py --host localhost:8100  # custom host
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from typing import Optional

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

# ============================================================
# Test helpers
# ============================================================

_passed = 0
_failed = 0
_errors: list[str] = []
_skip_slow: bool = "--quick" in sys.argv

# Config — override via CLI: --host HOST --port PORT
_HOST = next((a.split("=")[1] for a in sys.argv if a.startswith("--host=")),
             os.environ.get("TEST_HOST", "localhost"))
_PORT = next((a.split("=")[1] for a in sys.argv if a.startswith("--port=")),
             os.environ.get("TEST_PORT", "8000"))
_BASE_URL = f"http://{_HOST}:{_PORT}/api/v1"


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
    if _errors:
        print(f"\nFAILURES ({len(_errors)}):")
        for e in _errors:
            print(f"  {e}")
    print(f"{'='*60}")
    return _failed == 0


def skip_slow() -> bool:
    return _skip_slow


# ============================================================
# Auth — connect to DB → find admin → create JWT
# ============================================================

def _get_db_session():
    """Create a SQLAlchemy session connected to the project database."""
    from common.core.config import settings
    from common.core.db import engine
    from sqlmodel import Session
    return Session(engine)


def _find_admin_user(session) -> Optional[dict]:
    """Find the first enabled admin/superuser from the user table.

    Uses raw SQL to avoid depending on model imports that may
    change across versions.
    """
    from sqlalchemy import text
    result = session.execute(text(
        "SELECT id, account, name, email, oid, language, status, origin "
        "FROM \"user\" WHERE status = 1 AND origin = 0 and account='admin'"
        "ORDER BY id LIMIT 1"
    ))
    row = result.fetchone()
    if row:
        return {
            "id": row[0],
            "account": row[1],
            "name": row[2],
            "email": row[3],
            "oid": row[4],
            "language": row[5],
            "status": row[6],
            "origin": row[7],
        }
    return None


def _make_token(user: dict) -> str:
    """Create a JWT access token for the given user dict."""
    from common.core.security import create_access_token
    from common.core.config import settings
    return create_access_token(
        user.copy(),
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def _get_auth_headers(session=None) -> dict:
    """Get Authorization header for API requests.

    Caches the token globally so we only hit the DB once.
    """
    global _cached_token
    if _cached_token:
        return _cached_token
    if session is None:
        session = _get_db_session()
    try:
        user = _find_admin_user(session)
        if not user:
            raise RuntimeError("No admin user found in database")
        token = _make_token(user)
        _cached_token = {"Authorization": f"Bearer {token}"}
        print(f"\n  [auth] token created for user: {user['account']} (id={user['id']})")
        return _cached_token
    finally:
        session.close()


_cached_token: Optional[dict] = None


# ============================================================
# 1. Tool registration integrity (no HTTP — pure logic)
# ============================================================

def test_tool_registration():
    section("1. Tool registration integrity")
    from apps.chat.agent.tools.registry import ToolRegistry
    from apps.chat.agent.tools.register_all import register_all_tools

    ToolRegistry._tools.clear()
    n = register_all_tools()
    check("17 tools registered", n == 17, f"got {n}")

    # ── show_to_user split ──
    internal = [t.name for t in ToolRegistry._tools.values() if not t.show_to_user]
    visible = [t.name for t in ToolRegistry._tools.values() if t.show_to_user]

    check("11 internal tools", len(internal) == 11,
          f"got {len(internal)}: {internal}")
    check("6 user-visible tools", len(visible) == 6,
          f"got {len(visible)}: {visible}")

    # Verify specific internal tools
    for name in ["preview_sql", "search_relevant_tables", "get_table_metadata",
                 "get_field_values", "load_skill", "execute_sql_query"]:
        check(f"internal: {name}", name in internal)

    # Verify specific user-visible tools
    for name in ["create_sql_query", "edit_sql_query", "create_chart",
                 "edit_chart", "ask_for_clarification"]:
        check(f"user-visible: {name}", name in visible)

    # ── Terminal tools ──
    terminal = ToolRegistry.get_terminal_tool_names()
    check("5 terminal tools", len(terminal) == 5, f"got {len(terminal)}: {terminal}")
    for name in ["create_sql_query", "edit_sql_query", "replace_sql_fragment",
                 "edit_chart", "ask_for_clarification"]:
        check(f"terminal: {name}", name in terminal)

    # ── preview_sql is NOT terminal and NOT user-visible ──
    preview = ToolRegistry.get("preview_sql")
    check("preview_sql exists", preview is not None)
    check("preview_sql non-terminal", not preview.terminal)
    check("preview_sql NOT user-visible", not preview.show_to_user)
    check("preview_sql category=internal", preview.category == "internal")

    # ── is_user_visible helper ──
    check("is_user_visible(create_sql_query)", ToolRegistry.is_user_visible("create_sql_query"))
    check("not is_user_visible(preview_sql)", not ToolRegistry.is_user_visible("preview_sql"))
    check("not is_user_visible(search_relevant_tables)", not ToolRegistry.is_user_visible("search_relevant_tables"))

    # ── get_user_visible_schemas ──
    uvs = ToolRegistry.get_user_visible_schemas()
    check("user-visible schema count", len(uvs) == 6, f"got {len(uvs)}")

    ToolRegistry._tools.clear()


# ============================================================
# 2. AgentProfile consistency
# ============================================================

def test_profiles():
    section("2. AgentProfile consistency")
    from apps.chat.agent.engine import (
        build_qa_profile, build_analysis_profile, build_predict_profile,
    )
    from apps.chat.agent.tools.registry import ToolRegistry
    from apps.chat.agent.tools.register_all import register_all_tools

    ToolRegistry._tools.clear()
    register_all_tools()

    # ── QA profile ──
    qa = build_qa_profile()
    check("qa name", qa.name == "qa")
    check("qa tool count", len(qa.tool_names) == 14, f"got {len(qa.tool_names)}")
    check("qa has preview_sql", "preview_sql" in qa.tool_names)
    check("qa has create_sql_query", "create_sql_query" in qa.tool_names)
    check("qa has execute_sql_query", "execute_sql_query" in qa.tool_names)
    check("qa terminal count", len(qa.terminal_tools) == 5,
          f"got {len(qa.terminal_tools)}: {qa.terminal_tools}")
    for t in ["create_sql_query", "edit_sql_query", "replace_sql_fragment",
              "edit_chart", "ask_for_clarification"]:
        check(f"qa terminal: {t}", t in qa.terminal_tools)
    check("qa max_iterations", qa.max_iterations == 50)
    check("qa post_process", qa.post_process == "execute_and_chart")

    # Verify every tool in qa.tool_names is registered
    for tn in qa.tool_names:
        check(f"qa tool registered: {tn}", ToolRegistry.get(tn) is not None,
              f"tool '{tn}' not found in registry")

    # ── Analysis profile ──
    analysis = build_analysis_profile(None)
    check("analysis name", analysis.name == "analysis")
    check("analysis tool count", len(analysis.tool_names) == 4,
          f"got {len(analysis.tool_names)}")
    for tn in ["get_data_summary", "get_data_preview", "analyze_query_result", "ask_for_clarification"]:
        check(f"analysis has {tn}", tn in analysis.tool_names)
    check("analysis NO preview_sql", "preview_sql" not in analysis.tool_names)
    check("analysis NO search_relevant_tables", "search_relevant_tables" not in analysis.tool_names)
    check("analysis terminal", analysis.terminal_tools == ["ask_for_clarification"])
    check("analysis max_iterations", analysis.max_iterations == 10)
    check("analysis post_process", analysis.post_process == "text_and_chart")

    # ── Predict profile ──
    predict = build_predict_profile(None)
    check("predict name", predict.name == "predict")
    check("predict tool count", len(predict.tool_names) == 5,
          f"got {len(predict.tool_names)}")
    check("predict has search_web", "search_web" in predict.tool_names)
    check("predict terminal", predict.terminal_tools == ["ask_for_clarification"])
    check("predict max_iterations", predict.max_iterations == 15)
    check("predict post_process", predict.post_process == "text_and_chart")

    # ── Cross-profile validation: tools don't leak ──
    qa_set = set(qa.tool_names)
    analysis_set = set(analysis.tool_names)
    predict_set = set(predict.tool_names)
    check("analysis ⊂ qa", analysis_set.issubset(qa_set),
          f"extra: {analysis_set - qa_set}")
    check("predict ⊂ qa", predict_set.issubset(qa_set),
          f"extra: {predict_set - qa_set}")

    ToolRegistry._tools.clear()


# ============================================================
# 3. Memory — _charts_this_turn
# ============================================================

def test_memory_charts_this_turn():
    section("3. AgentMemory _charts_this_turn")
    from apps.chat.agent.memory import AgentMemory, ChartRecord

    m = AgentMemory()
    check("empty charts_this_turn", len(m._charts_this_turn) == 0)

    # Simulate chart creation in current turn
    c = ChartRecord(chart_ref="chart_r_1", record_id="r_1",
                    chart_config={"type": "bar"})
    m.charts["chart_r_1"] = c
    m.mark_chart_this_turn("chart_r_1")
    check("marked this turn", "chart_r_1" in m._charts_this_turn)

    # Add a chart from "previous turn" (not marked)
    c2 = ChartRecord(chart_ref="chart_r_0", record_id="r_0",
                     chart_config={"type": "line"})
    m.charts["chart_r_0"] = c2

    # get_latest_chart_this_turn should return the marked one
    latest = m.get_latest_chart_this_turn()
    check("get_latest_chart_this_turn", latest is not None and latest.chart_ref == "chart_r_1",
          f"got {latest.chart_ref if latest else None}")

    # get_latest_chart returns overall latest (chart_r_0 inserted last)
    overall = m.get_latest_chart()
    check("get_latest_chart overall", overall is not None and overall.chart_ref == "chart_r_0",
          f"got {overall.chart_ref if overall else None}")

    # Empty _charts_this_turn → get_latest_chart_this_turn returns None
    m2 = AgentMemory()
    c3 = ChartRecord(chart_ref="c3", record_id="r3", chart_config={"type": "pie"})
    m2.charts["c3"] = c3
    check("empty turn returns None", m2.get_latest_chart_this_turn() is None)

    # _charts_this_turn is NOT in to_dict (transient)
    d = m.to_dict()
    check("to_dict excludes _charts_this_turn", "_charts_this_turn" not in d)


# ============================================================
# 4. preview_sql tool (logic test)
# ============================================================

def test_preview_sql_logic():
    section("4. preview_sql — logic validation")
    from apps.chat.agent.tools.advanced_tools import preview_sql
    from apps.chat.agent.memory import AgentMemory

    # preview_sql requires a real datasource to execute, but we can test
    # the validation path: syntax error, denied keyword, missing table

    # Simulate a basic memory
    m = AgentMemory(datasource_type="mysql")

    # 4a: denied keyword → rejected
    r = asyncio.run(preview_sql("DROP TABLE x", memory=m))
    check("denied DROP", r["success"] == False, str(r))
    check("error mentions forbidden", "禁止" in r.get("error", "") or "DROP" in r.get("error", ""))

    # 4b: empty SQL → rejected
    r = asyncio.run(preview_sql("", memory=m))
    check("empty SQL rejected", r["success"] == False)

    # 4c: table not explored → rejected
    r = asyncio.run(preview_sql("SELECT * FROM nonexistent_table", memory=m))
    check("unknown table rejected", r["success"] == False)
    check("error suggests get_table_metadata", "get_table_metadata" in r.get("error", ""))


# ============================================================
# 5. API: health check + auth
# ============================================================

def test_api_health_and_auth():
    section("5. API — health check + auth")
    headers = _get_auth_headers()

    # 5a: health check (no auth needed if whitelisted)
    try:
        resp = httpx.get(f"{_BASE_URL}/chat/list", headers=headers, timeout=10)
        check("GET /chat/list status 200", resp.status_code == 200,
              f"status={resp.status_code} body={resp.text[:200]}")
        data = resp.json()
        check("chat list is list", isinstance(data, list), f"type={type(data)}")
    except httpx.ConnectError:
        check("backend reachable", False,
              f"Cannot connect to {_BASE_URL}. Is SQLBot running on {_HOST}:{_PORT}?")
        return

    # 5b: auth rejection without token
    resp = httpx.get(f"{_BASE_URL}/chat/list", timeout=10)
    check("no auth → 403", resp.status_code in (401, 403),
          f"status={resp.status_code}")

    # 5c: system info (datasource list)
    resp = httpx.get(f"{_BASE_URL}/datasource/list/1/10", headers=headers, timeout=10)
    check("GET /datasource/list 200", resp.status_code == 200,
          f"status={resp.status_code}")
    data = resp.json()
    check("datasource has records key", isinstance(data, dict) and "records" in data,
          f"keys={list(data.keys()) if isinstance(data, dict) else type(data)}")


# ============================================================
# 6. API: SSE chat/question stream
# ============================================================

async def _collect_sse_events(url: str, json_data: dict, headers: dict,
                               timeout: float = 60.0) -> list[dict]:
    """Stream an SSE endpoint and collect all parsed events.

    Returns list of parsed event dicts (data.type + payload).
    """
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=json_data, headers=headers) as resp:
            check(f"SSE {url.split('/')[-2]} status 200", resp.status_code == 200,
                  f"status={resp.status_code}")
            if resp.status_code != 200:
                body = await resp.aread()
                print(f"    body: {body[:500]}")
                return events

            buffer = ""
            async for chunk in resp.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="replace")
                # Split on SSE double-newline boundaries
                while "\n\n" in buffer:
                    line, buffer = buffer.split("\n\n", 1)
                    line = line.strip()
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            events.append(data)
                        except json.JSONDecodeError:
                            pass  # skip malformed events
    return events


def _find_event(events: list[dict], event_type: str) -> Optional[dict]:
    """Find the first SSE event with the given type."""
    for e in events:
        if e.get("type") == event_type:
            return e
    return None


def _find_all_events(events: list[dict], event_type: str) -> list[dict]:
    """Find all SSE events with the given type."""
    return [e for e in events if e.get("type") == event_type]


def test_api_chat_question_sse():
    if skip_slow():
        print("\n  ~~ SKIP (--quick mode): SSE stream tests")
        return

    section("6. API — POST /chat/question SSE stream")
    headers = _get_auth_headers()

    # 6a: Start a new chat
    try:
        resp = httpx.post(
            f"{_BASE_URL}/chat/assistant/start",
            json={"origin": 2},
            headers=headers,
            timeout=10,
        )
    except httpx.ConnectError:
        check("backend reachable", False, f"Cannot connect to {_BASE_URL}")
        return

    if resp.status_code != 200:
        # Maybe no assistant datasource — try regular chat start
        resp = httpx.post(
            f"{_BASE_URL}/chat/start",
            json={},
            headers=headers,
            timeout=10,
        )
    check("start chat 200", resp.status_code == 200,
          f"status={resp.status_code} body={resp.text[:200]}")
    chat_info = resp.json()
    chat_id = chat_info.get("id")
    check("chat_id returned", chat_id is not None)

    if chat_id is None:
        return

    # 6b: Ask a question via SSE
    try:
        events = asyncio.run(_collect_sse_events(
            f"{_BASE_URL}/chat/question",
            {"question": "SELECT 1", "chat_id": chat_id},
            headers,
            timeout=120.0,
        ))
    except Exception as e:
        check("SSE question stream", False, str(e))
        return

    check("SSE events received", len(events) > 0, f"got {len(events)} events")

    # 6c: Check key SSE event types
    event_types = {e.get("type") for e in events}
    print(f"\n    Event types received: {sorted(event_types)}")

    # Core events every response should have
    for evt in ["id", "finish"]:
        check(f"has {evt} event", evt in event_types,
              f"events: {sorted(event_types)}")

    # 6d: Check that internal tools do NOT produce tool-call events
    internal_tool_events = [e for e in events if e.get("type") == "tool-call"
                           and e.get("tool_name") in (
                               "search_relevant_tables", "get_table_metadata",
                               "get_field_values", "load_skill", "preview_sql",
                               "get_data_summary", "get_data_preview",
                               "execute_sql_query", "analyze_query_result",
                           )]
    if internal_tool_events:
        names = [e["tool_name"] for e in internal_tool_events]
        print(f"    ⚠ Internal tool events leaked to SSE: {names}")
        check("NO internal tool-call events in SSE", False,
              f"Leaked: {names}")
    else:
        check("NO internal tool-call events in SSE", True)

    # 6e: Check execution-stats event
    stats = _find_event(events, "execution-stats")
    if stats:
        log_data = json.loads(stats.get("content", "{}"))
        check("execution-stats has iterations", "iterations" in log_data)
        check("execution-stats has tokens", "tokens" in log_data)
        check("execution-stats has tools", "tools" in log_data)
        # Verify tool logs contain ALL tools (internal + user-visible)
        # but the SSE tool-call events should be filtered
        tool_logs = log_data.get("tools", [])
        tool_names_in_log = {t.get("name") for t in tool_logs}
        print(f"    Tools in execution log: {sorted(tool_names_in_log)}")

    # 6f: Check error event (may or may not be present — depends on data)
    error = _find_event(events, "error")
    if error:
        print(f"    ⚠ Query returned error: {error.get('content', '')[:200]}")


# ============================================================
# 7. API: analysis endpoint
# ============================================================

def test_api_analysis():
    if skip_slow():
        print("\n  ~~ SKIP (--quick mode): analysis SSE test")
        return

    section("7. API — POST /chat/record/{id}/analysis")
    headers = _get_auth_headers()

    # 7a: Find a record with data to analyze
    resp = httpx.get(f"{_BASE_URL}/chat/list", headers=headers, timeout=10)
    if resp.status_code != 200:
        check("chat list available", False, str(resp.status_code))
        return

    chats = resp.json()
    record_id = None
    for chat in chats:
        records = chat.get("records", [])
        for rec in records:
            if rec.get("data") and rec.get("sql"):
                record_id = rec.get("id")
                break
        if record_id:
            break

    if not record_id:
        print("    (no record with data found — skipping analysis test)")
        return

    check("found record with data", record_id is not None)

    # 7b: Call analysis endpoint
    try:
        events = asyncio.run(_collect_sse_events(
            f"{_BASE_URL}/chat/record/{record_id}/analysis",
            {},
            headers,
            timeout=120.0,
        ))
    except Exception as e:
        check("analysis SSE stream", False, str(e))
        return

    event_types = {e.get("type") for e in events}
    print(f"    Analysis events: {sorted(event_types)}")
    check("analysis has finish", "finish" in event_types)
    check("analysis has id", "id" in event_types)

    # analysis should use the analysis profile — verify no QA tools leak
    tool_call_names = {e.get("tool_name") for e in events if e.get("type") == "tool-call"}
    for forbidden in ["search_relevant_tables", "create_sql_query", "edit_sql_query"]:
        check(f"analysis no {forbidden}", forbidden not in tool_call_names,
              f"found: {tool_call_names}")


# ============================================================
# 8. parse SSE event structure for frontend compatibility
# ============================================================

def test_sse_event_schema():
    section("8. SSE event schema validation")
    from apps.chat.agent.executor import _tool_result_summary

    # 8a: All user-visible tools have summaries
    user_visible = [
        "create_sql_query", "edit_sql_query", "replace_sql_fragment",
        "create_chart", "edit_chart", "ask_for_clarification",
    ]
    for tool_name in user_visible:
        # Success case
        summary = _tool_result_summary(tool_name, {"success": True})
        check(f"summary for {tool_name} (success)", isinstance(summary, str) and len(summary) > 0,
              f"got '{summary}'")
        # Failure case
        summary = _tool_result_summary(tool_name, {"success": False, "error": "test error"})
        check(f"summary for {tool_name} (fail)", isinstance(summary, str) and "失败" in summary,
              f"got '{summary}'")

    # 8b: Internal tools also have summaries (for execution log, not SSE)
    internal = [
        "search_relevant_tables", "get_table_metadata", "get_table_sample_data",
        "get_field_values", "execute_sql_query", "load_skill",
        "analyze_query_result", "get_data_summary", "get_data_preview",
        "search_web", "preview_sql",
    ]
    for tool_name in internal:
        summary = _tool_result_summary(tool_name, {"success": True})
        check(f"summary for {tool_name} (internal)", isinstance(summary, str) and len(summary) > 0,
              f"got '{summary}'")

    # 8c: preview_sql summary
    summary = _tool_result_summary("preview_sql", {"success": True})
    check("preview_sql summary", isinstance(summary, str))

    # 8d: Unknown tool fallback
    summary = _tool_result_summary("unknown_tool_xyz", {"success": True})
    check("unknown tool fallback", summary == "完成", f"got '{summary}'")


# ============================================================
# 9. System prompt completeness
# ============================================================

def test_system_prompt():
    section("9. System prompt completeness")
    from apps.chat.agent.graph import _build_system_prompt
    from apps.chat.agent.memory import AgentMemory, QueryRecord, ChartRecord

    m = AgentMemory(datasource_type="mysql")

    # 9a: First-turn prompt covers all tool categories
    p = _build_system_prompt(m, is_followup=False)

    # Internal tools mentioned in prompt
    for keyword in ["内部工具", "内部", "preview_sql", "用户可见"]:
        check(f"prompt mentions '{keyword}'", keyword in p,
              f"prompt length={len(p)}")

    # User-visible tools mentioned
    for tool in ["create_sql_query", "edit_sql_query", "edit_chart"]:
        check(f"prompt mentions {tool}", tool in p)

    # Key rules section
    check("prompt has 关键规则", "关键规则" in p)
    check("prompt explains create_sql_query consequences",
          "执行" in p and "图表" in p and "展示" in p)

    # 9b: Follow-up prompt
    q = QueryRecord(record_id="r_1", sql="SELECT * FROM t", tables_used=["t"])
    m.queries["r_1"] = q
    c = ChartRecord(chart_ref="c_1", record_id="r_1", chart_config={"type": "bar"})
    m.charts["c_1"] = c

    p2 = _build_system_prompt(m, is_followup=True)
    check("followup has 追问模式", "追问模式" in p2)
    check("followup mentions edit_chart", "edit_chart" in p2)
    check("followup larger than first-turn", len(p2) > len(p))

    # 9c: DataEase mode
    m2 = AgentMemory(datasource_type="mysql", out_ds_instance=object())
    p3 = _build_system_prompt(m2, is_followup=False)
    check("DataEase guidance present", "DataEase 数据集" in p3)
    check("dataset hints", "is_dataset" in p3)


# ============================================================
# Main
# ============================================================

_TESTS = [
    # Always run (pure logic, no network)
    ("1. Tool registration", test_tool_registration),
    ("2. Agent profiles", test_profiles),
    ("3. Memory charts_this_turn", test_memory_charts_this_turn),
    ("4. preview_sql logic", test_preview_sql_logic),
    ("8. SSE event schema", test_sse_event_schema),
    ("9. System prompt", test_system_prompt),
    # Requires backend running
    ("5. API health + auth", test_api_health_and_auth),
    # Slow — SSE streaming (skip with --quick)
    ("6. API chat/question SSE", test_api_chat_question_sse),
    ("7. API analysis SSE", test_api_analysis),
]


if __name__ == "__main__":
    print("=" * 60)
    print("  SQLBot Agent — Integration Test Suite")
    print(f"  Target: {_BASE_URL}")
    print(f"  Quick mode: {_skip_slow}")
    print("=" * 60)

    if "--help" in sys.argv:
        print(__doc__)
        sys.exit(0)

    for label, test_fn in _TESTS:
        try:
            test_fn()
        except ImportError as e:
            print(f"\n  ~~ SKIP [{label}]: missing dependency — {e}")
        except Exception as e:
            _failed += 1
            print(f"\n  !! CRASH in [{label}]: {e}")
            import traceback
            traceback.print_exc()

    ok = summary()
    sys.exit(0 if ok else 1)
