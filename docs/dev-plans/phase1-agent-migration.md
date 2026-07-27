# 阶段1 开发文档：分析/预测功能迁移至 Agent 引擎

> 编写日期: 2026-07-15
> 状态: 待实施
> 策略: 新增两个轻量 Agent（分析/预测），QA Agent 不动，旧管线代码仅标记不删除

---

## 一、目标

把"数据分析"和"数据预测"两个按钮从旧线性 Pipeline 迁移到 Agent 引擎。QA Agent（主问答）不动。改完之后三条线共享同一套 Agent 架构。

### 1.1 改前 vs 改后

```
改前:
  POST /chat/record/{id}/analysis ─→ llm.py 旧管线（独立的线性代码）
  POST /chat/record/{id}/predict  ─→ 同上，action_type 区分
  POST /chat/question              ─→ Agent 引擎（QA）

改后:
  POST /chat/record/{id}/analysis ─→ Agent 引擎（新增分析 Agent）
  POST /chat/record/{id}/predict  ─→ Agent 引擎（新增预测 Agent）
  POST /chat/question              ─→ Agent 引擎（QA，不动）
```

### 1.2 不做的事

- QA Agent 不改
- DataEase 不改
- 旧代码不删（第四节约定的删除范围只在本文档标记，Phase 2 执行）
- 执行详情按钮不在此范围（独立问题）
- 推荐追问不在此范围（已支持 engine="agent"）
- 图表架构优化不在此范围（Phase 2）

---

## 二、架构设计

### 2.1 三个 Agent 配置对比

| | QA（不改） | 分析（新增） | 预测（新增） |
|---|---|---|---|
| 触发 | 用户在输入框发问 | 点图表下方"数据分析" | 点图表下方"数据预测" |
| 输入 | 自然语言问题 | 已有 SQL + 图表 + 数据 | 已有 SQL + 图表 + 数据 |
| 任务 | 探索表→写SQL→执行→画图 | 数据已就绪，直接解读 | 数据已就绪，预测趋势 |
| 工具数量 | 13 个 | 0-1 个（`analyze_query_result`，可选） | 同分析 |
| 最大迭代 | 50 | 5 | 5 |
| 后处理 | 执行 SQL + 生成图表 | 仅保存文本 | 仅保存文本 |
| System Prompt | 查表→写SQL→终端工具 | 数据已在 prompt 中，直接分析 | 同分析，预测视角 |

### 2.2 新增数据结构

```python
# agent/engine.py

@dataclass
class AgentProfile:
    """Agent 角色定义。"""
    name: str                    # "qa" | "analysis" | "predict"
    system_prompt: str           # 专属 System Prompt
    tool_names: list[str]        # 允许的工具名列表（空列表 = 不绑工具，纯文本输出）
    terminal_tools: list[str]    # 哪些工具成功后终止循环
    max_iterations: int          # 最大迭代次数
    post_process: str            # "execute_and_chart" | "text_only"
```

### 2.3 分析/预测 Agent 的执行流程

```
1. api/chat.py: analysis_or_predict()
   → 从 DB 取出 ChatRecord（含 SQL、chart、data）

2. engine.py: dispatch(profile, llm_service, session)
   → 创建 AgentMemory（注入 base_record 数据）
   → 用 profile 创建 AgentExecutor
   → 运行 Agent 循环

3. Agent 循环:
   → LLM 收到 System Prompt（数据已在其中）
   → LLM 直接输出分析/预测文本（无需调用工具）
   → 流式输出 text-delta 事件到前端

4. 前端:
   → AnalysisAnswer.vue / PredictAnswer.vue
   → 监听 text-delta（流式文本）+ finish（结束）
```

---

## 三、文件变更清单

### 3.1 新增文件（1 个）

| 文件 | 说明 |
|------|------|
| `backend/apps/chat/agent/engine.py` | AgentProfile 定义 + Profile 工厂 + dispatch 入口 |

### 3.2 修改文件（5 个）

| 文件 | 说明 | 影响 QA? |
|------|------|----------|
| `backend/apps/chat/agent/executor.py` | 构造函数接收可选 `profile` 参数 | 否 |
| `backend/apps/chat/agent/graph.py` | `build_agent_graph()` 支持工具过滤 + System Prompt 覆盖 | 否 |
| `backend/apps/chat/api/chat.py` | `analysis_or_predict()` 改为走 Agent dispatch | 否 |
| `frontend/src/views/chat/answer/AnalysisAnswer.vue` | SSE 事件名改为 `text-delta` + `finish` | — |
| `frontend/src/views/chat/answer/PredictAnswer.vue` | SSE 事件名改为 `text-delta` + `finish` | — |

### 3.3 旧代码标记删除（不执行）

| 文件 | 标记范围 | 说明 |
|------|---------|------|
| `backend/apps/chat/task/llm.py` | `run_analysis_or_predict_task_async()` (~L1502) | 分析/预测旧管线入口 |
| `backend/apps/chat/task/llm.py` | `run_analysis_or_predict_task_cache()` (~L1507) | 线程池缓存方法 |
| `backend/apps/chat/task/llm.py` | `run_analysis_or_predict_task()` (~L1511) | 旧管线主逻辑 |
| `backend/apps/chat/task/llm.py` | `generate_analysis()` (~L408) | 分析 prompt 拼接 + LLM 调用 |
| `backend/apps/chat/task/llm.py` | `generate_predict()` (~L463) | 预测 prompt 拼接 + LLM 调用 |
| `backend/apps/chat/task/llm.py` | `generate_recommend_questions_task()` (~L514) | 推荐追问旧管线 |
| `backend/apps/chat/task/llm.py` | `run_recommend_questions_task_async()` (~L1476) | 推荐追问旧管线入口 |
| `backend/templates/generate_analysis/` | 整个目录 | 分析旧模板 |
| `backend/templates/generate_predict/` | 整个目录 | 预测旧模板 |
| `backend/apps/chat/api/chat.py` | `engine="pipeline"` 分支 (~L389-427) | 旧主问答回退（灰度期后删） |

---

## 四、详细实现说明

### 4.1 `agent/engine.py`（新增）

#### 4.1.1 AgentProfile 数据类

```python
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class AgentProfile:
    name: str                              # "qa" | "analysis" | "predict"
    system_prompt: str                     # 专属 System Prompt
    tool_names: list[str] = field(default_factory=list)
    terminal_tools: list[str] = field(default_factory=list)
    max_iterations: int = 50
    post_process: str = "execute_and_chart"  # "execute_and_chart" | "text_only"
```

#### 4.1.2 三个 Profile 工厂函数

```python
def build_qa_profile(memory) -> AgentProfile:
    """QA Agent（当前行为，不改）。"""
    from apps.chat.agent.tools.register_all import register_all_tools

    register_all_tools()
    return AgentProfile(
        name="qa",
        system_prompt="",              # 空 = 由 graph.py 的 _build_system_prompt() 自动构建
        tool_names=[],                 # 空 = 使用全部注册工具
        terminal_tools=["create_sql_query", "edit_sql_query", "edit_chart",
                        "replace_sql_fragment", "ask_for_clarification"],
        max_iterations=50,
        post_process="execute_and_chart",
    )


def build_analysis_profile(base_record) -> AgentProfile:
    """分析 Agent：数据已就绪，直接解读。"""
    prompt = _build_analysis_or_predict_prompt(base_record, mode="analysis")
    return AgentProfile(
        name="analysis",
        system_prompt=prompt,
        tool_names=["analyze_query_result"],  # 保留一个工具，LLM 需要深入分析时可以调用
        terminal_tools=[],
        max_iterations=5,
        post_process="text_only",
    )


def build_predict_profile(base_record) -> AgentProfile:
    """预测 Agent：数据已就绪，预测趋势。"""
    prompt = _build_analysis_or_predict_prompt(base_record, mode="predict")
    return AgentProfile(
        name="predict",
        system_prompt=prompt,
        tool_names=["analyze_query_result"],
        terminal_tools=[],
        max_iterations=5,
        post_process="text_only",
    )
```

#### 4.1.3 System Prompt 构建（分析/预测共用逻辑）

```python
def _build_analysis_or_predict_prompt(base_record, mode: str) -> str:
    """为分析/预测 Agent 构建 System Prompt，将数据直接注入。

    base_record: ChatRecord（含 SQL、chart、data 字段）
    mode: "analysis" | "predict"
    """
    import orjson

    # 提取字段和数据
    data = base_record.data or {}
    fields = data.get("fields", []) if isinstance(data, dict) else []
    rows = data.get("data", []) if isinstance(data, dict) else []
    sql = getattr(base_record, "sql", "") or ""
    chart = getattr(base_record, "chart", None)

    # 解析图表类型
    chart_type = "table"
    if chart:
        try:
            chart_obj = orjson.loads(chart) if isinstance(chart, str) else chart
            chart_type = chart_obj.get("type", "table") if isinstance(chart_obj, dict) else "table"
        except Exception:
            pass

    # 格式化数据（最多 50 行）
    rows_preview = rows[:50] if rows else []
    data_str = str(rows_preview) if rows_preview else "（无数据）"
    fields_str = str(fields) if fields else "（无字段信息）"

    if mode == "analysis":
        role = "数据分析师"
        task = """请分析以下数据：
1. **整体概况**：数据量、时间范围、关键指标汇总
2. **特征与趋势**：数据呈现的规律、趋势、分布特征
3. **异常发现**：数据中的异常值、突变点、不合理之处
4. **业务建议**：基于数据洞察，给出可执行的业务建议"""
    else:  # predict
        role = "数据预测师"
        task = """请基于以下历史数据预测未来趋势：
1. **历史趋势**：数据体现的历史变化规律
2. **未来预测**：基于趋势的延展预测（说明预测方法和假设）
3. **关键拐点**：可能的转折点或风险信号
4. **置信度**：预测的可信程度和前提条件"""

    return f"""你是{role}。用户已经执行了 SQL 查询并得到了结果，你的任务是基于这些结果提供专业的数据解读。

## 已有查询信息

- SQL: {sql[:500]}
- 图表类型: {chart_type}
- 字段: {fields_str}

## 数据（前 50 行）

{data_str}

## 任务

{task}

## 输出要求

- 如果数据为空，明确告知用户"查询结果为空，无法分析"
- 使用 Markdown 格式输出，层次清晰
- 关键数字用粗体标注
- 可以使用 `analyze_query_result` 工具对特定维度深入分析"""
```

#### 4.1.4 dispatch 统一入口

```python
async def dispatch(
    profile: AgentProfile,
    llm_service,
    session,
    question: str = "",
    base_record = None,
) -> str:
    """Agent 统一入口。yield SSE 事件。

    分析/预测场景：base_record 非空，从中提取数据注入 Memory。
    QA 场景：base_record 为 None，走正常探索流程。
    """
    from apps.chat.agent.memory import AgentMemory, QueryRecord
    from apps.chat.agent.executor import AgentExecutor
    from apps.chat.agent.adapter import init_agent_memory

    # 构建 Memory
    memory = init_agent_memory(llm_service)
    memory.session = session
    memory.user_question = question
    memory.record = getattr(llm_service, "record", None)

    # 分析/预测场景：把已有数据注入 Memory
    if base_record:
        rid = f"r_base_{base_record.id}"
        data = base_record.data or {}
        memory.queries[rid] = QueryRecord(
            record_id=rid,
            sql=getattr(base_record, "sql", "") or "",
            compiled_sql=getattr(base_record, "sql", "") or "",
            status="executed",
            data=data,
            row_count=len(data.get("data", [])) if isinstance(data, dict) else 0,
            result_fields=data.get("fields", []) if isinstance(data, dict) else [],
        )

    # 运行 Agent
    import asyncio
    queue: asyncio.Queue = asyncio.Queue()
    executor = AgentExecutor(llm_service.llm, memory, queue, profile=profile)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, executor.run)

    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk
```

---

### 4.2 `agent/executor.py`（修改）

#### 4.2.1 构造函数增加 profile 参数

修改 `AgentExecutor.__init__`，增加可选 `profile` 参数，默认 QA：

```python
class AgentExecutor:
    def __init__(self, llm, memory: AgentMemory, queue: asyncio.Queue,
                 profile: "AgentProfile | None" = None):
        self.llm = llm
        self.memory = memory
        self._queue = queue
        self.iter_count = 0

        # 默认 QA profile
        if profile is None:
            from apps.chat.agent.engine import build_qa_profile
            profile = build_qa_profile(memory)

        self.profile = profile
        self.graph = build_agent_graph(llm, memory, profile=profile)
```

#### 4.2.2 _post_process 按 profile 分派

```python
async def _post_process(self) -> None:
    if self.profile.post_process == "text_only":
        # 分析/预测：不执行 SQL，不生成图表
        # 但需要保存输出文本到 ChatRecord，保证历史可回看
        self._save_text_answer()
        _log.info(f"[Agent] END ({self.profile.name}) text-only, "
                  f"iterations={self.iter_count}")
        return

    # QA 默认：执行 SQL + 生成图表（现有逻辑不变）
    latest = self.memory.get_latest_query()
    if latest and latest.status in ("created", "edited"):
        ...
```

`_save_text_answer()` 是新增方法：从 Agent 流式输出中收集的文本内容，保存到 ChatRecord 对应的字段，确保用户点击左侧历史时能回看分析/预测结果。旧管线是存在 `ChatRecord.analysis` 字段的（通过 `save_analysis_answer()`），Agent 需要等价替换这个保存逻辑。

当前 `_post_process` 在第 247 行。修改方式：在开头加判断，如果 `text_only` 就直接 return。QA 的 `execute_and_chart` 保持现有逻辑不变。

---

### 4.3 `agent/graph.py`（修改）

#### 4.3.1 build_agent_graph 支持工具过滤

```python
def build_agent_graph(llm, memory: AgentMemory, profile=None):
    # 确定工具 Schema
    if profile and profile.tool_names:
        # 分析/预测：只暴露指定的工具
        all_schemas = ToolRegistry.get_openai_schemas()
        schemas = [s for s in all_schemas
                   if s["function"]["name"] in profile.tool_names]
    else:
        # QA：全部工具
        schemas = ToolRegistry.get_openai_schemas()

    # 构建图（其余不变）
    ...
```

对应的，`make_agent_node` 需要改为接收 `schemas` 参数而非直接调 `ToolRegistry.get_openai_schemas()`：

```python
def make_agent_node(llm, memory: AgentMemory, schemas: list[dict]):
    llm_with_tools = llm.bind_tools(schemas) if schemas else llm
    ...
```

#### 4.3.2 System Prompt 覆盖

```python
def _build_system_prompt(memory, is_followup=False, profile=None):
    # 如果 profile 提供了 System Prompt，直接使用
    if profile and profile.system_prompt:
        return profile.system_prompt

    # QA：现有逻辑不变
    ds_type = memory.datasource_type or "unknown"
    ...
```

#### 4.3.3 ToolRegistry 增加按名过滤方法

```python
# agent/tools/registry.py

@classmethod
def get_openai_schemas_for(cls, names: list[str]) -> list[dict]:
    """返回指定名称的工具 Schema。"""
    return [t.to_openai_schema() for name in names
            if (t := cls._tools.get(name))]
```

---

### 4.4 `api/chat.py`（修改）

`analysis_or_predict` 函数（当前第 482 行）：

```python
# ── 修改前（第 507-510 行）──
# request_question = ChatQuestion(chat_id=record.chat_id, question=record.question)
# llm_service = await LLMService.create(session, current_user, request_question, current_assistant)
# llm_service.run_analysis_or_predict_task_async(session, action_type, record, in_chat, stream)

# ── 修改后 ──
async def analysis_or_predict(session, current_user, chat_record_id, action_type,
                              current_assistant, in_chat=True, stream=True):
    try:
        if action_type not in ('analysis', 'predict'):
            raise Exception(f"Type {action_type} Not Found")

        # ── 1. 从 DB 取出 base_record ──
        record = _fetch_record(session, chat_record_id)  # 提取现有查询逻辑
        if not record:
            raise Exception(f"Chat record with id {chat_record_id} not found")
        if not record.chart:
            raise Exception("尚未生成图表，不支持分析")

        # ── 2. 创建 LLMService（复用 datasource 解析逻辑）──
        request_question = ChatQuestion(chat_id=record.chat_id, question=record.question)
        llm_service = await LLMService.create(session, current_user, request_question,
                                               current_assistant)

        # ── 3. 构建 Profile ──
        from apps.chat.agent.engine import (
            dispatch,
            build_analysis_profile,
            build_predict_profile,
        )
        if action_type == "analysis":
            profile = build_analysis_profile(record)
        else:
            profile = build_predict_profile(record)

        # ── 4. 走 Agent 分发 ──
        question = record.question or ""
        return StreamingResponse(
            dispatch(profile, llm_service, session,
                     question=question, base_record=record),
            media_type="text/event-stream",
        )

    except Exception as e:
        traceback.print_exc()
        if stream:
            def _err(_e):
                yield 'data:' + orjson.dumps({
                    'content': str(_e), 'type': 'error'
                }).decode() + '\n\n'
            return StreamingResponse(_err(e), media_type="text/event-stream")
        else:
            return JSONResponse(content={'message': str(e)}, status_code=500)
```

原 `_fetch_record` 逻辑直接从现有代码（第 489-498 行）提取，不变。

---

### 4.5 `AnalysisAnswer.vue`（修改）

#### 4.5.1 事件格式变化

后端改为 Agent 引擎后，SSE 事件会从旧格式变为 Agent 统一格式。前端需要适配的是**事件名和事件结构**。

| 维度 | 旧格式（废弃） | 新格式（Agent 统一） |
|------|-------------|-------------------|
| 流式文本 | `{"type": "analysis-result", "content": "...", "reasoning_content": "..."}` | `{"type": "text-delta", "content": "..."}` |
| 完成信号 | `{"type": "analysis_finish"}` | `{"type": "finish"}` |
| 记录 ID | 无（旧管线不通过 SSE 返回 id） | `{"type": "id", "id": 123}` |
| 工具调用 | 无 | `{"type": "tool-call", "tool_name": "analyze_query_result", "args": {...}}` |
| 工具结果 | 无 | `{"type": "tool-result", "tool_name": "...", "success": true, "summary": "..."}` |
| 错误 | `{"type": "error", "content": "..."}` | `{"type": "error", "content": "..."}`（格式一致） |

#### 4.5.2 需要改的代码逻辑

对应的 SSE 事件处理分支（以 `AnalysisAnswer.vue` 为例）：

```javascript
// ── 改前（监听旧事件名）──
switch (event.type) {
  case 'analysis-result':
    // 追加流式文本
    this.analysisText += event.content
    break
  case 'analysis_finish':
    // 结束加载状态
    this.loading = false
    break
}

// ── 改后（监听 Agent 统一事件名）──
switch (event.type) {
  case 'id':
    // 保存 record_id，用于历史回看
    this.recordId = event.id
    break
  case 'text-delta':
    // 追加流式文本（字段名从 content 取值，与旧格式一致）
    this.analysisText += event.content
    break
  case 'tool-call':
    // Agent 正在调用工具进行分析，有需要可以显示进度提示
    break
  case 'tool-result':
    // 工具调用完成，可选择展示工具摘要
    break
  case 'finish':
    // 结束加载状态
    this.loading = false
    break
  case 'error':
    // 显示错误信息
    this.errorMsg = event.content
    this.loading = false
    break
}
```

#### 4.5.3 显示完整性检查清单

改动完成后需要确认以下场景：

- [ ] 流式文本正常逐字输出（text-delta 逐块追加）
- [ ] Agent 思考/调用工具时，分析区域有合理的加载状态（finish 事件到达前不关闭 loading）
- [ ] 分析文本中包含 Markdown 格式（表格、列表、粗体）能正确渲染
- [ ] 分析结果为空时显示"查询结果为空，无法分析"（后端 prompt 已包含此逻辑）
- [ ] 请求失败时显示错误信息（监听 error 事件）
- [ ] 左侧历史面板中，分析结果的标题正确显示（不再显示日期格式，已通过 `_update_chat_brief()` 修复）

#### 4.5.4 参考

QA 的 `ChartAnswer.vue` 已经完整适配了 Agent 统一事件格式（text-delta / finish / tool-call / tool-result / sql / chart），分析组件的事件处理逻辑可以参照 `ChartAnswer.vue` 中对应部分实现。

---

### 4.6 `PredictAnswer.vue`（修改）

与 `AnalysisAnswer.vue` 的改动完全一致：将 `predict-result` → `text-delta`，`predict_finish` → `finish`，增加 `id` 和 `error` 事件处理。预测结果也是纯文本 + Markdown 渲染，没有图表依赖。

#### 4.6.1 事件格式变化

| 维度 | 旧格式（废弃） | 新格式（Agent 统一） |
|------|-------------|-------------------|
| 流式文本 | `{"type": "predict-result", "content": "...", "reasoning_content": "..."}` | `{"type": "text-delta", "content": "..."}` |
| 完成信号 | `{"type": "predict_finish"}` | `{"type": "finish"}` |

#### 4.6.2 显示完整性检查清单

- [ ] 预测文本流式输出正常
- [ ] Markdown 格式正确渲染
- [ ] 空数据、错误状态有对应提示
- [ ] 旧预测记录在历史中仍可正常回看（旧记录存的是旧格式数据，回看不走 SSE，不受影响）

---

## 五、测试验证

### 5.1 分析功能验证

1. 在 QA 对话中正常提问，生成图表
2. 点击图表下方"数据分析"按钮
3. 预期结果：
   - 前端收到 `text-delta` 流式文本（不再是 `analysis-result`）
   - 分析内容正确显示
   - 收到 `finish` 事件后加载状态结束
   - 分析结果在左侧历史中可回看

### 5.2 预测功能验证

1. 在 QA 对话中正常提问，生成含数值数据的图表
2. 点击图表下方"数据预测"按钮
3. 预期结果：同分析，但内容是预测视角

### 5.3 QA 回归验证

1. 正常发起问答
2. 追问（改图表类型、改筛选条件）
3. 预期：行为完全不变

---

## 六、风险与回退

### 6.1 风险

| 风险 | 概率 | 缓解 |
|------|------|------|
| 分析/预测 Agent prompt 质量不如旧管线 | 中 | prompt 可调；旧代码不删，随时可切回 |
| 前端改事件名后 QA 的 text-delta 和分析的 text-delta 混在一起 | 低 | 两个组件各自独立，不会互相干扰 |
| LLM 不调用 analyze_query_result 工具直接输出 | 低 | 不绑工具时纯文本输出正是预期行为 |

### 6.2 回退

如果分析/预测 Agent 结果不如预期：

1. 恢复 `chat.py` 中 `analysis_or_predict()` 的旧代码（已保留，仅注释）
2. 恢复前端事件名
3. QA 完全不受影响

---

## 七、后续计划（Phase 2+）

1. 验证分析/预测结果稳定后，删除旧代码（按 3.3 节标记范围）
2. 执行详情按钮：前端改读 tool_calls_log
3. 推荐追问：确认默认 engine 值
4. 图表架构优化：Chart LLM 裁剪、坐标轴修复、图表类型扩充
5. 预测功能增强：独立的 predict_data 工具（时间序列模型）
6. 语义层机械展开
7. 术语表集成

---

> **审核人**: ________
> **审核日期**: ________
> **开始实施日期**: ________
