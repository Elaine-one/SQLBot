# SQLBot Agent 系统维护手册

本文档面向**后期维护人员**，涵盖三个 Agent（QA / analysis / predict）的架构、工具注册流程、思考-回复链路、以及常见维护操作。

---

## 目录

1. [三个 Agent 概览](#1-三个-agent-概览)
2. [工具系统](#2-工具系统)
3. [新增一个工具（完整流程）](#3-新增一个工具完整流程)
4. [Agent 思考与回复链路](#4-agent-思考与回复链路)
5. [SSE 事件类型与前端渲染](#5-sse-事件类型与前端渲染)
6. [终端工具 vs 非终端工具](#6-终端工具-vs-非终端工具)
7. [内部工具 vs 用户可见工具](#7-内部工具-vs-用户可见工具)
8. [后处理流程](#8-后处理流程)
9. [跨轮次内存](#9-跨轮次内存)
10. [关键文件速查表](#10-关键文件速查表)

---

## 1. 三个 Agent 概览

SQLBot 有三套 Agent 配置，定义在 `backend/apps/chat/agent/engine.py` 的 `AgentProfile`：

```
┌──────────┬──────────┬─────────────┬────────────────┬───────────────┐
│ Profile  │ 触发方式  │ 工具数       │ 最大迭代        │ 后处理         │
├──────────┼──────────┼─────────────┼────────────────┼───────────────┤
│ qa       │ 默认提问  │ 14          │ 50             │ execute_and_chart│
│ analysis │ /analysis │ 4           │ 10             │ text_and_chart │
│ predict  │ /predict  │ 5           │ 15             │ text_and_chart │
└──────────┴──────────┴─────────────┴────────────────┴───────────────┘
```

### QA（默认）

用户正常提问时使用。完整工具集——先探索数据库 Schema，再生成 SQL，执行并出图。

```
探索链: search_relevant_tables → get_table_metadata → get_field_values → preview_sql
创建链: create_sql_query → (自动执行) → (自动出图)
编辑链: edit_sql_query / replace_sql_fragment / edit_chart
```

14 个工具中 5 个是终端工具，成功后终止 Agent 循环。

### Analysis（数据分析）

用户对已有查询结果请求深度分析。系统把数据注入 prompt，Agent 用 4 个内部工具逐步探索数据并输出分析文本。不走 `create_sql_query`，不能改写 SQL。

### Predict（数据预测）

类似 Analysis，但多了 `search_web` 工具进行互联网搜索，辅助趋势预测。

---

## 2. 工具系统

### 2.1 核心数据结构

**`ToolDef`** (`tools/registry.py`)：

```python
@dataclass
class ToolDef:
    name: str              # 唯一标识，LLM 通过此名调用
    description: str       # LLM 可读的功能描述（会注入 function-calling schema）
    parameters: dict       # JSON Schema，定义工具参数
    fn: Callable           # async fn(**kwargs, memory: AgentMemory) -> dict
    terminal: bool         # 成功后是否终止 Agent 循环
    show_to_user: bool     # 工具调用/结果是否推送前端 SSE
    category: str          # "explore" | "create" | "edit" | "execute" | "internal"
```

**`ToolRegistry`** (`tools/registry.py`)：

全局单例注册表。提供：
- `register(tool)` — 注册工具
- `get(name)` — 按名查找
- `get_openai_schemas()` — 生成 OpenAI function-calling schema
- `execute(name, args, memory)` — 执行工具
- `is_user_visible(name)` — 查询 show_to_user 标志
- `get_terminal_tool_names()` — 获取所有终端工具名

### 2.2 工具注册位置

```
register_all.py          ← 所有工具的定义（name, description, parameters, terminal, show_to_user）
    ├── schema_tools.py  ← search_relevant_tables, get_table_metadata, get_table_sample_data
    ├── query_tools.py   ← get_field_values, execute_sql_query
    ├── sql_tools.py     ← create_sql_query, edit_sql_query
    ├── chart_tools.py   ← create_chart, edit_chart
    └── advanced_tools.py ← preview_sql, ask_for_clarification, analyze_query_result,
                             load_skill, get_data_summary, get_data_preview, search_web,
                             replace_sql_fragment
```

### 2.3 工具分配到 Profile

在 `engine.py` 的 `build_*_profile()` 中，`tool_names` 列表决定该 Agent 能使用哪些工具：

```python
# QA: 14 tools (全部探索 + 创建 + 编辑)
tool_names = [
    "search_relevant_tables", "get_table_metadata", ... "ask_for_clarification"
]

# Analysis: 4 tools (只看数据，不改 SQL)
tool_names = ["get_data_summary", "get_data_preview", "analyze_query_result", "ask_for_clarification"]

# Predict: 5 tools (analysis + 网络搜索)
tool_names = [..., "search_web"]
```

`graph.py` 中 `build_agent_graph()` 根据 `profile.tool_names` 过滤 schema：

```python
if profile is not None and profile.tool_names:
    schemas = ToolRegistry.get_openai_schemas_for(profile.tool_names)
else:
    schemas = ToolRegistry.get_openai_schemas()  # 全部工具
```

**重要**：工具必须先通过 `register_all_tools()` 注册到 `ToolRegistry`，然后才能在各 profile 的 `tool_names` 中引用。两步缺一不可。

---

## 3. 新增一个工具（完整流程）

以新增 `preview_sql` 为例，逐步说明：

### Step 1：实现工具函数

在合适的 `*_tools.py` 中编写 async 函数，签名必须是 `async def tool_name(..., memory: AgentMemory) -> dict`：

```python
# 文件: tools/advanced_tools.py

async def preview_sql(
    sql: str,
    memory: AgentMemory = None,
    max_rows: int = 20,
) -> dict:
    """Execute a read-only SQL query for internal agent exploration.

    NON-TERMINAL — the agent continues exploring after calling this.
    Results are returned to the agent ONLY; nothing is shown to the user.
    """
    # 1. 校验 SQL
    # 2. 执行查询
    # 3. 返回结果
    return {"success": True, "row_count": N, "rows": [...]}
```

**返回值约定**：
- `{"success": True, ...}` — 成功。terminal 工具会触发 `memory.terminal_triggered = True`
- `{"success": False, "error": "具体原因"}` — 失败。Agent 看到错误后修正重试

### Step 2：在 `register_all.py` 注册

```python
# 2a: 懒加载导入
def _lazy_import_tools():
    from apps.chat.agent.tools.advanced_tools import preview_sql
    return {..., "preview_sql": preview_sql}

# 2b: 工具定义（决定 terminal / show_to_user / category）
_TOOL_DEFS = [
    ...
    dict(name="preview_sql", fn_key="preview_sql",
         terminal=False, show_to_user=False, category="internal",
         description="执行只读 SQL 查询进行内部数据验证...",
         parameters={"type": "object",
             "properties": {
                 "sql": {"type": "string", "description": "完整的 SQL SELECT 语句"},
                 "max_rows": {"type": "integer", "description": "最多返回行数，默认 20"},
             },
             "required": ["sql"]}),
]
```

**字段决策指南**：

| 场景 | terminal | show_to_user | category |
|------|----------|-------------|----------|
| Agent 内部探索（查表、翻数据） | `False` | `False` | `"explore"` |
| Agent 内部执行（analyze、preview） | `False` | `False` | `"internal"` |
| 给用户的最终产物（SQL、图表） | `True` | `True` | `"create"/"edit"` |
| 向用户提问澄清 | `True` | `True` | `"explore"` |

### Step 3：在 `engine.py` 分配给 profile

```python
def build_qa_profile():
    return AgentProfile(
        tool_names=[
            ...
            "preview_sql",    # ← 添加到这里
            ...
        ],
        terminal_tools=[...],
    )
```

### Step 4：添加结果摘要

在 `executor.py` 的 `_tool_result_summary()` 中添加一行：

```python
def _tool_result_summary(tool_name: str, result: dict) -> str:
    ...
    elif tool_name == "preview_sql":
        rows = result.get("row_count", 0)
        returned = result.get("returned", 0)
        truncated = "(截断)" if result.get("truncated") else ""
        return f"内部查询: {returned}/{rows} 行 {truncated}"
```

这个摘要仅用于 `execution_log`（服务端调试），`show_to_user=False` 的工具不会推到前端 SSE。

### Step 5：更新系统提示词

如果新工具改变了 Agent 的行为策略，在 `graph.py` 的 `_build_system_prompt()` 中更新工具表和关键规则。

### Step 6：更新测试

- `tests/test_agent.py` — 测试工具注册、验证逻辑
- `tests/test_integration.py` — 测试 API/SSE 端点

---

## 4. Agent 思考与回复链路

### 4.1 整体流程

```
用户输入 "本月销售额"
    │
    ▼
POST /api/v1/chat/question (SSE)
    │
    ▼
engine.py: dispatch(profile) → AgentExecutor.run()
    │
    ▼
┌─────────────────────────────────────────────┐
│  LangGraph ReAct 循环                        │
│                                             │
│  ① agent_node: SystemMessage + HumanMessage │
│     ↓                                       │
│     LLM 调用（带 tool schemas）               │
│     ↓                                       │
│     返回 AIMessage:                          │
│       - content → reasoning (thinking面板)   │
│       - tool_calls → 执行工具                │
│     ↓                                       │
│  ② tools_node: ToolRegistry.execute()       │
│     ↓                                       │
│     ToolMessage 返回给 LLM                   │
│     ↓                                       │
│  ③ 路由检查:                                 │
│     - terminal_triggered? → 结束循环         │
│     - 超过 max_iterations? → 结束            │
│     - 还有 tool_calls? → 回到 ①              │
│     - 否则 → 结束                            │
└─────────────────────────────────────────────┘
    │
    ▼
_post_process()
    ├── execute_and_chart (QA): 执行 SQL → 生成图表
    └── text_and_chart (analysis/predict): 保存文本 → 生成图表
    │
    ▼
emit("finish") → _finalize_record_and_chat()
```

### 4.2 系统提示词构建

`graph.py:_build_system_prompt()` 按以下层次构建：

```
1. 基础角色 + 工具表（内部工具 / 用户可见工具）
2. DataEase 数据集指导（如有 out_ds_instance）
3. 对话历史（如有 conversation_history）
4. 轮次摘要（如有 conversation_summary）
5. 已有查询/图表列表
6. 已探索表缓存提示
7. 追问模式指导（如 is_followup）
```

### 4.3 LLM 输出如何分发

在 `executor.py` 的 `_run_agent()` 中，每个 `AIMessage` 按内容拆解：

```
AIMessage.content
  ├── has tool_calls → emit("reasoning") → thinking 面板（探索过程）
  └── no tool_calls  → emit("text-delta") → 回复区（最终答案）

AIMessage.additional_kwargs.reasoning_content
  └── emit("reasoning") → thinking 面板（DeepSeek 原生思考）
```

### 4.4 终端触发后

终端工具（如 `create_sql_query`）成功时设置 `memory.terminal_triggered = True`。`graph.py` 的 `_route_fn` 检测到此标志 → 返回 `"end"` → 循环终止 → 进入 `_post_process()`。

**失败的终端调用不终止**：Agent 看到错误信息后可以修正 SQL 并重试，最多 `max_sql_retries`（默认 3）次。

---

## 5. SSE 事件类型与前端渲染

| 事件 type | 触发时机 | 前端渲染位置 |
|-----------|---------|-------------|
| `id` | 请求开始 | 更新 record.id |
| `question` | 同上 | 显示用户问题 |
| `reasoning` | LLM 思考过程 | 可折叠 thinking 面板 |
| `text-delta` | LLM 最终回答 | 回复区 Markdown 渲染 |
| `tool-call` | **仅 show_to_user=True 的工具** | 执行详情抽屉 |
| `tool-result` | **仅 show_to_user=True 的工具** | 执行详情抽屉 |
| `sql` | SQL 生成完成 | 代码高亮块 |
| `sql-data` | SQL 执行成功 | 触发前端 fetch 数据 |
| `chart-result` | 图表 LLM 流式生成 | 图表面板流式展示 |
| `chart` | 图表 JSON 已确定 | ChartBlock 渲染 |
| `execution-stats` | Agent 完成 | 执行统计面板 |
| `finish` | Agent 完成 | 隐藏 loading |
| `error` | 异常 | 错误提示 |
| `clarify` | ask_for_clarification | 追问卡片 |

**重点**：内部工具（`show_to_user=False`）不会产生 `tool-call`/`tool-result` SSE 事件，前端不可见。但所有工具的执行日志仍会记录在 `execution_log.tools` 中用于调试。

---

## 6. 终端工具 vs 非终端工具

### 终端工具（terminal=True）

成功后立刻终止 Agent 循环，不再继续探索。当前 5 个终端工具：

| 工具 | 为什么是终端 |
|------|------------|
| `create_sql_query` | 已生成最终 SQL，系统自动执行+出图 |
| `edit_sql_query` | 已修改 SQL，自动重新执行+出图 |
| `replace_sql_fragment` | 同上（单个替换） |
| `edit_chart` | 图表修改完成 |
| `ask_for_clarification` | 需要等待用户输入 |

### 非终端工具（terminal=False）

Agent 可以连续调用多个非终端工具完成探索：

```
典型 QA 流程:
  search_relevant_tables  ─┐
  get_table_metadata      ─┤  非终端，连续探索
  get_field_values        ─┤
  preview_sql             ─┘
  create_sql_query        ─── 终端，循环结束
```

---

## 7. 内部工具 vs 用户可见工具

这是 `show_to_user` 字段控制的维度，与 `terminal` 正交：

| | terminal=True | terminal=False |
|---|---|---|
| **show_to_user=True** | `create_sql_query`<br>`edit_sql_query`<br>`edit_chart`<br>`replace_sql_fragment`<br>`ask_for_clarification` | `create_chart` |
| **show_to_user=False** | （暂无） | `search_relevant_tables`<br>`get_table_metadata`<br>`get_table_sample_data`<br>`get_field_values`<br>`load_skill`<br>`execute_sql_query`<br>`analyze_query_result`<br>`get_data_summary`<br>`get_data_preview`<br>`search_web`<br>`preview_sql` |

- `terminal` 控制 **Agent 循环是否停止**
- `show_to_user` 控制 **工具事件是否推到前端 SSE**

两者独立，各管各的。

---

## 8. 后处理流程

Agent 循环结束后，`executor.py:_post_process()` 根据 profile 分支：

### execute_and_chart（QA）

```
1. get_latest_query() → 找到 status="created"/"edited" 的查询
2. apply_permissions() → 行级安全过滤
3. execute_sql_query() → 实际执行
4. emit("sql") → 前端显示 SQL
5. emit("sql-data") → 前端 fetch 数据
6. _generate_chart() → LLM 生成图表 JSON（最多 3 次自纠错）
7. emit("chart") → 前端渲染图表
```

### text_and_chart（analysis/predict）

```
1. _save_text_answer() → 保存分析/预测文本到 ChatRecord
2. 找到预注入的 r_base_* 查询（已有 data）
3. _generate_chart() → 在已有数据上生成图表
4. _update_chart_title() → 前缀"数据分析："或"数据预测："
```

### Chart-only 路径

当 `terminal_triggered` 且当前轮次创建了新图表（`_charts_this_turn`），直接 emit 图表而不重新执行 SQL。**不会**推送上一轮的旧图表。

---

## 9. 跨轮次内存

`AgentMemory` 持久化到 `Chat.memory_state`（JSONB 列），每次请求开始时恢复：

```
持久化字段：
  queries           — {record_id: {sql, compiled_sql, tables_used, status, row_count, ...}}
  charts            — {chart_ref: {chart_ref, record_id, chart_config}}
  explored_tables   — {table_name: {fields[], sql?, is_dataset}}
  conversation_summary — 累积轮次摘要

不持久化（运行时）：
  session, current_user, ds, out_ds_instance  — 每次重新注入
  _charts_this_turn, _search_cache            — 仅当前轮次有效
  terminal_triggered, sql_retry_count         — 每轮重置
```

**轮次摘要**（`_finalize_turn`）格式：
```
Q1: 本月销售额 | 涉及表: orders | 返回 342 行 | 查询已执行 | 生成column图表
Q2: 按产品分类 | 涉及表: orders, products | 返回 15 行 | 查询已执行
```

系统提示词注入这些摘要，让 Agent 知道之前的对话上下文。

---

## 10. 关键文件速查表

| 文件 | 改什么时看 |
|------|-----------|
| `engine.py` | 新增 Agent 角色、修改 profile 工具集、调整迭代次数 |
| `graph.py` | 修改系统提示词、调整路由逻辑 |
| `executor.py` | 修改 SSE 事件、后处理流程、图表生成策略 |
| `memory.py` | 修改 Agent 持久化状态、轮次摘要 |
| `tools/registry.py` | 修改 ToolDef 数据结构、注册表接口 |
| `tools/register_all.py` | **新增/删除/修改工具定义** |
| `tools/advanced_tools.py` | 新增内部工具（非 SQL/图表） |
| `tools/sql_tools.py` | 修改 SQL 创建/编辑逻辑 |
| `tools/chart_tools.py` | 修改图表创建/编辑逻辑 |
| `tools/schema_tools.py` | 修改表探索逻辑 |
| `tools/query_tools.py` | 修改 SQL 执行逻辑 |
| `adapter.py` | 修改内存持久化、对话历史构建 |
| `compiler.py` | 修改 DataEase 数据集 SQL 展开 |
| `chart_registry.py` | 新增/修改图表类型 |

---

## 附录：工具一览（17 个）

```
内部工具 (11, show_to_user=False):
  search_relevant_tables   — 语义搜索候选表
  get_table_metadata       — 获取表字段结构
  get_table_sample_data    — 获取 3 行样本
  get_field_values         — 获取字段去重值
  load_skill               — 加载 SQL 方言/业务知识
  preview_sql              — 自定义 SQL 内部验证 (NON-TERMINAL)
  execute_sql_query        — 执行已有查询记录
  analyze_query_result     — LLM 深度分析数据
  get_data_summary         — 字段统计摘要
  get_data_preview         — 分页查看数据
  search_web               — 互联网搜索（仅 Predict）

用户可见 (6, show_to_user=True):
  create_sql_query         — 创建最终查询 (TERMINAL)
  edit_sql_query           — 编辑查询 (TERMINAL)
  replace_sql_fragment     — 替换 SQL 片段 (TERMINAL)
  edit_chart               — 编辑图表 (TERMINAL)
  ask_for_clarification    — 向用户追问 (TERMINAL)
  create_chart             — 创建图表配置
```
