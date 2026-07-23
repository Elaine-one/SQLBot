"""
Adapter: bridge the existing LLMService to the new Agent engine.

Provides init_agent_memory() which extracts the necessary context from
an LLMService instance into an AgentMemory, and stream_agent() which
executes the agent and yields SSE events.

Cross-turn memory is persisted via Chat.memory_state (JSON column).
On each question:
  1. Restore AgentMemory from DB (queries, charts, explored_tables, summary)
  2. Inject runtime context (session, user, ds, record)
  3. Run agent
  4. Save AgentMemory back to DB
"""

from __future__ import annotations

import traceback
from typing import Optional

import orjson

from apps.chat.agent.memory import AgentMemory
from apps.chat.agent.executor import AgentExecutor
from apps.chat.agent.tools.register_all import register_all_tools
from common.utils.utils import SQLBotLogUtil as _log


def init_agent_memory(llm_service) -> AgentMemory:
    """
    Build a base AgentMemory from the existing LLMService context.
    Only sets runtime fields (ds, user, etc.) — persistent fields
    (queries, charts, etc.) are restored separately from DB.
    """
    ds = llm_service.ds
    ds_type = ""
    if hasattr(ds, "type"):
        ds_type = ds.type
    elif hasattr(ds, "type_name"):
        ds_type = ds.type_name or ""

    memory = AgentMemory(
        datasource_id=getattr(ds, "id", None),
        datasource_type=ds_type,
        session=None,   # set by caller
        current_user=llm_service.current_user,
        ds=ds,
        out_ds_instance=getattr(llm_service, "out_ds_instance", None),
        # ── workspace settings from LLMService ──────────
        sqlbot_name=getattr(llm_service.chat_question, "sqlbot_name", "SQLBot"),
        enable_sql_row_limit=getattr(llm_service, "enable_sql_row_limit", True),
        context_record_count=getattr(llm_service, "base_message_round_count_limit", 5),
        expand_thinking_block=getattr(llm_service, "expand_thinking_block", True),
    )
    return memory


def _load_prompt_context(session, memory: AgentMemory,
                        user_question: str, oid: int,
                        datasource_id: int | None = None,
                        advanced_app_id: int | None = None) -> None:
    """Load terminology, training examples, and custom prompts for the agent.

    This replaces the old pipeline's filter_terminology_template(),
    filter_training_template(), and filter_custom_prompts() calls.

    Results are stored in memory.{terminology_text, training_examples_text,
    custom_prompts_text} and injected into the system prompt by graph.py.
    """
    # ── Terminology ──────────────────────────────────────────
    if user_question.strip():
        try:
            from apps.terminology.curd.terminology import get_terminology_template
            term_text, _ = get_terminology_template(
                session, user_question, oid=oid, datasource=datasource_id
            )
            if term_text:
                memory.terminology_text = term_text
                _log.info(f"[Agent] loaded terminology for question")
        except Exception as e:
            _log.info(f"[Agent] failed to load terminology: {e}")

    # ── Training examples ────────────────────────────────────
    if user_question.strip():
        try:
            from apps.data_training.curd.data_training import get_training_template
            train_text, _ = get_training_template(
                session, user_question, oid=oid,
                datasource=datasource_id,
                advanced_application_id=advanced_app_id,
            )
            if train_text:
                memory.training_examples_text = train_text
                _log.info(f"[Agent] loaded training examples for question")
        except Exception as e:
            _log.info(f"[Agent] failed to load training examples: {e}")

    # ── Custom prompts (xpack enterprise feature) ────────────
    try:
        from sqlbot_xpack.custom_prompt.curd.custom_prompt import find_custom_prompts
        from sqlbot_xpack.custom_prompt.models.custom_prompt_model import CustomPromptTypeEnum
        prompts = find_custom_prompts(
            session, CustomPromptTypeEnum.GENERATE_SQL, oid, datasource_id
        )
        if prompts:
            parts = ["## 自定义提示词"]
            for p in prompts:
                if hasattr(p, "content"):
                    parts.append(p.content)
            memory.custom_prompts_text = "\n".join(parts)
            _log.info(f"[Agent] loaded {len(prompts)} custom prompts")
    except ImportError:
        pass  # xpack not installed
    except Exception as e:
        _log.info(f"[Agent] failed to load custom prompts: {e}")


# ── memory persistence ────────────────────────────────────


def _load_memory_from_db(session, chat_id: int) -> AgentMemory:
    """Restore AgentMemory persistent state from Chat.memory_state."""
    from apps.chat.models.chat_model import Chat

    if not chat_id:
        return AgentMemory()

    try:
        chat = session.get(Chat, chat_id)
        if chat and chat.memory_state:
            memory = AgentMemory.from_dict(chat.memory_state)
            _log.info(
                f"[Agent] restored memory: {len(memory.queries)} queries, "
                f"{len(memory.charts)} charts, "
                f"{len(memory.explored_tables)} explored tables"
            )
            return memory
    except Exception as e:
        _log.info(f"[Agent] failed to restore memory (starting fresh): {e}")

    return AgentMemory()


def _save_memory_to_db(session, chat_id: int, memory: AgentMemory) -> None:
    """Save AgentMemory persistent state to Chat.memory_state."""
    from apps.chat.models.chat_model import Chat

    if not chat_id:
        return

    try:
        chat = session.get(Chat, chat_id)
        if chat:
            chat.memory_state = memory.to_dict()
            session.add(chat)
            session.commit()
            _log.info(
                f"[Agent] saved memory: {len(memory.queries)} queries, "
                f"{len(memory.charts)} charts"
            )
    except Exception as e:
        _log.info(f"[Agent] failed to save memory: {e}")


def _build_history_context(session, chat_id: int, max_turns: int) -> str:
    """Build conversation history context from DB ChatRecords.

    Returns a compact summary of recent turns for injection into
    the system prompt. Includes questions and a brief description
    of what was generated in each turn.
    """
    if not chat_id:
        return ""

    try:
        from apps.chat.models.chat_model import ChatRecord
        from sqlalchemy import select, and_

        stmt = (
            select(ChatRecord.question, ChatRecord.chart, ChatRecord.sql, ChatRecord.error,
                   ChatRecord.first_chat)
            .where(and_(ChatRecord.chat_id == chat_id, ChatRecord.first_chat == False))
            .order_by(ChatRecord.create_time.asc())
            .limit(max_turns * 2)  # user + assistant pairs
        )
        result = session.execute(stmt)
        rows = result.fetchall()

        if not rows:
            return ""

        parts = ["## 对话历史"]
        for i, row in enumerate(rows[-max_turns * 2:], 1):
            question = (row.question or "")[:120]
            if row.error:
                parts.append(f"Q{i}: {question} → [失败: {row.error[:60]}]")
            elif row.chart and row.sql:
                parts.append(f"Q{i}: {question} → [已生成SQL和图表]")
            elif row.sql:
                parts.append(f"Q{i}: {question} → [已生成SQL]")
            else:
                parts.append(f"Q{i}: {question}")

        return "\n".join(parts)
    except Exception as e:
        _log.info(f"[Agent] failed to build history context: {e}")
        return ""


# ── streaming entry points ────────────────────────────────


async def stream_agent_recommend(
    llm_service,
    session,
    articles_number: int = 4,
):
    """Lightweight recommend questions — no schema embedding."""
    from apps.chat.curd.chat import get_old_questions
    llm = llm_service.llm

    cq = getattr(llm_service, "chat_question", None)
    question = cq.question if cq and cq.question else ""

    old = get_old_questions(session, llm_service.ds.id) if llm_service.ds else []

    prompt = f"""根据当前问题和历史提问，推测用户接下来可能提问的 1-{articles_number} 个问题。
只返回 JSON 数组: ["问题1", "问题2", ...]
不要输出任何其他内容。

当前问题: {question}
历史提问: {old[:10] if old else "无"}
"""
    try:
        response = llm.invoke(prompt)
        content = response.content if hasattr(response, "content") else str(response)
        yield "data:" + orjson.dumps({
            "content": content, "type": "recommended_question"
        }).decode() + "\n\n"
    except Exception as e:
        yield "data:" + orjson.dumps({
            "content": "[]", "type": "recommended_question"
        }).decode() + "\n\n"


async def stream_agent(
    llm_service,
    session,
    question: str,
    is_followup: bool = False,
) -> str:
    """
    Execute the SQLBot Agent and yield SSE events.

    Cross-turn memory flow:
      1. Restore AgentMemory persistent state from Chat.memory_state
      2. Inject runtime context (session, user, ds, record)
      3. If follow-up, build conversation history from DB
      4. Run agent
      5. Save AgentMemory persistent state back to Chat.memory_state
    """
    # Lazy-init tools (once per process)
    if not hasattr(stream_agent, "_tools_registered"):
        register_all_tools()
        stream_agent._tools_registered = True

    llm = llm_service.llm
    record = llm_service.record  # ChatRecord from init_record()
    chat_id = getattr(llm_service.chat_question, "chat_id", None)

    # ═══ Step 1: Build runtime context ═══
    base_memory = init_agent_memory(llm_service)

    # ═══ Step 2: Restore persistent state from DB ═══
    if chat_id:
        memory = _load_memory_from_db(session, chat_id)
        # Merge runtime fields into restored memory
        memory.datasource_id = base_memory.datasource_id
        memory.datasource_type = base_memory.datasource_type
        memory.session = session
        memory.current_user = base_memory.current_user
        memory.ds = base_memory.ds
        memory.out_ds_instance = base_memory.out_ds_instance
        memory.sqlbot_name = base_memory.sqlbot_name
        memory.enable_sql_row_limit = base_memory.enable_sql_row_limit
        memory.context_record_count = base_memory.context_record_count
        memory.expand_thinking_block = base_memory.expand_thinking_block
    else:
        memory = base_memory
        memory.session = session

    # ═══ Step 3: Set per-turn fields ═══
    memory.user_question = question
    memory.chat_id = chat_id
    memory.is_followup = is_followup
    memory.record = record
    memory.terminal_triggered = False
    memory.iteration = 0
    memory.sql_retry_count = 0

    # ═══ Step 4: Load prompt enrichment context ═══
    _load_prompt_context(
        session, memory, question,
        oid=getattr(llm_service.current_user, "oid", 1),
        datasource_id=getattr(memory, "datasource_id", None),
        advanced_app_id=getattr(
            getattr(llm_service, "current_assistant", None), "id", None
        ) if getattr(llm_service, "current_assistant", None) else None,
    )

    # ═══ Step 5: Build conversation history context ═══
    if is_followup and chat_id:
        memory.conversation_history = _build_history_context(
            session, chat_id, max_turns=memory.context_record_count)

    # ═══ Step 5: Emit record ID (frontend needs this) ═══
    if memory.record:
        yield "data:" + orjson.dumps({
            "type": "id", "id": memory.record.id
        }).decode() + "\n\n"
        yield "data:" + orjson.dumps({
            "type": "question", "question": question
        }).decode() + "\n\n"

    # ═══ Step 6: Run agent in background thread ═══
    # Uses asyncio.Queue instead of polling a shared list.
    # The agent pushes chunks to the queue; we await them here
    # without burning CPU.  A None sentinel means agent is done.
    import asyncio
    queue: asyncio.Queue = asyncio.Queue()
    executor = AgentExecutor(llm, memory, queue)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, executor.run)

    while True:
        chunk = await queue.get()
        if chunk is None:      # sentinel: agent finished
            break
        yield chunk

    # ═══ Step 7: Save persistent state back to DB ═══
    if chat_id:
        _save_memory_to_db(session, chat_id, memory)
