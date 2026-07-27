"""
LangGraph agent graph for SQLBot.

Builds a ReAct-style agent graph:

    agent_node ←──→ tools_node (custom, passes AgentMemory)
         │
         └──→ END (terminal triggered or max iterations)

Cross-turn state is checkpointed via a shared LangGraph MemorySaver.
"""

from __future__ import annotations

from typing import Literal
import json

from langgraph.graph import StateGraph, END
from langchain_core.messages import SystemMessage, ToolMessage, AIMessage

from apps.chat.agent.state import AgentState
from apps.chat.agent.memory import AgentMemory
from apps.chat.agent.tools.registry import ToolRegistry


def _build_dialect_guide(dialect, ds_type: str) -> str:
    """Build a database-specific SQL syntax guide from the Dialect registry.

    Injected directly into the system prompt so the LLM always knows
    the correct SQL dialect — no need to call load_skill for basic syntax.
    """
    quote = dialect.quote_char
    if quote == '"':
        quote_desc = "双引号"
        quote_open = quote_close = '"'
    elif quote == '`':
        quote_desc = "反引号"
        quote_open = quote_close = '`'
    elif quote == '[':
        quote_desc = "方括号"
        quote_open, quote_close = '[', ']'
    else:
        quote_desc = quote
        quote_open = quote_close = quote

    quote_example = f'{quote_open}table_name{quote_close}'
    quote_select = f'SELECT {quote_open}column{quote_close} FROM {quote_open}table{quote_close}'

    # Row limit syntax
    if dialect.supports_explain and dialect.explain_prefix == "EXPLAIN ":
        # MySQL/PG/CK style
        limit_syntax = "LIMIT N"
    elif dialect.db_type == "sqlServer":
        limit_syntax = "TOP N"
    elif dialect.db_type in ("oracle", "dm"):
        limit_syntax = "WHERE ROWNUM <= N"
    else:
        limit_syntax = "LIMIT N"

    guide = f"""## SQL 方言指南: {dialect.display_name}

- 标识符引用: {quote_desc}，如 {quote_select}
- 行数限制: {limit_syntax}
{f'- 注意: 不支持 EXPLAIN 语法，写 SQL 时不需要考虑 EXPLAIN 兼容性' if not dialect.supports_explain else ''}
- 只写 {dialect.display_name} 兼容的 SQL，禁止混用其他数据库语法
"""

    if dialect.db_type == "sqlServer":
        guide += """
- 字符串拼接: CONCAT() 或 + 运算符
- 百分比格式化: CONVERT(VARCHAR, ROUND(x*100, 2)) + '%'
- 分页: OFFSET N ROWS FETCH NEXT M ROWS ONLY（需配合 ORDER BY）
"""
    elif dialect.db_type in ("oracle", "dm"):
        guide += """
- 字符串拼接: || 运算符
- 注意 Oracle 对别名和 GROUP BY 的严格限制
"""
    elif dialect.db_type in ("mysql", "doris", "starrocks"):
        guide += """
- 字符串拼接: CONCAT()
- 百分比格式化: CONCAT(ROUND(x*100, 2), '%')
"""
    elif dialect.db_type == "pg":
        guide += """
- 字符串拼接: || 运算符
- 类型转换: ::type 语法
"""

    guide += f"\n数据源类型: {ds_type} ({dialect.display_name})\n"
    return guide


def _build_system_prompt(memory: AgentMemory, is_followup: bool = False,
                         profile=None) -> str:
    """Build the system prompt for the agent.

    If `profile` provides a non-empty system_prompt, return it as-is
    (analysis/predict profiles embed data directly in the prompt).

    Otherwise builds the QA prompt from template variables:
      - conversation_history (prior Q&A pairs from DB)
      - conversation_summary (accumulated turn summaries)
      - existing queries (for edit_sql_query reference)
      - existing charts (for edit_chart reference)
      - explored_tables (to avoid redundant get_table_metadata calls)
    """
    # Profile override: analysis/predict use pre-built prompts
    if profile is not None and profile.system_prompt:
        return profile.system_prompt

    ds_type = memory.datasource_type or "unknown"
    context = memory.get_context_for_llm()
    sqlbot_name = memory.sqlbot_name or "SQLBot"
    limit_rows = memory.enable_sql_row_limit
    ds_type = memory.datasource_type or "unknown"

    # ── Auto-inject SQL dialect guide (database-specific syntax) ──
    from apps.db.dialect import get_dialect
    db_dialect = get_dialect(ds_type)
    dialect_guide = _build_dialect_guide(db_dialect, ds_type)

    base = f"""你是 {sqlbot_name}，一个数据分析助手。通过调用工具完成数据查询和可视化。

操作按以下优先级：

## 1. 语义层优先（如有配置）

先用 `load_skill` 加载当前数据源的语义文件:
- `load_skill("business-{{数据源名}}_metrics")` → 指标 SQL 片段
- `load_skill("business-{{数据源名}}_segments")` → 可复用筛选条件
- `load_skill("business-{{数据源名}}_derived_metrics")` → 派生指标公式

语义层覆盖的指标 → 直接用其 SQL 片段，只在此基础上添加 GROUP BY / WHERE / ORDER BY。
语义层未覆盖的维度或筛选 → 用下面的探索流程补充。

## 2. 没有语义层或语义层未覆盖时 → 探索

1. `search_relevant_tables` 找到入口（不要猜表名）
2. `get_table_metadata` 深入确认
3. `get_table_sample_data` 或 `get_field_values` 验证字段含义和数值范围
4. 需要自定义 SQL 验证数据 → `preview_sql`（内部工具，不会展示给用户）
5. 不要用稍有不同的措辞反复调用 search_relevant_tables —— 结果已缓存

## 3. 构建最终查询

- 涉及金额/利润/成本时，字段名 ≠ 字段含义（如 subtotal 可能是折扣后参考价）
- 优先查找名称含 profit/snapshot/summary 的表作为权威数据源
- 通过 preview_sql 或 sample_data 交叉验证数值范围是否合理
- **只有准备好给用户的最终答案时，才调用 create_sql_query**

## 工具表

### 内部工具（Agent 使用，结果不展示给用户）

| 场景 | 工具 | 说明 |
|------|------|------|
| 加载语义层 | `load_skill` | business-* 或 sql-* |
| 搜索相关表 | `search_relevant_tables` | 语义搜索，返回候选表 |
| 看表结构 | `get_table_metadata` | 字段名、类型、注释 |
| 看字段值 | `get_field_values` | 去重样本值，辅助 WHERE |
| 看样本数据 | `get_table_sample_data` | 3 行样本 |
| **内部 SQL 验证** | `preview_sql` | **自定义只读 SELECT，仅 agent 看到结果** |
| 执行已有查询 | `execute_sql_query` | 执行已创建的查询记录 |
| 分析查询结果 | `analyze_query_result` | LLM 深度分析 |
| 数据统计摘要 | `get_data_summary` | min/max/avg/distinct |
| 翻阅数据 | `get_data_preview` | 分页查看，每页 20 行 |
| 搜索外部信息 | `search_web` | 互联网搜索（预测专用）|

### 用户可见工具（结果展示给用户，调用前确保已是最终答案）

| 场景 | 工具 | 终端 | 说明 |
|------|------|------|------|
| **创建最终查询** | `create_sql_query` | ✅ | **调用后自动执行+生成图表展示给用户** |
| 修改查询 | `edit_sql_query` | ✅ | 编辑后自动重新执行+更新图表 |
| 替换 SQL 片段 | `replace_sql_fragment` | ✅ | 单处替换 |
| 创建图表 | `create_chart` | ❌ | 为已有查询配置图表 |
| 修改图表 | `edit_chart` | ✅ | 修改图表类型/样式 |
| 需要用户澄清 | `ask_for_clarification` | ✅ | 向用户提问 |

## 查询规范

{f"- 明细数据必须限制返回行数，默认 1000；聚合查询不需要" if limit_rows else ""}
- 用户说"全部""所有"时不限制行数

## ⚠️ 关键规则

- **`preview_sql` vs `create_sql_query`**：前者是内部验证，后者是最终答案。
  如果你想"我先查一下看看数据长什么样"→ 用 `preview_sql`。
  只有当你确信这就是用户要的最终结果时 → 用 `create_sql_query`。
- **`create_sql_query` 的后果**：一旦调用成功，系统立即执行 SQL、生成图表、展示给用户。
  此时 agent 循环终止，你无法再做更多探索或修正。
- **终端工具**：create_sql_query / edit_sql_query / replace_sql_fragment / edit_chart / ask_for_clarification
  成功 → 本轮完成。失败 → 修正后重试。

{dialect_guide}"""

    # ═══ DataEase dataset guidance (assistant mode only) ═══
    if memory.out_ds_instance is not None:
        base += """
## DataEase 数据集

部分表标注了 is_dataset=true，它们是预定义的逻辑视图，底层有完整的 SQL 查询。
你不需关心底层 SQL 细节——像操作普通表一样写 SQL 即可，
create_sql_query 会自动处理数据集到物理查询的转换。

规则：
- 用 get_table_metadata 返回的字段 name 作为列名写入 SQL
- 如果字段有 comment（业务名称），用 AS 别名映射为可读输出
- 用 get_field_values / get_table_sample_data 确认字段值
- 不要尝试修改或"优化"数据集的内部 SQL
"""

    # ═══ Inject custom prompts (enterprise xpack) ═══
    if memory.custom_prompts_text:
        base += f"\n{memory.custom_prompts_text}\n"

    # ═══ Inject terminology (pgvector semantic match) ═══
    if memory.terminology_text:
        base += f"\n## 业务术语定义\n{memory.terminology_text}\n"

    # ═══ Inject training examples (pgvector semantic match) ═══
    if memory.training_examples_text:
        base += f"\n## SQL训练示例\n{memory.training_examples_text}\n"

    # ═══ Inject conversation history (from DB ChatRecords) ═══
    if memory.conversation_history:
        base += f"\n{memory.conversation_history}\n"

    # ═══ Inject conversation summary (accumulated turn summaries) ═══
    if memory.conversation_summary:
        base += f"\n<conversation-summary>{memory.conversation_summary}</conversation-summary>\n"

    # ═══ Inject existing artifacts (queries + charts) ═══
    if memory.queries:
        base += "\n## 已有查询\n"
        for qid, q in memory.queries.items():
            tables = ", ".join(q.tables_used) if q.tables_used else "?"
            sql_preview = q.sql[:150] + "..." if len(q.sql) > 150 else q.sql
            base += f"- `{qid}` (状态:{q.status}, 表:{tables}): {sql_preview}\n"

    if memory.charts:
        base += "\n## 已有图表\n"
        for cid, c in memory.charts.items():
            ctype = c.chart_config.get("type", "unknown") if c.chart_config else "unknown"
            base += f"- `{cid}` (类型:{ctype}, 绑定查询:{c.record_id})\n"

    # ═══ Inject explored table cache hint ═══
    if memory.explored_tables:
        table_names = list(memory.explored_tables.keys())
        base += f"\n已缓存的表结构: {', '.join(table_names)}（字段结构已获取，无需重复调用 get_table_metadata）\n"

    # ═══ Follow-up mode: edit-first guidance ═══
    if is_followup and (memory.queries or memory.charts or context.strip()):
        base += """
## 追问模式

你正在处理一条追问消息。上方列出了之前对话中产生的可引用对象。

决策规则:
1. 同类图表切换（柱状图↔条形图↔折线图）
   → `edit_chart(chart_ref, {"type": "column/bar/line"})` ← 不改SQL
2. 切换到饼图（"扇形图""饼图""占比""份额"）
   → `create_chart` ← 饼图轴结构与笛卡尔图不同，必须重新生成
      constraints: series=分类(≤10), y=正数值, 不支持负值/多指标/时间轴
3. 切换到表格（"表格""明细""列表"）
   → `create_chart` ← 表格用 columns 结构，不同于 axis 结构
4. 仅改样式（"加标题""换颜色""环形"）
   → `edit_chart` ← 只改 settings/extra
5. 用户要求修改数据范围/条件（"只看华东区""加上利润列"）
   → `edit_sql_query` ← 字符串替换，系统会自动重新执行
6. 用户要求换个统计维度（"改成按月份""按客户分组"）
   → 检查已有查询是否覆盖该维度
   → 覆盖 → `edit_sql_query`
   → 不覆盖 → `create_sql_query`
7. 用户提出全新话题 → `create_sql_query`
8. 你创建的每个图表都会保存为新记录，历史图表不会丢失
   → 不需要担心覆盖问题，只需专注于生成当前轮的最佳结果
"""

    return base


def make_agent_node(llm, memory: AgentMemory, schemas: list[dict] | None = None,
                    profile=None):
    """Create the agent node function (LLM call with tools).

    If `schemas` is provided, only those tools are available to the LLM.
    If None, all registered tools are used (QA default).

    If `profile` is provided, its system_prompt can override the
    template-built prompt (used by analysis/predict).

    AgentMemory is captured via closure, not stored in LangGraph state.
    """
    if schemas is None:
        schemas = ToolRegistry.get_openai_schemas()

    llm_with_tools = llm.bind_tools(schemas) if schemas else llm

    def agent_node(state: AgentState) -> dict:
        messages = list(state["messages"])

        # Inject system prompt on first call (per turn)
        if not any(isinstance(m, SystemMessage) for m in messages):
            is_followup = getattr(memory, "is_followup", False)
            messages = [SystemMessage(content=_build_system_prompt(
                memory, is_followup, profile))] + messages

        try:
            response = llm_with_tools.invoke(messages)
        except Exception as exc:
            from common.utils.utils import SQLBotLogUtil as _log
            _log.info(f"[Agent] LLM call failed: {exc}")
            if hasattr(exc, "response"):
                try:
                    _log.info(f"[Agent] LLM response status: {exc.response.status_code}")
                    _log.info(f"[Agent] LLM response body: {exc.response.text[:2000]}")
                except Exception:
                    pass
            sys_msg = next((m.content for m in messages if isinstance(m, SystemMessage)), "")
            _log.info(f"[Agent] System prompt length: {len(sys_msg)} chars, "
                      f"total messages: {len(messages)}")
            raise

        return {
            "messages": [response],
            "iteration": state.get("iteration", 0) + 1,
        }

    return agent_node


def _make_tools_node(memory: AgentMemory):
    """Create a custom tools node that accesses AgentMemory via closure."""

    async def tools_node(state: AgentState) -> dict:
        messages = list(state["messages"])
        last_msg = messages[-1]

        if not hasattr(last_msg, "tool_calls") or not last_msg.tool_calls:
            return {"messages": []}

        tool_messages: list[ToolMessage] = []
        for tc in last_msg.tool_calls:
            args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            result = await ToolRegistry.execute(
                name=tc["name"] if isinstance(tc, dict) else tc.name,
                args=args,
                memory=memory,
            )
            tool_msg = ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                name=tc["name"] if isinstance(tc, dict) else tc.name,
                tool_call_id=tc["id"] if isinstance(tc, dict) else tc.id,
            )
            tool_messages.append(tool_msg)

        return {"messages": tool_messages}

    return tools_node


def _route_fn(memory: AgentMemory):
    """Create a conditional routing function with closure-captured memory."""
    def route(state: AgentState) -> Literal["tools", "end"]:
        if memory.terminal_triggered:
            return "end"
        if state.get("iteration", 0) >= memory.max_iterations:
            return "end"
        if memory.sql_retry_count > memory.max_sql_retries:
            return "end"

        last_msg = state["messages"][-1] if state["messages"] else None
        if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"

        return "end"
    return route


def build_agent_graph(llm, memory: AgentMemory, profile=None):
    """
    Build the SQLBot Agent LangGraph.

    If `profile` is provided with explicit `tool_names`, only those tools
    are bound to the LLM. Each profile carries its own tool list (QA=13,
    analysis=5, predict=6). The registry is shared — tools are registered
    once, and each profile picks what it needs by name.

    AgentMemory is captured via closure — NOT stored in LangGraph state
    (it contains non-serializable objects like DB sessions).

    No checkpointer is used.  Cross-turn context comes from AgentMemory
    persistence (Chat.memory_state), NOT from LangGraph checkpointing.
    Using a shared checkpointer would leak intermediate tool-call messages
    from prior questions into the current LLM context.
    """
    # Determine tool schemas — each profile carries an explicit tool_names list
    if profile is not None and profile.tool_names:
        schemas = ToolRegistry.get_openai_schemas_for(profile.tool_names)
    else:
        # Fallback (no profile or empty list): all registered tools
        schemas = ToolRegistry.get_openai_schemas()

    workflow = StateGraph(AgentState)
    workflow.add_node("agent", make_agent_node(llm, memory, schemas, profile))
    workflow.add_node("tools", _make_tools_node(memory))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", _route_fn(memory), {
        "tools": "tools",
        "end": END,
    })
    workflow.add_edge("tools", "agent")

    return workflow.compile()
