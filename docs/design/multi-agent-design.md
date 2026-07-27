# SQLBot 多 Agent 架构设计

> 编写日期: 2026-07-15
> 背景: 数据分析/预测/执行详情等功能仍使用旧线性 Pipeline，用户提出不应在单 Agent 上不断叠加职责

---

## 一、旧管线分析

### 1.1 旧管线架构

```
POST /api/v1/chat/record/{id}/analysis
  → analysis_or_predict() → LLMService.run_analysis_or_predict_task_async()
    → generate_analysis()
        1. get_chat_chart_data(record_id)        ← 从 DB 读取已有图表数据
        2. filter_terminology_template()          ← 匹配术语
        3. filter_custom_prompts()                ← 加载自定义提示词
        4. build: SystemMessage + HumanMessage    ← 拼 prompt
        5. llm.stream(messages)                   ← 一次 LLM 调用
        6. save_analysis_answer(record_id, text)  ← 保存到 ChatRecord.analysis
```

**核心特征**：线性流程，1 次 LLM 调用，无工具，无循环。

### 1.2 SSE 事件格式对比

| 事件 | 旧 Analysis | 旧 Predict | Agent 主问答 |
|------|-----------|-----------|-------------|
| 流式文本 | `analysis-result` | `predict-result` | `text-delta` |
| 完成 | `analysis_finish` | `predict_finish` | `finish` |
| 数据 | 嵌入 prompt | 嵌入 prompt | `sql-data` + DB fetch |
| 图表 | LLM 自由生成 | — | `chart` |
| 工具调用 | — | — | `tool-call` / `tool-result` |
| SQL | — | — | `sql` |

**前端组件各自绑定自己的事件格式**：
- `ChartAnswer.vue` → `text-delta`, `tool-call`, `chart`, `sql-data`, `finish`
- `AnalysisAnswer.vue` → `analysis-result`, `analysis_finish`
- `PredictAnswer.vue` → `predict-result`, `predict_finish`

### 1.3 为什么不能"把分析加到主 Agent 上"

| 主 QA Agent | Analysis Agent |
|---|---|
| 从 0 开始探索表结构 | 数据已经有了（base_record） |
| 需要 search → metadata → sample | 不需要探索 |
| 需要 create_sql_query | SQL 已有，不需要生成 |
| 终端工具触发后处理 | 没有终端工具 |
| 输出图表 + 数据 | 输出文本分析 + 可选图表 |
| 10+ 轮迭代 | 1 轮思考就够了 |
| SSE: `chart` + `finish` | SSE: `analysis-result` + `analysis_finish` |

如果在主 Agent 里加 if/else 分支来区分这两种模式，会变成：

```python
if self.mode == "qa":
    await self._execute_and_chart(latest)
elif self.mode == "analysis":
    await self._emit_analysis_result()
elif self.mode == "predict":
    await self._emit_predict_result()
```

每一个新功能都加一个分支 → 不可维护。

## 二、多 Agent 架构设计

### 2.1 原则

1. **每个 Agent 职责单一**，不互相耦合
2. **共享底层能力**（LLM、工具注册、Memory 持久化），不重复造轮子
3. **前端组件按事件类型匹配 Agent**，不改现有组件的事件监听
4. **API 路由按 profile 分发**，不修改现有端点签名

### 2.2 Agent 定义

```
┌──────────────────────────────────────────────────────────┐
│                   AgentEngine                            │
│                                                          │
│  dispatch(profile, question, memory) → SSE stream        │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ QA Agent    │  │ Analysis     │  │ Predict Agent  │  │
│  │             │  │ Agent        │  │                │  │
│  │ 13 tools    │  │ 1 tool       │  │ 1-2 tools      │  │
│  │ max 50 iter │  │ max 5 iter   │  │ max 10 iter    │  │
│  │ execute+    │  │ text output  │  │ chart output   │  │
│  │ chart       │  │              │  │                │  │
│  └─────────────┘  └──────────────┘  └────────────────┘  │
│                                                          │
│  共享: llm, memory, session, graph, tool_registry        │
└──────────────────────────────────────────────────────────┘
```

### 2.3 三个 Agent 的职责边界

| | QA Agent | Analysis Agent | Predict Agent |
|---|---|---|---|
| **触发** | 用户输入框发送问题 | 点击图表"数据分析"按钮 | 点击图表"数据预测"按钮 |
| **输入** | 用户自然语言问题 | base_record（已有 chart + data + SQL） | base_record |
| **做什么** | 探索表 → 生成 SQL → 执行 → 图表 | 分析已有查询结果，产出数据洞察 | 基于历史数据预测未来趋势 |
| **工具** | 全部 13 工具 | `analyze_query_result` | `load_skill`, `analyze_query_result`, `predict_data`(新) |
| **输出** | SQL + data + chart | 文本分析 + 可选图表 | 预测数据 + 图表 |
| **SSE 事件** | `text-delta`, `sql`, `chart` | `analysis-result`(兼容) 或 `text-delta` | `predict-result`(兼容) 或 `text-delta` |
| **迭代上限** | 50 | 5 | 10 |
| **后处理** | execute + chart | 保存分析文本 | 生成预测图表 |

### 2.4 路由分发

```
POST /api/v1/chat/question
  → engine="agent"
  → AgentEngine.dispatch(profile="qa", ...)

POST /api/v1/chat/record/{id}/analysis
  → engine="agent"  (改为 agent)
  → AgentEngine.dispatch(profile="analysis", base_record=record, ...)

POST /api/v1/chat/record/{id}/predict
  → engine="agent"  (改为 agent)
  → AgentEngine.dispatch(profile="predict", base_record=record, ...)
```

### 2.5 代码结构

```
backend/apps/chat/agent/
  engine.py              ← AgentEngine.dispatch() — 统一入口
  profiles/
    __init__.py
    qa.py                ← build_qa_profile() → system_prompt, tools, max_iter, post_process
    analysis.py          ← build_analysis_profile() → system_prompt, tools, max_iter, post_process
    predict.py           ← build_predict_profile() → system_prompt, tools, max_iter, post_process
  executor.py            ← AgentExecutor — 接收 profile，运行 agent 循环
  memory.py              ← 共享，不变
  graph.py               ← 共享，不变（graph 由 profile 决定 system_prompt）
  tools/                 ← 共享工具注册，各 profile 按需选用
```

### 2.6 Agent Profile 数据结构

```python
@dataclass
class AgentProfile:
    name: str                          # "qa" | "analysis" | "predict"
    system_prompt: str                 # 专用 System Prompt
    tools: list[str]                   # 允许的工具名列表（从 ToolRegistry 中筛选）
    terminal_tools: list[str]          # 终端工具
    max_iterations: int                # 最大迭代数
    post_process: str                  # "execute_and_chart" | "text_only" | "chart_from_data"
    base_record: Optional[ChatRecord]  # 分析/预测时的基础 record
```

## 三、前端适配方案

### 3.1 统一事件格式（推荐）

将所有 Agent 的 SSE 事件统一为 Agent 主问答格式：

| 旧事件 | 新事件 |
|--------|--------|
| `analysis-result` | `text-delta` |
| `analysis_finish` | `finish` |
| `predict-result` | `text-delta` |
| `predict_finish` | `finish` |

- `AnalysisAnswer.vue` → 改为监听 `text-delta` + `finish`（和 ChartAnswer 一致）
- `PredictAnswer.vue` → 同上

### 3.2 保持兼容（备选）

Agent 按 profile 发出对应的事件名：
- QA → 保持现有
- Analysis → 发 `analysis-result`（前端不变）
- Predict → 发 `predict-result`（前端不变）

**缺点**: Agent 内部要 if/else 决定事件名，违背解耦原则。

### 3.3 推荐方案：统一 + 前端适配

改前端 `AnalysisAnswer.vue` 和 `PredictAnswer.vue` 的 SSE 监听事件名，从旧格式改为 Agent 统一格式。改动量很小（改几个 case 字符串），之后所有 Agent 都发同一套事件。

## 四、迁移路径

### Phase 1: 抽 AgentProfile + 重构路由（不改变行为）

1. 创建 `agent/profiles/qa.py` → 抽取当前 system_prompt + 工具配置
2. 创建 `agent/engine.py` → `dispatch(profile, ...)` 统一入口
3. 验证 QA 行为不变

### Phase 2: 数据分析 Agent（替代旧管线）

1. 创建 `agent/profiles/analysis.py` → 专用 prompt + `analyze_query_result` 工具
2. 改 `POST /chat/record/{id}/analysis` → `dispatch(profile="analysis", base_record=record)`
3. 前端 `AnalysisAnswer.vue` → 改为监听 `text-delta` + `finish`
4. 验证分析结果展示正常，删除旧 `generate_analysis()`

### Phase 3: 数据预测 Agent

1. 新增 `predict_data` 工具
2. 创建 `agent/profiles/predict.py`
3. 改路由 + 前端

### Phase 4: 清理

1. 删除 `LLMService.run_analysis_or_predict_task_async()` 及相关方法
2. 删除旧模板 `generate_analysis/generate_predict`
3. 提取 `AnalysisAnswer` / `PredictAnswer` 公共逻辑

## 五、与 Metabase Profile 的对比

| | Metabase | SQLBot 设计 |
|---|---|---|
| Profile 定义 | `profiles.clj` 8 个 profile | `profiles/*.py` 3 个 profile |
| 工具集合 | profile.tools — set of tool names | 同上 |
| 终端工具 | profile.terminal-tools | 同上 |
| Prompt 模板 | Selmer 模板（`sql-querying-only.selmer` 等） | Python f-string |
| 路由 | `/api/metabot/agent-streaming` + `profile_id` | `engine.py dispatch(profile, ...)` |
| 前端 | 统一 `MetabotChat` 组件 | 当前 3 个组件，计划统一事件格式 |

> 目标：每种 Agent 是一个独立 profile，共享底层能力（LLM/ToolRegistry/Memory），互不耦合。新加功能 = 新增 profile 文件，不改现有代码。
