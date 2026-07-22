"""
# executor.py 集成变更说明

将 chart_channel_knowledge.json 集成到 _generate_chart() 管线中。
改动点标注为 ✨ NEW，原有代码标注为 --- 不变 ---
"""

# ═══════════════════════════════════════════════════════════════════════════════
# 变更 1: import 区新增
# ═══════════════════════════════════════════════════════════════════════════════

# 在 executor.py 顶部 import 区新增:
# from apps.chat.agent.chart_knowledge import (
#     generate_llm_knowledge_text,
#     validate_chart_semantics,
#     build_correction_prompt_semantic,
# )


# ═══════════════════════════════════════════════════════════════════════════════
# 变更 2: _generate_chart() 方法中，将知识库注入 LLM prompt
# ═══════════════════════════════════════════════════════════════════════════════

async def _generate_chart_v2(self, query) -> None:  # 替代原 _generate_chart
    """...原有 docstring 不变..."""
    # --- 以下为原有代码 (lines 932-967) ---
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
    system_msg = tpl["system"].format(lang="zh-CN", sqlbot_name="SQLBot")
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

    # ─── ✨ NEW: 将通道知识库注入用户提示词 ───
    from apps.chat.agent.chart_knowledge import generate_llm_knowledge_text
    channel_knowledge_text = generate_llm_knowledge_text(chart_type, lang="zh-CN")
    # ─── ✨ END ───

    user_msg = tpl["user"].format(
        lang="zh-CN", sql=query.sql, question=question,
        chart_type=chart_type,
        columns_schema=columns_schema_text,
        chart_type_settings_schema=get_settings_schema_text(chart_type),
        data_constraints=get_data_constraints_text(),
        chart_selection_rules=get_selection_rules(),
    )

    # ─── ✨ NEW: 将通道知识库追加到 user prompt ───
    if channel_knowledge_text:
        user_msg += "\n\n" + channel_knowledge_text
    # ─── ✨ END ───

    chart_messages = [
        LCSystem(content=system_msg),
        LCHuman(content=rules_msg),
        LCHuman(content=user_msg),
    ]

    # --- 以下循环及保存逻辑不变 (lines 975-1061) ---
    MAX_ATTEMPTS = 3
    chart: dict | None = None
    full_text = ""
    valid_columns = self._extract_sql_output_columns(query)

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
                if attempt == 1:
                    self._emit("chart-result", content=content, reasoning_content=reasoning)

            import json as _json
            from common.utils.utils import extract_nested_json
            chart_json = extract_nested_json(full_text)

            # ─── ✨ NEW: 双层验证 = 列名验证 + 语义验证 ───
            chart, col_errors = self._validate_and_fix_chart_json(chart_json, query)

            # 语义级验证（只在列名验证通过时执行）
            semantic_errors = []
            if not col_errors and query.data:
                from apps.chat.agent.chart_knowledge import validate_chart_semantics
                semantic_errors = validate_chart_semantics(chart, query.data)

            all_errors = col_errors + semantic_errors
            # ─── ✨ END ───

            if not all_errors:
                _log.info(f"[Agent] chart valid on attempt {attempt}")
                break

            _log.info(
                f"[Agent] chart attempt {attempt} had {len(all_errors)} issue(s): "
                + "; ".join(all_errors)
            )

            if attempt < MAX_ATTEMPTS:
                # ─── ✨ NEW: 增强纠错提示，融入语义反馈 ───
                from apps.chat.agent.chart_knowledge import build_correction_prompt_semantic

                # 列名级纠错
                correction = self._build_chart_correction_prompt(
                    col_errors, valid_columns, columns_schema_text
                )

                # 语义级纠错（追加到列名纠错后面）
                if semantic_errors:
                    semantic_correction = build_correction_prompt_semantic(
                        semantic_errors, chart.get("type", "table"),
                        valid_columns, columns_schema_text
                    )
                    correction += "\n\n" + semantic_correction

                chart_messages.append(LCHuman(content=correction))
                _log.info(
                    f"[Agent] chart retrying with correction feedback "
                    f"(col_errors={len(col_errors)}, "
                    f"semantic_errors={len(semantic_errors)})"
                )
                # ─── ✨ END ───
            else:
                _log.info(
                    f"[Agent] chart max attempts reached, using fixed version "
                    f"({len(all_errors)} issue(s) resolved by validation)"
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

    # --- 保存及 emit 逻辑不变 (lines 1039-1061) ---
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
    _log.info(f"[Agent] chart saved to memory: {chart_ref}")


# ═══════════════════════════════════════════════════════════════════════════════
# 变更 3: template.yaml 中新增 {channel_knowledge} 占位符已由 user_msg += 追加方式替代
# 无需修改 template.yaml，知识库文本直接追加在 user_msg 末尾
# ═══════════════════════════════════════════════════════════════════════════════
