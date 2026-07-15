"""
AgentExecutor — wraps the LangGraph agent graph for the existing SQLBot API.

Produces SSE events in the same JSON format as the current pipeline
so the frontend requires zero changes.

Architecture:
  stream_agent (async gen, event loop)
    → asyncio.Queue()          ← async-safe channel
    → loop.run_in_executor()   ← runs sync LLM invoke in thread
    → await queue.get()        ← no polling, no CPU burn
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from typing import Optional

import orjson
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

from apps.chat.agent.memory import AgentMemory
from apps.chat.agent.graph import build_agent_graph
from apps.chat.agent.tools.registry import ToolRegistry
from common.utils.utils import SQLBotLogUtil as _log


def _update_chat_brief(session, record) -> None:
    """Update Chat.brief from the first user question in the chat.

    Only sets brief if the current brief looks like an auto-generated
    datetime string (the default when a Chat is created without a question).
    """
    try:
        from apps.chat.models.chat_model import Chat, ChatRecord
        from sqlalchemy import select, and_, update, func

        chat_id = record.chat_id
        if not chat_id:
            return

        chat = session.get(Chat, chat_id)
        if not chat or not chat.brief:
            return

        # Only replace datetime-format briefs (e.g. "2026-07-14 15:14:02")
        if not re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', chat.brief):
            return

        stmt = (
            select(ChatRecord.question)
            .where(and_(ChatRecord.chat_id == chat_id, ChatRecord.first_chat == False))
            .order_by(ChatRecord.create_time.asc())
            .limit(1)
        )
        result = session.execute(stmt)
        row = result.fetchone()
        if row and row.question:
            brief = row.question.strip()[:20]
            chat.brief = brief
            session.add(chat)
            session.commit()
            _log.info(f"[Agent] updated chat brief: {brief}")
    except Exception as exc:
        _log.info(f"[Agent] update brief skipped: {exc}")


def _tool_result_summary(tool_name: str, result: dict) -> str:
    """Build a one-line summary of a tool result for the frontend.

    Avoids sending the full result payload (which may be large)
    to the browser SSE stream.
    """
    if not isinstance(result, dict):
        return "done"

    success = result.get("success")
    if success is True:
        if tool_name == "search_relevant_tables":
            count = len(result.get("tables", []))
            return f"找到 {count} 张候选表" if count else "未找到匹配表"
        elif tool_name == "get_table_metadata":
            name = result.get("table_name", "")
            count = len(result.get("fields", []))
            cached = "(缓存)" if result.get("cached") else ""
            return f"已获取 {name} 结构 ({count} 字段) {cached}"
        elif tool_name == "get_field_values":
            field = result.get("field", "")
            count = result.get("distinct_count", 0)
            return f"{field} 有 {count} 个去重值"
        elif tool_name == "get_table_sample_data":
            name = result.get("table_name", "")
            rows = len(result.get("sample_data", []))
            return f"{name} 返回 {rows} 行样本"
        elif tool_name == "create_sql_query":
            rid = result.get("record_id", "?")
            return f"SQL 查询已创建 ({rid})"
        elif tool_name == "create_chart":
            return "图表已创建"
        elif tool_name == "edit_sql_query":
            return "SQL 查询已更新"
        elif tool_name == "edit_chart":
            return "图表配置已更新"
        elif tool_name == "execute_sql_query":
            rows = result.get("row_count", 0)
            return f"查询执行成功 ({rows} 行)"
        elif tool_name == "load_skill":
            return "技能已加载"
        elif tool_name == "analyze_query_result":
            return "数据分析完成"
        elif tool_name == "replace_sql_fragment":
            return "SQL 片段已替换"
        elif tool_name == "ask_for_clarification":
            return f"需要澄清: {result.get('clarification', '?')[:100]}"
        else:
            return "完成"
    elif success is False:
        err = str(result.get("error", "unknown"))[:80]
        return f"失败: {err}"
    else:
        return "完成"


class AgentExecutor:
    """Executes the SQLBot Agent loop.

    Chunks (SSE events) are pushed to an asyncio.Queue provided by the
    caller.  The caller drains the queue from the asyncio event loop while
    the agent runs inside loop.run_in_executor() in a background thread.
    """

    def __init__(self, llm, memory: AgentMemory, queue: asyncio.Queue):
        self.llm = llm
        self.memory = memory
        self.graph = build_agent_graph(llm, memory)
        self._queue: asyncio.Queue = queue
        self.iter_count: int = 0

    # ── entry point (called from a thread) ──────────────────

    def run(self) -> None:
        """Run the agent in a background thread.

        Creates its own event loop (needed for async tools) and pushes
        SSE chunks to the asyncio.Queue.  A None sentinel is pushed
        when the agent finishes (success or error).
        """
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_agent())
        except Exception:
            self._emit("error", content=traceback.format_exc(limit=2))
        finally:
            self._queue.put_nowait(None)  # sentinel: agent done
            try:
                loop.close()
            except Exception:
                pass

    # ── SSE helpers ────────────────────────────────────────

    def _emit(self, event_type: str, **kwargs) -> None:
        """Push an SSE event to the queue (thread-safe)."""
        payload = {"type": event_type, **kwargs}
        self._queue.put_nowait(
            "data:" + orjson.dumps(payload).decode() + "\n\n"
        )

    # ── agent loop (runs in thread's event loop) ───────────

    async def _run_agent(self) -> None:
        """Execute the LangGraph agent and convert events to SSE.

        Structure:
          1. Run agent graph (streaming)
          2. Post-process (execute query + generate chart)
          3. FINALLY: finalize record + update brief + generate summary

        Steps 2-3 are always executed regardless of whether the agent
        produced a query, hit an error, or terminated early.
        """
        user_question = getattr(self.memory, "user_question", "")
        chat_id = getattr(self.memory, "chat_id", None)
        import uuid
        thread_id = f"chat_{chat_id}_q_{uuid.uuid4().hex[:8]}" if chat_id else f"agent_{id(self)}"

        _log.info(f"[Agent] START question='{user_question[:80]}' thread={thread_id}")

        initial_state = {
            "messages": [HumanMessage(content=user_question)],
            "iteration": 0,
        }

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 50,
        }
        self.iter_count = 0

        try:
            async for event in self.graph.astream(initial_state, config):
                for _node_name, node_output in event.items():
                    messages = node_output.get("messages", [])

                    for msg in messages:
                        if isinstance(msg, AIMessage):
                            self.iter_count += 1
                            content = msg.content
                            if isinstance(content, str) and content.strip():
                                self._emit("text-delta", content=content)

                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tc_name = tc.get("name", "")
                                    tc_args = tc.get("args", {})
                                    _log.info(f"[Agent] iter={self.iter_count} tool_call={tc_name}")
                                    self._emit("tool-call", tool_name=tc_name, args=tc_args)
                                    # Emit clarification question as visible text
                                    if tc_name == "ask_for_clarification":
                                        self._emit("clarify", content=tc_args.get("question", ""))

                        elif isinstance(msg, ToolMessage):
                            try:
                                result = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                            except json.JSONDecodeError:
                                result = {"raw": str(msg.content)}
                            success = result.get("success", "?")
                            _log.info(f"[Agent] tool_result name={msg.name} success={success}")
                            self._emit("tool-result",
                                       tool_name=msg.name or "",
                                       success=success,
                                       summary=_tool_result_summary(msg.name, result))

            await self._post_process()

        finally:
            self._emit("finish")
            self._finalize_record_and_chat()
            self._finalize_turn()

    # ── post-processing ─────────────────────────────────────

    async def _post_process(self) -> None:
        """Execute query / emit chart based on what the agent produced."""
        latest = self.memory.get_latest_query()
        if latest and latest.status in ("created", "edited"):
            has_compiled = bool(getattr(latest, "compiled_sql", ""))
            _log.info(f"[Agent] EXECUTE record_id={latest.record_id} "
                      f"sql={latest.sql[:100]}... compiled={'Y' if has_compiled else 'N'}")
            await self._execute_and_chart(latest)
            return

        # Chart-only edit
        if self.memory.terminal_triggered and self.memory.charts:
            latest_chart = self.memory.get_latest_chart()
            if latest_chart:
                query = self.memory.queries.get(latest_chart.record_id)
                if query and query.status == "executed":
                    _log.info(f"[Agent] chart-only edit: {latest_chart.chart_ref}")
                    await self._emit_chart_from_memory(query, latest_chart)
                    self._emit("sql-data", content="execute-success")
                    return

        _log.info(f"[Agent] END no query to execute "
                  f"(terminal_triggered={self.memory.terminal_triggered}, "
                  f"queries={len(self.memory.queries)})")

    def _finalize_record_and_chat(self) -> None:
        """Mark ChatRecord as finished + update Chat.brief."""
        record = getattr(self.memory, "record", None)
        session = self.memory.session
        if not record or not session:
            return

        try:
            from apps.chat.curd.chat import finish_record
            finish_record(session=session, record_id=record.id)
        except Exception as exc:
            _log.info(f"finish_record skipped: {exc}")

        try:
            _update_chat_brief(session, record)
        except Exception as exc:
            _log.info(f"update_brief skipped: {exc}")

    def _finalize_turn(self) -> None:
        """Generate a compact turn summary for cross-turn context."""
        if not self.memory.queries:
            return

        latest = self.memory.get_latest_query()
        if not latest:
            return

        turn_num = len(self.memory.queries)
        question = (self.memory.user_question or "")[:100]

        parts = [f"Q{turn_num}: {question}"]

        if latest.tables_used:
            parts.append(f"涉及表: {', '.join(latest.tables_used)}")

        if latest.row_count > 0:
            parts.append(f"返回 {latest.row_count} 行")
        elif latest.data:
            parts.append("有返回数据")

        if latest.status == "executed":
            parts.append("查询已执行")

        if self.memory.charts:
            latest_chart = self.memory.get_latest_chart()
            if latest_chart and latest_chart.chart_config:
                ctype = latest_chart.chart_config.get("type", "?")
                parts.append(f"生成{ctype}图表")

        new_summary = " | ".join(parts)

        if self.memory.conversation_summary:
            self.memory.conversation_summary += f"\n{new_summary}"
        else:
            self.memory.conversation_summary = new_summary

        _log.info(f"[Agent] turn summary: {new_summary}")

    # ── chart generation ────────────────────────────────────

    async def _emit_chart_from_memory(self, query, chart_record) -> None:
        """Save + emit an already-updated chart config (no LLM call)."""
        import orjson as _orjson
        from apps.chat.curd.chat import save_chart_answer, save_chart, save_sql, save_sql_exec_data

        record = getattr(self.memory, "record", None)
        session = self.memory.session

        chart_str = _orjson.dumps(chart_record.chart_config).decode()
        self._emit("chart", content=chart_str)

        if record and session:
            try:
                save_sql(session=session, record_id=record.id, sql=query.sql)
                if not query.data:
                    from apps.chat.models.chat_model import ChatRecord as CR
                    from sqlalchemy import select, and_
                    row = session.execute(
                        select(CR.data).where(
                            and_(CR.chat_id == record.chat_id, CR.data.isnot(None))
                        ).order_by(CR.id.desc()).limit(1)
                    ).fetchone()
                    if row and row.data:
                        query.data = _orjson.loads(row.data)
                if query.data:
                    save_sql_exec_data(session=session, record_id=record.id,
                                      data=_orjson.dumps(query.data).decode())
                save_chart_answer(session=session, record_id=record.id,
                                  answer=_orjson.dumps({"content": "chart updated"}).decode())
                save_chart(session=session, record_id=record.id, chart=chart_str)
            except Exception as exc:
                _log.info(f"save chart skipped: {exc}")

        _log.info(f"[Agent] FINISH (chart-only) iterations={self.iter_count}")

    async def _generate_chart(self, query) -> None:
        """Generate chart config via LLM and save to ChatRecord."""
        from apps.template.generate_chart.generator import get_chart_template
        from apps.chat.curd.chat import save_chart_answer, save_chart, get_chart_config
        import orjson as _orjson

        record = getattr(self.memory, "record", None)
        session = self.memory.session

        if not query.data:
            _log.info("[Agent] no data for chart generation")
            return

        tpl = get_chart_template()
        system_msg = tpl["system"].format(lang="zh-CN", sqlbot_name="SQLBot")
        rules_msg = tpl["generate_rules"].format(lang="zh-CN")

        chart_type = ""
        question = getattr(self.memory, "user_question", "").lower()
        if any(w in question for w in ["柱状图", "柱状", "bar", "column"]):
            chart_type = "column"
        elif any(w in question for w in ["折线", "趋势", "line", "折线图"]):
            chart_type = "line"
        elif any(w in question for w in ["饼图", "占比", "pie", "饼"]):
            chart_type = "pie"

        user_msg = tpl["user"].format(
            lang="zh-CN", sql=query.sql, question=question,
            chart_type=chart_type, schema="",
        )

        from langchain_core.messages import SystemMessage as LCSystem, HumanMessage as LCHuman

        chart_messages = [
            LCSystem(content=system_msg),
            LCHuman(content=rules_msg),
            LCHuman(content=user_msg),
        ]

        full_text = ""
        try:
            for chunk in self.llm.stream(chart_messages):
                content = chunk.content if hasattr(chunk, "content") else ""
                reasoning = getattr(chunk, "additional_kwargs", {}).get("reasoning_content", "")
                if content:
                    full_text += content
                self._emit("chart-result", content=content, reasoning_content=reasoning)

            import json as _json
            from common.utils.utils import extract_nested_json
            chart_json = extract_nested_json(full_text)
            chart = _json.loads(chart_json) if chart_json else {"type": "table"}
            chart_str = _orjson.dumps(chart).decode()
            self._emit("chart", content=chart_str)

            if record and session:
                try:
                    save_chart_answer(session=session, record_id=record.id,
                                      answer=_orjson.dumps({"content": full_text}).decode())
                    save_chart(session=session, record_id=record.id, chart=chart_str)
                except Exception as exc:
                    _log.info(f"save chart skipped: {exc}")

            chart_ref = f"chart_{query.record_id}"
            from apps.chat.agent.memory import ChartRecord
            self.memory.charts[chart_ref] = ChartRecord(
                chart_ref=chart_ref,
                record_id=query.record_id,
                chart_config=chart,
            )
            _log.info(f"[Agent] chart saved to memory: {chart_ref}")
        except Exception as exc:
            _log.info(f"[Agent] chart generation failed: {exc}")
            self._emit("chart", content=_orjson.dumps({"type": "table"}).decode())

        _log.info(f"[Agent] FINISH iterations={self.iter_count}")

    async def _execute_and_chart(self, query) -> None:
        """Execute a query (with permission filtering), save to DB, generate chart."""
        from apps.chat.agent.tools.query_tools import execute_sql_query
        from apps.chat.agent.tools.permission_tools import apply_permissions
        from apps.chat.curd.chat import save_sql, save_sql_exec_data

        record = getattr(self.memory, "record", None)
        session = self.memory.session

        # Check if already executed (avoids dual execution)
        already_executed = query.status == "executed" and query.data
        if already_executed:
            _log.info(f"[Agent] query {query.record_id} already executed, reusing cached data")
            import sqlparse
            format_sql = sqlparse.format(query.sql, reindent=True)
            self._emit("sql", content=format_sql)

            if record and session:
                try:
                    save_sql(session=session, record_id=record.id, sql=query.sql)
                    if query.data:
                        import orjson as _orjson
                        save_sql_exec_data(session=session, record_id=record.id,
                                          data=_orjson.dumps(query.data).decode())
                except Exception as exc:
                    _log.info(f"save skipped: {exc}")

            self._emit("sql-data", content="execute-success")
            await self._generate_chart(query)
            return

        # Normal execution path
        try:
            filtered_sql = await apply_permissions(
                sql=query.sql,
                tables_used=query.tables_used,
                memory=self.memory,
                llm=self.llm,
            )
            if filtered_sql != query.sql:
                query.sql = filtered_sql
                self._emit("info", msg="permissions applied")
        except Exception as exc:
            _log.info(f"Permission filter skipped: {exc}")

        import sqlparse
        format_sql = sqlparse.format(query.sql, reindent=True)
        self._emit("sql", content=format_sql)

        if record and session:
            try:
                save_sql(session=session, record_id=record.id, sql=query.sql)
            except Exception as exc:
                _log.info(f"save SQL skipped: {exc}")

        exec_result = await execute_sql_query(record_id=query.record_id, memory=self.memory)
        if exec_result.get("success"):
            _log.info(f"[Agent] executed query {query.record_id}: {exec_result.get('row_count', 0)} rows")

            if record and session and query.data:
                import orjson as _orjson
                try:
                    save_sql_exec_data(session=session, record_id=record.id,
                                       data=_orjson.dumps(query.data).decode())
                except Exception as exc:
                    _log.info(f"save data skipped: {exc}")

            self._emit("sql-data", content="execute-success")
        else:
            self._emit("error", content=exec_result.get("error", "SQL execution failed"))
            return

        await self._generate_chart(query)
