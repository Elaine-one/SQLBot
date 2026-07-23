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
import time
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
        elif tool_name == "get_data_summary":
            count = result.get("field_count", 0)
            rows = result.get("row_count", 0)
            return f"数据摘要: {count} 字段, {rows} 行"
        elif tool_name == "get_data_preview":
            returned = result.get("returned", 0)
            total = result.get("total", 0)
            return f"数据预览: {returned}/{total} 行"
        elif tool_name == "search_web":
            count = result.get("count", 0)
            cached = "(缓存)" if result.get("cached") else ""
            return f"搜索完成: {count} 条结果 {cached}"
        elif tool_name == "preview_sql":
            rows = result.get("row_count", 0)
            returned = result.get("returned", 0)
            truncated = "(截断)" if result.get("truncated") else ""
            return f"内部查询: {returned}/{rows} 行 {truncated}"
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

    def __init__(self, llm, memory: AgentMemory, queue: asyncio.Queue,
                 profile=None):
        """profile: AgentProfile | None.  None defaults to QA."""
        self.llm = llm
        self.memory = memory

        # Default to QA profile if none provided
        if profile is None:
            from apps.chat.agent.engine import build_qa_profile
            profile = build_qa_profile()
            _log.info(f"[Agent:executor] init QA profile (default)")
        else:
            _log.info(f"[Agent:executor] init profile={profile.name} "
                      f"tools={profile.tool_names or 'ALL'} "
                      f"post_process={profile.post_process}")
        self.profile = profile

        # Inject LLM into memory so tools can access it
        if not hasattr(memory, '_llm') or getattr(memory, '_llm', None) is None:
            memory._llm = llm

        self.graph = build_agent_graph(llm, memory, profile=profile)
        self._queue: asyncio.Queue = queue
        self.iter_count: int = 0
        # Accumulate text output for save (analysis/predict)
        self._text_output: str = ""
        # Accumulate reasoning_content (DeepSeek native think) for persistence
        self._reasoning_output: str = ""
        # Execution tracking
        self._exec_start_time: float = 0.0
        self._tool_logs: list[dict] = []
        self._token_usage: dict[str, int] = {"prompt": 0, "completion": 0}
        self._tool_t0: float = 0.0

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
            self._emit("error", content=traceback.format_exc())
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

        # 统一开启 thinking mode（OpenAI 格式，所有模型通用）。
        # content 为空时由 _build_turn_summary_text 生成摘要补齐回复区。
        object.__setattr__(self.llm, "enable_thinking", True)
        _log.info(f"[Agent] enable_thinking=True profile={self.profile.name}")

        initial_state = {
            "messages": [HumanMessage(content=user_question)],
            "iteration": 0,
        }

        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 50,
        }
        self.iter_count = 0
        self._text_output = ""
        self._reasoning_output = ""
        self._exec_start_time = time.monotonic()
        self._tool_logs = []
        self._token_usage = {"prompt": 0, "completion": 0}
        self._tool_t0 = 0.0
        self._pending_tool_args = {}

        try:
            async for event in self.graph.astream(initial_state, config):
                for _node_name, node_output in event.items():
                    messages = node_output.get("messages", [])

                    for msg in messages:
                        if isinstance(msg, AIMessage):
                            self.iter_count += 1
                            content = msg.content

                            # Capture token usage from LLM response metadata
                            self._capture_token_usage(msg)

                            reasoning = ""
                            if hasattr(msg, "additional_kwargs"):
                                ak = msg.additional_kwargs
                                if isinstance(ak, dict):
                                    reasoning = ak.get("reasoning_content", "") or ""

                            # Emit native reasoning (DeepSeek-R1 etc.)
                            # Gate on expand_thinking_block: when False, suppress reasoning SSE
                            _emit_reasoning = getattr(self.memory, "expand_thinking_block", True)
                            if reasoning and reasoning.strip():
                                if _emit_reasoning:
                                    self._emit("reasoning", content=reasoning)
                                self._reasoning_output += reasoning

                            # Split content by intent:
                            # - has tool_calls → exploration phase → thinking panel
                            # - no tool_calls  → final answer     → reply area
                            has_tools = bool(msg.tool_calls)
                            if isinstance(content, str) and content.strip():
                                if has_tools:
                                    # Exploration narrative — goes to thinking panel
                                    if _emit_reasoning:
                                        self._emit("reasoning", content=content)
                                    self._reasoning_output += content
                                else:
                                    # Final answer — goes to reply area
                                    self._emit("text-delta", content=content)
                                    self._text_output += content

                            if msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tc_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                                    tc_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                                    _log.info(f"[Agent] iter={self.iter_count} tool_call={tc_name}")
                                    self._tool_t0 = time.monotonic()
                                    # Stash args keyed by tool name so ToolMessage handler can pair them
                                    self._pending_tool_args = tc_args
                                    if tc_name == "ask_for_clarification":
                                        self._emit("clarify", content=tc_args.get("question", ""))
                                    # Only push tool-call to frontend for user-visible tools
                                    if ToolRegistry.is_user_visible(tc_name):
                                        self._emit("tool-call", tool_name=tc_name, args=tc_args)

                        elif isinstance(msg, ToolMessage):
                            elapsed_ms = round((time.monotonic() - self._tool_t0) * 1000) if self._tool_t0 else 0
                            try:
                                result = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                            except json.JSONDecodeError:
                                result = {"raw": str(msg.content)}
                            # ToolMessage content can be a list (JSON array) from some tools;
                            # normalize to dict so .get() calls below don't crash
                            if not isinstance(result, dict):
                                result = {"value": result}
                            success = result.get("success", "?")
                            summary = _tool_result_summary(msg.name, result)
                            self._tool_logs.append({
                                "name": msg.name or "",
                                "args": getattr(self, '_pending_tool_args', {}),
                                "result": result,
                                "elapsed_ms": elapsed_ms,
                                "summary": summary,
                            })
                            _log.info(f"[Agent] tool_result name={msg.name} success={success}")
                            # Only push tool-result to frontend for user-visible tools
                            if ToolRegistry.is_user_visible(msg.name):
                                self._emit("tool-result",
                                           tool_name=msg.name or "",
                                           success=success,
                                           summary=summary)

            await self._post_process()

            # thinking mode 开启后 content 可能为空（模型输出转为 tool_call），
            # 用查询执行摘要作为 text-delta 确保回复区始终有内容。
            if not self._text_output.strip():
                summary = self._build_turn_summary_text()
                if summary:
                    self._emit("text-delta", content=summary)
                    self._text_output = summary

        finally:
            self._emit("execution-stats",
                       content=json.dumps(self._build_execution_log()))
            self._emit("finish")
            self._finalize_record_and_chat()
            self._finalize_turn()

    # ── post-processing ─────────────────────────────────────

    async def _post_process(self) -> None:
        """Execute query / emit chart based on what the agent produced.

        Branches on profile.post_process:
          - "text_and_chart" → save text + emit chart if created (analysis/predict)
          - "execute_and_chart" → existing QA logic (default)
        """
        # ── Analysis / Predict: save text + generate chart like QA ──
        if self.profile.post_process == "text_and_chart":
            self._save_text_answer()
            # Find the pre-injected base_record query (r_base_*), not a
            # cross-turn restored QA query that may lack data
            query = None
            for q in self.memory.queries.values():
                if q.data and q.status == "executed":
                    query = q
                    break
            if not query:
                query = list(self.memory.queries.values())[0] if self.memory.queries else None
            if query and query.data:
                await self._generate_chart(query)
                # Force-distinguish from QA chart by updating title + saving
                _label = "数据分析" if self.profile.name == "analysis" else "数据预测"
                self._update_chart_title(self.memory, _label)
                _log.info(f"[Agent] END ({self.profile.name}) "
                          f"text+chart iterations={self.iter_count}")
            else:
                _log.info(f"[Agent] END ({self.profile.name}) text-only "
                          f"(no data for chart) iterations={self.iter_count}")
            return

        # ── QA: existing logic ────────────────────────────
        latest = self.memory.get_latest_query()
        if latest and latest.status in ("created", "edited"):
            has_compiled = bool(getattr(latest, "compiled_sql", ""))
            _log.info(f"[Agent] EXECUTE record_id={latest.record_id} "
                      f"sql={latest.sql[:100]}... compiled={'Y' if has_compiled else 'N'}")
            await self._execute_and_chart(latest)
            return

        # Chart-only path — emit charts created/edited in the CURRENT turn.
        # This covers: edit_chart (terminal), create_chart on existing query (non-terminal),
        # and chart-only edits.  Charts from previous turns are NOT re-emitted.
        if self.memory._charts_this_turn:
            latest_chart = self.memory.get_latest_chart_this_turn()
            if latest_chart:
                query = self.memory.queries.get(latest_chart.record_id)
                if query and query.status == "executed":
                    _log.info(f"[Agent] chart-only emit (this turn): {latest_chart.chart_ref}")
                    await self._emit_chart_from_memory(query, latest_chart)
                    self._emit("sql-data", content="execute-success")
                    return

        _log.info(f"[Agent] END no query to execute "
                  f"(terminal_triggered={self.memory.terminal_triggered}, "
                  f"queries={len(self.memory.queries)})")

    # ── analysis / predict persistence ──────────────────────

    def _save_text_answer(self) -> None:
        """Save the accumulated text output to the appropriate ChatRecord column.

        Analysis → ChatRecord.analysis, Predict → ChatRecord.predict.
        Reuses the existing curd functions to stay compatible with
        how old pipeline records are stored.
        """
        record = getattr(self.memory, "record", None)
        session = self.memory.session
        if not record or not session:
            _log.info(f"[Agent:{self.profile.name}] save skipped: no record/session")
            return

        text = (self._text_output or "").strip()
        reasoning_text = (self._reasoning_output or "").strip()
        if not text and not reasoning_text:
            _log.info(f"[Agent:{self.profile.name}] save skipped: empty output "
                      f"(iterations={self.iter_count})")
            return

        _log.info(f"[Agent:{self.profile.name}] saving answer "
                  f"record_id={record.id} text_len={len(text)} "
                  f"reasoning_len={len(reasoning_text)} "
                  f"iterations={self.iter_count}")

        answer_json = orjson.dumps({
            "content": text,
            "reasoning_content": reasoning_text,
        }).decode()

        try:
            if self.profile.name == "analysis":
                from apps.chat.curd.chat import save_analysis_answer
                save_analysis_answer(session=session, record_id=record.id,
                                     answer=answer_json)
                _log.info(f"[Agent:analysis] saved to ChatRecord.analysis "
                          f"record_id={record.id}")
            elif self.profile.name == "predict":
                from apps.chat.curd.chat import save_predict_answer
                save_predict_answer(session=session, record_id=record.id,
                                    answer=answer_json)
                _log.info(f"[Agent:predict] saved to ChatRecord.predict "
                          f"record_id={record.id}")
        except Exception as exc:
            _log.info(f"[Agent:{self.profile.name}] save failed: {exc}")

    # ── execution log ──────────────────────────────────────

    def _capture_token_usage(self, msg) -> None:
        """Extract token usage from LangChain AIMessage.

        Uses usage_metadata (same as old pipeline's process_stream →
        chunk.usage_metadata).  Falls back to response_metadata for
        providers that don't populate usage_metadata.
        """
        usage = {}

        # Primary: usage_metadata (LangChain standard, same as old pipeline)
        umeta = getattr(msg, "usage_metadata", None)
        if umeta:
            usage = {
                "prompt_tokens": umeta.get("input_tokens", 0) if isinstance(umeta, dict) else 0,
                "completion_tokens": umeta.get("output_tokens", 0) if isinstance(umeta, dict) else 0,
            }

        # OpenAI / DeepSeek via response_metadata
        if not usage:
            meta = getattr(msg, "response_metadata", {}) or {}
            usage = meta.get("token_usage", None) or meta.get("usage", None) or {}
            if not usage and "completion_tokens" in meta:
                usage = {
                    "prompt_tokens": meta.get("prompt_tokens", 0),
                    "completion_tokens": meta.get("completion_tokens", 0),
                }

        if usage:
            added_prompt = usage.get("prompt_tokens", 0) or 0
            added_completion = usage.get("completion_tokens", 0) or 0
            if added_prompt or added_completion:
                self._token_usage["prompt"] += added_prompt
                self._token_usage["completion"] += added_completion
        else:
            # Diagnostic: log what *is* available on first call
            if not hasattr(self, '_token_diag_done'):
                self._token_diag_done = True
                umeta = getattr(msg, "usage_metadata", None)
                rmeta = getattr(msg, "response_metadata", None)
                _log.info(f"[Agent] token_usage MISS — "
                          f"usage_metadata={type(umeta).__name__}:{umeta!r} "
                          f"response_metadata_keys={list(rmeta.keys()) if isinstance(rmeta, dict) else type(rmeta).__name__} "
                          f"msg_type={type(msg).__name__}")

    def _build_execution_log(self) -> dict:
        """Build the execution_log dict from accumulated tracking data."""
        duration_ms = round((time.monotonic() - self._exec_start_time) * 1000) if self._exec_start_time else 0
        return {
            "iterations": self.iter_count,
            "duration_ms": duration_ms,
            "tokens": {
                "prompt": self._token_usage.get("prompt", 0),
                "completion": self._token_usage.get("completion", 0),
                "total": self._token_usage.get("prompt", 0) + self._token_usage.get("completion", 0),
            },
            "tools": self._tool_logs,
            "has_chart": len(self.memory.charts) > 0 if self.memory.charts else False,
        }

    def _save_execution_log(self) -> None:
        """Write execution_log to ChatRecord."""
        record = getattr(self.memory, "record", None)
        session = self.memory.session
        if not record or not session:
            return
        log = self._build_execution_log()
        try:
            from apps.chat.models.chat_model import ChatRecord as CR
            from sqlalchemy import update
            stmt = update(CR).where(CR.id == record.id).values(execution_log=log)
            session.execute(stmt)
            session.commit()
            _log.info(f"[Agent:{self.profile.name}] execution_log saved "
                      f"iterations={log['iterations']} "
                      f"duration={log['duration_ms']}ms "
                      f"tokens={log['tokens']['total']} "
                      f"tools={len(log['tools'])}")
        except Exception as exc:
            _log.info(f"[Agent:{self.profile.name}] save execution_log failed: {exc}")

    # ── record finalization ─────────────────────────────────

    def _finalize_record_and_chat(self) -> None:
        """Mark ChatRecord as finished + update Chat.brief + save execution log + sql_answer.

        sql_answer is only saved for the QA profile.  Analysis / Predict
        profiles write to their own dedicated columns (ChatRecord.analysis,
        ChatRecord.predict) via _save_text_answer().
        """
        record = getattr(self.memory, "record", None)
        session = self.memory.session
        if not record or not session:
            return

        self._save_execution_log()

        # ── sql_answer: QA only ──────────────────────────────
        if self.profile.name == "qa":
            text = (self._text_output or "").strip()
            reasoning_text = (self._reasoning_output or "").strip()
            if text or reasoning_text:
                try:
                    from apps.chat.curd.chat import save_sql_answer
                    answer_json = orjson.dumps({
                        "content": text,
                        "reasoning_content": reasoning_text,
                    }).decode()
                    save_sql_answer(session=session, record_id=record.id, answer=answer_json)
                    _log.info(f"[Agent:qa] sql_answer saved "
                              f"record_id={record.id} content_len={len(text)} "
                              f"reasoning_len={len(reasoning_text)}")
                except Exception as exc:
                    _log.info(f"[Agent:qa] save sql_answer failed: {exc}")

        try:
            from apps.chat.curd.chat import finish_record
            finish_record(session=session, record_id=record.id)
        except Exception as exc:
            _log.info(f"finish_record skipped: {exc}")

        try:
            _update_chat_brief(session, record)
        except Exception as exc:
            _log.info(f"update_brief skipped: {exc}")

    def _build_turn_summary_text(self) -> str:
        """Build a one-line text summary of the completed query + chart."""
        parts: list[str] = []
        latest = self.memory.get_latest_query()
        if latest:
            if latest.tables_used:
                parts.append(f"已查询 {', '.join(latest.tables_used[:3])}")
            if latest.row_count > 0:
                parts.append(f"返回 {latest.row_count} 行")
            elif latest.data:
                parts.append("已返回数据")
        if self.memory.charts:
            chart = self.memory.get_latest_chart()
            if chart and chart.chart_config:
                ctype = chart.chart_config.get("type", "?") if isinstance(chart.chart_config, dict) else "?"
                parts.append(f"生成{ctype}图表")
        return "，".join(parts) if parts else ""

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
                cfg = latest_chart.chart_config
                ctype = "?"
                if isinstance(cfg, dict):
                    ctype = cfg.get("type", "?")
                elif isinstance(cfg, str):
                    try:
                        ctype = json.loads(cfg).get("type", "?")
                    except Exception:
                        pass
                parts.append(f"生成{ctype}图表")

        new_summary = " | ".join(parts)

        if self.memory.conversation_summary:
            self.memory.conversation_summary += f"\n{new_summary}"
        else:
            self.memory.conversation_summary = new_summary

        _log.info(f"[Agent] turn summary: {new_summary}")

    # ── chart generation ────────────────────────────────────

    @staticmethod
    def _normalize_chart_config(chart_record) -> None:
        """Convert LLM-generated xField/yField/seriesField to frontend format.

        The DisplayChartBlock component expects:
          columns: [{name, value}, ...]   — field mappings
          axis: {x: {name, value?}, y: {title?}, series: {name, value?}}

        LLMs often produce flat keys like xField/yField/seriesField.
        This normalizes those into the expected structure in-place.
        """
        cfg = chart_record.chart_config
        if not isinstance(cfg, dict):
            return

        # Already has columns + axis — nothing to do
        if cfg.get("columns") and cfg.get("axis"):
            return

        fields = cfg.get("columns", [])
        axis = dict(cfg.get("axis") or {})

        # Convert xField → columns + axis.x
        xf = cfg.pop("xField", None)
        if xf:
            fields.append({"name": xf, "value": xf})
            if "x" not in axis:
                axis["x"] = {"name": xf, "value": xf}

        # Convert yField → columns + axis.y
        yf = cfg.pop("yField", None)
        if yf:
            fields.append({"name": yf, "value": yf})
            if "y" not in axis:
                axis["y"] = {"title": yf}

        # Convert seriesField → columns + axis.series
        sf = cfg.pop("seriesField", None)
        if sf:
            fields.append({"name": sf, "value": sf})
            if "series" not in axis:
                axis["series"] = {"name": sf, "value": sf}

        if fields:
            cfg["columns"] = fields
        if axis:
            cfg["axis"] = axis

    def _update_chart_title(self, memory, label: str) -> None:
        """Prefix the latest chart's title, re-save to DB, re-emit SSE.

        Called after _generate_chart() so the analysis/predict chart is
        visibly distinct from the QA chart.
        """
        import orjson as _orjson
        from apps.chat.curd.chat import save_chart

        chart = memory.get_latest_chart()
        if not chart or not chart.chart_config:
            return
        cfg = chart.chart_config
        if not isinstance(cfg, dict):
            return
        old_title = cfg.get("title", "")
        if not old_title or old_title.startswith(label):
            return

        cfg["title"] = f"{label}：{old_title}"
        _log.info(f"[Agent:{self.profile.name}] chart title updated: '{old_title}' → '{cfg['title']}'")

        # Re-save to DB with updated title
        record = getattr(memory, "record", None)
        session = memory.session
        if record and session:
            try:
                chart_str = _orjson.dumps(cfg).decode()
                save_chart(session=session, record_id=record.id, chart=chart_str)
                # Re-emit so frontend sees the updated title immediately
                self._emit("chart", content=chart_str)
            except Exception as exc:
                _log.info(f"[Agent:{self.profile.name}] chart title re-save skipped: {exc}")

    async def _emit_chart_from_memory(self, query, chart_record) -> None:
        """Save + emit an already-updated chart config (no LLM call).

        Only uses query.data directly — does NOT cross-fetch data from
        other ChatRecords, which would pollute the current record with
        unrelated data.
        """
        import orjson as _orjson
        from apps.chat.agent.tools.chart_tools import _normalize_chart_config_dict
        from apps.chat.curd.chat import save_chart_answer, save_chart, save_sql, save_sql_exec_data

        record = getattr(self.memory, "record", None)
        session = self.memory.session

        chart_record.chart_config = _normalize_chart_config_dict(chart_record.chart_config)
        chart_str = _orjson.dumps(chart_record.chart_config).decode()
        self._emit("chart", content=chart_str)
        self._emit("sql-data", content="execute-success")
        self.memory.mark_chart_this_turn(chart_record.chart_ref)

        if record and session:
            try:
                # AgentMemory.queries[].data is intentionally NOT persisted
                # (see memory.py:190).  For chart-only follow-ups, find the
                # most recent ChatRecord in this chat that actually has data,
                # and copy it to the current record so the frontend can fetch it.
                if not query.data:
                    try:
                        from apps.chat.models.chat_model import ChatRecord as CR
                        from sqlalchemy import select as sa_select, desc, and_
                        chat_id = getattr(self.memory, "chat_id", None)
                        if chat_id:
                            db_rec = session.exec(
                                sa_select(CR.data).where(
                                    and_(CR.chat_id == chat_id, CR.data.isnot(None))
                                ).order_by(desc(CR.create_time)).limit(1)
                            ).first()
                            if db_rec and db_rec[0]:
                                import orjson as _ojson2
                                raw = _ojson2.loads(db_rec[0]) if isinstance(db_rec[0], str) else db_rec[0]
                                query.data = raw
                                _log.info(f"[Agent:{self.profile.name}] copied data from earlier "
                                          f"record in chat {chat_id}")
                    except Exception as _fe:
                        _log.info(f"[Agent:{self.profile.name}] data copy skipped: {_fe}")

                has_data = bool(query.data)
                _log.info(f"[Agent:{self.profile.name}] chart save starting "
                          f"record_id={record.id} "
                          f"query_data={'Y' if has_data else 'N'} "
                          f"data_rows={len(query.data.get('data', [])) if has_data else 0}")
                save_sql(session=session, record_id=record.id, sql=query.sql)
                if query.data:
                    data_str = _orjson.dumps(query.data).decode()
                    _log.info(f"[Agent:{self.profile.name}] saving chart data "
                              f"len={len(data_str)} to record={record.id}")
                    save_sql_exec_data(session=session, record_id=record.id,
                                      data=data_str)
                else:
                    _log.info(f"[Agent:{self.profile.name}] chart-only: no data "
                              f"in query {query.record_id}, skipping data save")
                save_chart_answer(session=session, record_id=record.id,
                                  answer=_orjson.dumps({"content": "chart updated"}).decode())
                save_chart(session=session, record_id=record.id, chart=chart_str)
                _log.info(f"[Agent:{self.profile.name}] chart save complete "
                          f"record_id={record.id}")
            except Exception as exc:
                _log.info(f"save chart skipped: {exc}")

        _log.info(f"[Agent] FINISH (chart-only) iterations={self.iter_count}")

    # ── Chart JSON helper functions ──────────────────────────────────────────

    @staticmethod
    def _build_columns_schema(query_data) -> str:
        """Extract column metadata from SQL result for LLM prompt injection.

        Input:  query.data = {"fields": [...], "data": [[...], ...]}
        Output: text listing each column's name, type, and sample values.
        """
        fields = query_data.get("fields", []) if isinstance(query_data, dict) else []
        data_rows = query_data.get("data", []) if isinstance(query_data, dict) else []

        lines = ["SQL 查询结果列:"]
        sample_rows = data_rows[:3] if data_rows else []

        for i, field in enumerate(fields):
            if isinstance(field, dict):
                name = field.get("name", f"col_{i}")
                col_type = field.get("type", "unknown")
            else:
                name = str(field)
                col_type = "unknown"

            samples = []
            for row in sample_rows:
                if isinstance(row, (list, tuple)) and i < len(row):
                    samples.append(str(row[i]))
                elif isinstance(row, dict) and name in row:
                    samples.append(str(row[name]))

            sample_str = ", ".join(samples[:3]) if samples else "(无数据)"
            lines.append(f"  - {name} (类型: {col_type}, 示例: {sample_str})")

        return "\n".join(lines)

    @staticmethod
    def _extract_sql_output_columns(query) -> set:
        """Extract all valid column names/aliases from SQL result fields + SQL text."""
        columns = set()
        try:
            data = query.data
            if isinstance(data, dict):
                for f in (data.get("fields") or []):
                    if isinstance(f, dict):
                        columns.add(f.get("name", ""))
                    else:
                        columns.add(str(f))
        except Exception:
            pass
        # Also parse aliases from SQL text as fallback
        sql = getattr(query, "sql", "") or ""
        if isinstance(sql, str):
            for m in re.finditer(r'(?:AS\s+|[.`"\[])([`"\[]?)(\w+)\1', sql, re.IGNORECASE):
                columns.add(m.group(2))
        return columns

    @staticmethod
    def _validate_and_fix_chart_json(chart_json, query) -> tuple:
        """Validate and repair LLM-generated chart JSON.

        Returns (fixed_chart: dict, errors: list[str]).
        errors is empty when the chart was valid as-is (no fixes applied).
        Non-empty errors means the chart had problems that were fixed.

        Rules:
          1. Parse failure → default {type:"table"}
          2. type not in registry → fallback to "table"
          3. axis fields referencing columns not in SQL output → remove
          4. title missing → auto-generate from SQL table name
          5. settings field → remove (frontend controls settings)
        """
        import json as _json
        from apps.chat.agent.chart_registry import validate_chart_type

        errors = []

        # Step 1: parse
        if isinstance(chart_json, str):
            try:
                chart = _json.loads(chart_json)
            except _json.JSONDecodeError:
                errors.append("JSON 解析失败，不是有效的 JSON 格式")
                chart = {"type": "table"}
        else:
            chart = chart_json if chart_json else {"type": "table"}

        # Step 2: type validation
        chart_type = chart.get("type", "")
        if not chart_type or not validate_chart_type(chart_type):
            if chart_type:
                errors.append(
                    f"图表类型 '{chart_type}' 不在支持的列表中，"
                    f"已降级为 'table'。支持的类型: table, column, bar, line, pie"
                )
            else:
                errors.append("缺少 'type' 字段，已默认设为 'table'")
            chart["type"] = "table"

        # Step 3: validate axis column names against actual SQL output
        valid_columns = AgentExecutor._extract_sql_output_columns(query)
        if valid_columns and "axis" in chart:
            axis = chart["axis"]
            available = ", ".join(sorted(valid_columns))
            # x
            if axis.get("x") and axis["x"].get("value") not in valid_columns:
                bad_col = axis["x"].get("value", "?")
                errors.append(
                    f"axis.x.value='{bad_col}' 不在 SQL 输出列中。可用列: [{available}]"
                )
                axis.pop("x", None)
            # y (object or list)
            y = axis.get("y")
            if y:
                if isinstance(y, list):
                    bad_y = [item.get("value") for item in y
                             if item.get("value") not in valid_columns]
                    axis["y"] = [item for item in y if item.get("value") in valid_columns]
                    if bad_y:
                        errors.append(
                            f"axis.y 中 {bad_y} 不在 SQL 输出列中。可用列: [{available}]"
                        )
                    if not axis["y"]:
                        axis.pop("y", None)
                elif isinstance(y, dict):
                    if y.get("value") not in valid_columns:
                        bad_col = y.get("value", "?")
                        errors.append(
                            f"axis.y.value='{bad_col}' 不在 SQL 输出列中。可用列: [{available}]"
                        )
                        axis.pop("y", None)
            # series
            if axis.get("series") and axis["series"].get("value") not in valid_columns:
                bad_col = axis["series"].get("value", "?")
                errors.append(
                    f"axis.series.value='{bad_col}' 不在 SQL 输出列中。可用列: [{available}]"
                )
                axis.pop("series", None)
            # multi-quota
            mq = axis.get("multi-quota")
            if mq and isinstance(mq, dict):
                bad_mq = [v for v in mq.get("value", []) if v not in valid_columns]
                mq["value"] = [v for v in mq.get("value", []) if v in valid_columns]
                if bad_mq:
                    errors.append(
                        f"multi-quota.value 中 {bad_mq} 不在 SQL 输出列中。可用列: [{available}]"
                    )
                if not mq["value"]:
                    axis.pop("multi-quota", None)
            # if axis is now empty, remove it so frontend derives from columns
            if not axis:
                chart.pop("axis", None)
                errors.append("axis 中所有字段均无效，已移除 axis（前端将从 columns 自动推导）")

        # Step 4: title fallback
        if not chart.get("title"):
            try:
                sql = getattr(query, "sql", "") or ""
                m = re.search(r'FROM\s+`?(\w+)`?', sql, re.IGNORECASE)
                title = f"{m.group(1)} 数据表" if m else "数据查询结果"
            except Exception:
                title = "数据查询结果"
            chart["title"] = title
            errors.append(f"缺少 'title' 字段，已自动生成: '{title}'")

        # Step 5: remove settings (frontend owns this)
        if "settings" in chart:
            chart.pop("settings", None)

        return chart, errors

    @staticmethod
    def _build_chart_correction_prompt(errors: list, valid_columns: set,
                                        columns_schema_text: str) -> str:
        """Build a correction-feedback prompt for the chart LLM retry."""
        lines = [
            "⚠️ 你上一次生成的图表 JSON 有以下错误，请修正后重新生成：",
            "",
        ]
        for i, err in enumerate(errors, 1):
            lines.append(f"  {i}. {err}")

        lines.append("")
        lines.append("修正要求：")
        lines.append("- axis 中的 'value' 必须是 SQL 输出列名，不能编造")
        lines.append(f"- 可用的列名: {', '.join(sorted(valid_columns))}")
        lines.append("- 如果找不到合适的列来映射 axis，请改用 type='table'")
        lines.append("")
        lines.append("请直接输出修正后的 JSON，不要输出任何其他文本。")

        return "\n".join(lines)

    @staticmethod
    def _resolve_chart_fallback(chart_type: str, query_data: dict | None) -> str:
        """Determine the best fallback chart type based on actual data shape.

        Analyzes column types (temporal / metric / categorical) from
        query_data and picks the chart type whose channel requirements
        the data can actually satisfy.

        Rules (ordered by priority):
          1. column/bar with temporal-only dims → line  (time trend)
          2. column/bar with categorical dims    → column (keep)
          3. pie with ratio/negative issues      → column
          4. line with only categorical dims     → column
          5. anything                            → table
        """
        if not query_data:
            return "table"

        column_profiles = AgentExecutor._profile_data_columns(query_data)
        if not column_profiles:
            return "table"

        temporal_cols = [c for c in column_profiles if c["is_temporal"]]
        metric_cols = [c for c in column_profiles if c["is_metric"]]
        categorical_cols = [
            c for c in column_profiles
            if not c["is_temporal"] and not c["is_metric"]
        ]

        has_temporal = len(temporal_cols) > 0
        has_metric = len(metric_cols) > 0
        has_categorical = len(categorical_cols) > 0

        if not has_metric:
            return "table"  # no numeric column → can't render any chart

        if chart_type in ("column", "bar"):
            # column/bar forbid temporal on x-axis.
            # → line if temporal dims exist (line prefers temporal)
            # → column if categorical dims exist
            # → table otherwise
            if has_temporal:
                return "line"
            if has_categorical:
                return "column"
            return "table"

        if chart_type == "pie":
            # pie needs 1 categorical dim + 1 positive non-ratio metric.
            # → column/bar for comparison; line if temporal trend
            if has_temporal:
                return "line"
            if has_categorical:
                return "column"
            return "table"

        if chart_type == "line":
            # line prefers temporal/ordinal x-axis.
            # → column/bar if only categorical dims available
            if not has_temporal and has_categorical:
                return "column"
            if not has_temporal and not has_categorical:
                return "table"
            return "line"

        return "table"

    @staticmethod
    def _profile_data_columns(query_data: dict) -> list[dict]:
        """Classify each column in query_data as temporal / metric / categorical.

        Returns a list of dicts: {name, is_temporal, is_metric, sample_values}.
        Uses the same heuristics as the frontend classifyColumn().
        """
        import re

        fields = query_data.get("fields", []) if isinstance(query_data, dict) else []
        data_rows = query_data.get("data", []) if isinstance(query_data, dict) else []
        if not fields:
            return []

        TEMPORAL_NAME_RE = re.compile(
            r"时间|日期|date|time|年|月|日|timestamp|datetime|year|month|day",
            re.IGNORECASE,
        )

        profiles: list[dict] = []
        for i, field in enumerate(fields):
            name = field.get("name", f"col_{i}") if isinstance(field, dict) else str(field)

            # Collect sample values for this column
            samples: list = []
            for row in (data_rows or [])[:50]:
                val = None
                if isinstance(row, (list, tuple)) and i < len(row):
                    val = row[i]
                elif isinstance(row, dict) and name in row:
                    val = row[name]
                if val is not None and val != "":
                    samples.append(val)

            # Classify
            is_temporal = TEMPORAL_NAME_RE.search(name) is not None

            non_empty = [s for s in samples if s is not None and s != ""]
            numeric_count = 0
            if non_empty:
                numeric_count = sum(
                    1 for s in non_empty
                    if not isinstance(s, bool)
                    and AgentExecutor._can_coerce_to_number(s)
                )
            is_metric = (
                len(non_empty) > 0 and numeric_count / len(non_empty) >= 0.8
            )

            profiles.append({
                "name": name,
                "is_temporal": is_temporal,
                "is_metric": is_metric and not is_temporal,  # temporal wins
                "sample_values": samples,
            })

        return profiles

    @staticmethod
    def _can_coerce_to_number(value) -> bool:
        """Check if a value can be interpreted as a number."""
        try:
            s = str(value).replace(",", "").replace("%", "").strip()
            if not s:
                return False
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    # ── _generate_chart (with self-correction loop) ────────────────────────

    async def _generate_chart(self, query) -> None:
        """Generate chart config via LLM with self-correction loop.

        Architecture:
          Attempt 1: LLM generates chart JSON → validate
            → valid → save
            → invalid → collect errors, feed back to LLM
          Attempt 2: LLM sees specific errors → regenerates
            → valid → save
            → still invalid → attempt 3 (last chance)
          Attempt 3: final attempt with errors → forcibly fix + save

        Max 3 LLM calls. Each retry appends correction feedback as a new
        HumanMessage so the LLM sees exactly what was wrong.
        """
        from apps.template.generate_chart.generator import get_chart_template
        from apps.chat.curd.chat import save_chart_answer, save_chart, get_chart_config
        import orjson as _orjson
        from langchain_core.messages import SystemMessage as LCSystem, HumanMessage as LCHuman

        record = getattr(self.memory, "record", None)
        session = self.memory.session

        if not query.data:
            _log.info("[Agent] no data for chart generation")
            return

        tpl = get_chart_template()
        sqlbot_name = getattr(self.memory, "sqlbot_name", None) or "SQLBot"
        system_msg = tpl["system"].format(lang="zh-CN", sqlbot_name=sqlbot_name)
        rules_msg = tpl["generate_rules"].format(lang="zh-CN")

        from apps.chat.agent.chart_registry import (
            get_chart_type_hint,
            get_settings_schema_text,
            get_data_constraints_text,
            get_selection_rules,
        )

        question = getattr(self.memory, "user_question", "")
        chart_type = get_chart_type_hint(question, query.data)
        columns_schema_text = self._build_columns_schema(query.data)
        valid_columns = self._extract_sql_output_columns(query)

        user_msg = tpl["user"].format(
            lang="zh-CN", sql=query.sql, question=question,
            chart_type=chart_type,
            columns_schema=columns_schema_text,
            chart_type_settings_schema=get_settings_schema_text(chart_type),
            data_constraints=get_data_constraints_text(),
            chart_selection_rules=get_selection_rules(),
        )

        chart_messages = [
            LCSystem(content=system_msg),
            LCHuman(content=rules_msg),
            LCHuman(content=user_msg),
        ]

        MAX_ATTEMPTS = 3
        chart: dict | None = None
        full_text = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            _log.info(
                f"[Agent] chart attempt {attempt}/{MAX_ATTEMPTS} "
                f"record_id={getattr(query, 'record_id', '?')}"
            )

            full_text = ""
            try:
                for chunk in self.llm.stream(chart_messages):
                    content = chunk.content if hasattr(chunk, "content") else ""
                    reasoning = getattr(chunk, "additional_kwargs", {}).get("reasoning_content", "")
                    if content:
                        full_text += content
                    # Only emit chart-result on first attempt (avoid confusing the frontend)
                    if attempt == 1:
                        self._emit("chart-result", content=content, reasoning_content=reasoning)

                import json as _json
                from common.utils.utils import extract_nested_json
                from apps.chat.agent.chart_knowledge import (
                    validate_chart_semantics,
                    build_correction_prompt_semantic,
                )
                chart_json = extract_nested_json(full_text)
                chart, errors = self._validate_and_fix_chart_json(chart_json, query)

                # ── Semantic validation (channel type compatibility) ──
                if not errors and chart.get("type") != "table":
                    semantic_errors = validate_chart_semantics(chart, query.data or {})
                    if semantic_errors:
                        _log.info(
                            f"[Agent] chart attempt {attempt} semantic issues: "
                            + "; ".join(semantic_errors)
                        )
                        errors.extend(semantic_errors)

                if not errors:
                    # ── Clean: no fixes needed ──
                    _log.info(f"[Agent] chart valid on attempt {attempt}")
                    break

                # ── Had to fix things ──
                _log.info(
                    f"[Agent] chart attempt {attempt} had {len(errors)} issue(s): "
                    + "; ".join(errors)
                )

                if attempt < MAX_ATTEMPTS:
                    # Feed errors back to LLM for self-correction
                    # Use semantic prompt for semantic errors, basic prompt for structural
                    has_semantic = any(
                        "channel" in e or "通道" in e or "metric" in e or "dimension" in e
                        for e in errors
                    )
                    if has_semantic:
                        correction = build_correction_prompt_semantic(
                            errors, chart.get("type", "table"),
                            valid_columns, columns_schema_text
                        )
                    else:
                        correction = self._build_chart_correction_prompt(
                            errors, valid_columns, columns_schema_text
                        )
                    chart_messages.append(LCHuman(content=correction))
                    _log.info(
                        f"[Agent] chart retrying with correction feedback "
                        f"({len(correction)} chars)"
                    )
                else:
                    # Last attempt: try fallback to compatible type
                    _fallback_type = self._resolve_chart_fallback(
                        chart.get("type", "table"), query.data
                    )
                    if _fallback_type != chart.get("type"):
                        _log.info(
                            f"[Agent] chart max attempts, fallback "
                            f"{chart.get('type')} → {_fallback_type}"
                        )
                        chart["type"] = _fallback_type
                    else:
                        _log.info(
                            f"[Agent] chart max attempts reached, using fixed version "
                            f"({len(errors)} issue(s) resolved by validation)"
                        )

            except Exception as stream_exc:
                _log.info(f"[Agent] chart attempt {attempt} stream error: {stream_exc}")
                if attempt < MAX_ATTEMPTS:
                    chart_messages.append(LCHuman(
                        content=f"流式输出中断: {stream_exc}。请直接输出 JSON，不要输出其他文本。"
                    ))
                else:
                    chart = {"type": "table"}
                    break

        # Fallback: if all attempts failed entirely
        if chart is None:
            chart = {"type": "table"}

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
        self.memory.mark_chart_this_turn(chart_ref)
        _log.info(f"[Agent] chart saved to memory: {chart_ref}")

        # ── Render chart picture (MCP) ──
        await self._render_chart_picture(chart, query, chart_ref)

    async def _render_chart_picture(self, chart: dict, query, chart_ref: str) -> None:
        """Render chart as PNG via MCP image host (best-effort, non-blocking).

        Posts chart config + data to MCP_IMAGE_HOST for server-side rendering.
        Failures are logged but never block chart delivery — the frontend
        can always render charts from JSON.
        """
        import os
        import httpx
        import orjson as _orjson
        from common.core.config import settings

        image_host = settings.MCP_IMAGE_HOST
        if not image_host:
            return

        try:
            chart_data = query.data if query else None
            if not chart_data:
                return
            payload = {
                "chart": _orjson.dumps(chart).decode(),
                "data": _orjson.dumps(chart_data, default=str).decode(),
            }
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                resp = await client.post(f"{image_host}/api/chart/render", json=payload)
                if resp.status_code == 200:
                    result = resp.json()
                    image_url = result.get("url") or result.get("image_url")
                    if image_url:
                        self.memory.charts[chart_ref].image_url = image_url
                        _log.info(f"[Agent] chart picture rendered: {image_url}")
        except Exception as e:
            _log.info(f"[Agent] chart picture render skipped: {e}")

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
