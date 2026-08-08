# SQLBot 旧管线代码清理分析

> 编写日期: 2026-07-22
> 状态: 分析完成，待评审
> 目的: 系统性梳理旧管线（Pipeline）代码的调用状态，区分死代码 / 保留 fallback / Agent 复用代码，为清理提供依据

---

## 一、架构概述

SQLBot 有两套引擎：

| | 旧管线 (Pipeline) | 新 Agent |
|---|---|---|
| **入口文件** | `apps/chat/task/llm.py` (103KB) | `apps/chat/agent/` (14 文件) |
| **架构** | 线性 RAG: Embedding 检索表 → Schema 全量注入 Prompt → LLM 单次生成 SQL → 执行 → 图表 | ReAct Agent: LLM + 13 工具迭代调用 (LangGraph) |
| **代码量** | ~1700 行 LLMService + 模板系统 | ~2500 行 agent/ |
| **默认引擎** | `engine="pipeline"` (旧默认，Phase 4 起废弃) | `engine="agent"` (当前默认) |

**关键事实**: `LLMService` 只被一个文件导入 — `apps/chat/api/chat.py:20`。整个后端没有其他模块引用旧管线。

---

## 二、旧管线六大入口及当前状态

### 2.1 SQL 问答 (主流程)

```
POST /chat/question → stream_sql() → run_task_async() → run_task() (~300行)
```

- **旧管线代码**: `LLMService.run_task()` + 12 个内部方法
- **Agent 替代**: `_stream_sql_agent()` → `stream_agent()` → `AgentExecutor`
- **当前状态**: ⚠️ 旧代码**保留为 fallback**，仅 `engine="pipeline"` 时触发（默认 `agent`，无人显式传参）

### 2.2 数据分析 (Analysis)

```
POST /chat/record/{id}/analysis → analysis_or_predict()
```

- **旧管线代码**: `LLMService.run_analysis_or_predict_task()` → `generate_analysis()`
- **Agent 替代**: `dispatch(build_analysis_profile(record), ...)` — 复用 Agent 引擎
- **当前状态**: ❌ **纯死代码**。chat.py:566-569 显式标记 `[DEPRECATED]`，旧代码已注释

### 2.3 数据预测 (Predict)

```
POST /chat/record/{id}/predict → analysis_or_predict()
```

- **旧管线代码**: `LLMService.run_analysis_or_predict_task()` → `generate_predict()`
- **Agent 替代**: `dispatch(build_predict_profile(record), ...)`
- **当前状态**: ❌ **纯死代码**。同上

### 2.4 推荐追问 (Recommend Questions)

```
POST /chat/recommend_questions/{id} → ask_recommend_questions()
```

- **旧管线代码**: `LLMService.run_recommend_questions_task()` → `generate_recommend_questions_task()`
- **Agent 替代**: `stream_agent_recommend()` (轻量 prompt, 无 Schema 嵌入)
- **当前状态**: ⚠️ 保留为 fallback，仅 `engine="pipeline"` 时触发

### 2.5 数据源选择 (内嵌于 SQL 问答)

- **旧管线代码**: `LLMService.select_datasource()` — Embedding 排序 + LLM 选择
- **Agent 替代**: Agent 初始化时直接使用 `chat.datasource`，无需 LLM 选择
- **当前状态**: ⚠️ 保留在 `run_task()` 内部，仅 fallback 路径触发

### 2.6 行权限过滤 (内嵌于 SQL 问答)

- **旧管线代码**: `LLMService.generate_filter()` → `build_table_filter()`
- **Agent 替代**: `permission_tools.py` → `apply_permissions()` 在后处理中执行
- **当前状态**: ⚠️ 保留在 `run_task()` 内部，仅 fallback 路径触发

---

## 三、LLMService 方法逐行调用分析

> 方法总计: **45 个** (含 `__init__`, `create`, 静态方法, 类方法)

### 3.1 被 Agent 复用的方法 (8 个 — 不可删除)

| # | 方法 | 调用方 | 用途 |
|---|------|--------|------|
| 1 | `__init__()` | `LLMService.create()` → agent 路径 | 数据源解析、LLM 配置、用户初始化 |
| 2 | `create()` (classmethod) | `chat.py:249,256,402,448,527` | 工厂方法，agent 和 pipeline 共用 |
| 3 | `init_record()` | `chat.py:404,450` | 创建 ChatRecord |
| 4 | `get_record()` | `adapter.py` (agent) | 获取 ChatRecord |
| 5 | `set_record()` | `chat.py:257,528` | 设置 ChatRecord |
| 6 | `is_running()` | adapter 状态检查 | 检查异步任务状态 |

Agent 代码还直接访问这些实例属性:
| 属性 | 访问方 |
|------|--------|
| `llm_service.ds` | `adapter.py:35`, `engine.py` |
| `llm_service.llm` | `adapter.py:155,202`, `engine.py:357` |
| `llm_service.current_user` | `adapter.py:46` |
| `llm_service.record` | `adapter.py:203` |
| `llm_service.chat_question` | `adapter.py:204`, `engine.py:359` |

### 3.2 纯死代码 (6 个 — 可安全删除)

这些方法仅被已注释的 `run_analysis_or_predict_task_async()` 调用，无任何外部引用:

| # | 方法 | 行号 | 说明 |
|---|------|------|------|
| 1 | `run_analysis_or_predict_task_async()` | L1509 | chat.py:568 已注释废弃 |
| 2 | `run_analysis_or_predict_task_cache()` | L1514 | 仅被 #1 调用 |
| 3 | `run_analysis_or_predict_task()` | L1518 | 仅被 #2 调用 |
| 4 | `generate_analysis()` | L408 | 仅被 #3 调用 |
| 5 | `generate_predict()` | L463 | 仅被 #3 调用 |
| 6 | `check_save_predict_data()` | L1084 | 仅被 #5 调用 |

### 3.3 Fallback 路径代码 (19 个 — 需确认无外部调用后删除)

仅在 `engine="pipeline"` 时触发（默认 `agent`，无用户显式传参）:

**SQL 问答主流程:**
| # | 方法 | 行号 | 调用链 |
|---|------|------|--------|
| 7 | `run_task_async()` | L1164 | chat.py:405 (仅 engine≠agent) |
| 8 | `run_task_cache()` | L1170 | 仅被 #7 |
| 9 | `run_task()` | L1175 | 仅被 #8 (~300行核心管线) |
| 10 | `init_messages()` | L223 | 仅被 #9 |
| 11 | `choose_table_schema()` | L384 | 仅被 #10 |
| 12 | `generate_sql()` | L728 | 仅被 #9 |
| 13 | `generate_chart()` | L901 | 仅被 #9 |
| 14 | `generate_with_sub_sql()` | L771 | 仅被 #15 |
| 15 | `generate_assistant_dynamic_sql()` | L820 | 仅被 #9 |
| 16 | `build_table_filter()` | L835 | 仅被 #17, #18 |
| 17 | `generate_filter()` | L884 | 仅被 #9 |
| 18 | `generate_assistant_filter()` | L891 | 仅被 #9 (未使用) |
| 19 | `check_sql()` | L943 | 仅被 #20, #9 |
| 20 | `check_save_sql()` | L1015 | 仅被 #9 |
| 21 | `check_save_chart()` | L1023 | 仅被 #9 |
| 22 | `save_error()` | L1098 | 仅被 #9, #3 |
| 23 | `save_sql_data()` | L1101 | 仅被 #9 |
| 24 | `finish()` | L1118 | 仅被 #9, #3 |

**推荐追问:**
| 25 | `run_recommend_questions_task_async()` | L1483 | chat.py:259 (仅 engine≠agent) |
| 26 | `run_recommend_questions_task_cache()` | L1486 | 仅被 #25 |
| 27 | `run_recommend_questions_task()` | L1490 | 仅被 #26 |
| 28 | `generate_recommend_questions_task()` | L514 | 仅被 #27 |
| 29 | `set_articles_number()` | L313 | 仅被 chat.py:258 (engine≠agent) |

**数据源选择:**
| 30 | `select_datasource()` | L583 | 仅被 #9 |
| 31 | `validate_history_ds()` | L1653 | 仅被 #9 |

**过滤/模板辅助:**
| 32 | `filter_terminology_template()` | L320 | 仅被 #9, #4, #5 |
| 33 | `filter_custom_prompts()` | L337 | 仅被 #9, #4, #5 |
| 34 | `filter_training_template()` | L358 | 仅被 #9 |

**聚合/输出辅助:**
| 35 | `execute_sql()` | L1121 | 仅被 #9 |
| 36 | `pop_chunk()` | L1143 | 仅被旧 SSE 流 |
| 37 | `await_result()` | L1150 | 仅被 chat.py:266,420 (engine≠agent) |
| 38 | `get_fields_from_chart()` | L316 | 仅被 #4, #5 |

### 3.4 仅内部使用的静态方法 (2 个)

| # | 方法 | 行号 | 说明 |
|---|------|------|------|
| 39 | `get_chart_type_from_sql_answer()` | L976 | 仅被 `run_task()` 调用 |
| 40 | `get_brief_from_sql_answer()` | L996 | 仅被 `run_task()` 调用 |

### 3.5 Standalone 函数 (6 个 — 全部仅内部使用)

| # | 函数 | 行号 | 外部调用? |
|---|------|------|-----------|
| 41 | `execute_sql_with_db()` | L1674 | ❌ 无 |
| 42 | `request_picture()` | L1700 | ❌ 无 (agent 用自己的图表渲染) |
| 43 | `get_token_usage()` | L1762 | ❌ 无 |
| 44 | `process_stream()` | L1774 | ❌ 无 |
| 45 | `get_lang_name()` | L1872 | ❌ 仅 `__init__` 调用 |
| 46 | `get_last_conversation_rounds()` | L1885 | ❌ 仅 `init_messages` 调用 |

---

## 四、`apps/template/` 模板系统调用分析

```
apps/template/
  template.py                          ← 核心模板加载器
  generate_sql/generator.py            ← get_sql_template(), get_sql_example_template()
  generate_chart/generator.py          ← get_chart_template()
  generate_analysis/generator.py       ← get_analysis_template()
  generate_predict/generator.py        ← get_predict_template()
  generate_dynamic/generator.py        ← get_dynamic_template()
  generate_guess_question/generator.py ← get_guess_question_template()
  select_datasource/generator.py       ← get_datasource_template()
  filter/generator.py                  ← get_permissions_template()
```

### 4.1 被 Agent 复用的模板 (2 个 — 不可删除)

| 模块 | 函数 | Agent 调用方 |
|------|------|-------------|
| `generate_chart/generator.py` | `get_chart_template()` | `executor.py:1069` |
| `generate_analysis/generator.py` | `get_analysis_template()` | `advanced_tools.py:105` |

### 4.2 仅旧管线使用的模板 (6 个 — 可随旧管线一起删除)

| 模块 | 函数 | 唯一调用方 |
|------|------|-----------|
| `generate_sql/generator.py` | `get_sql_template()`, `get_sql_example_template()` | `chat_model.py` → `LLMService.init_messages()` |
| `generate_predict/generator.py` | `get_predict_template()` | `chat_model.py` → `LLMService.generate_predict()` (已死) |
| `generate_dynamic/generator.py` | `get_dynamic_template()` | `chat_model.py` → `LLMService.generate_assistant_dynamic_sql()` |
| `generate_guess_question/generator.py` | `get_guess_question_template()` | `chat_model.py` → `LLMService.generate_recommend_questions_task()` |
| `select_datasource/generator.py` | `get_datasource_template()` | `chat_model.py` → `LLMService.select_datasource()` |
| `filter/generator.py` | `get_permissions_template()` | `chat_model.py` → `LLMService.generate_filter()` |

### 4.3 `template.py` 核心加载器

`template.py` 被上述所有 generator 依赖。如果只剩 chart + analysis 两个 generator，需要保留。但目前它是纯函数库，直接保留即可。

---

## 五、`chat_model.py` 模板方法分析

`ChatQuestion` 类上有约 20 个模板格式化方法 (如 `sql_user_question()`, `chart_sys_question()`, `analysis_sys_question()` 等)。

**关键发现**: Agent 代码**完全不使用** ChatQuestion 的任何模板方法。Agent 在 `graph.py` 中自己构建 System Prompt，在 `executor.py` 中直接调用 `get_chart_template()`。

| 方法 | 旧管线调用方 | Agent 调用? |
|------|------------|------------|
| `sql_sys_question()` | `init_messages()` | ❌ |
| `sql_user_question()` | `generate_sql()` | ❌ |
| `chart_sys_question()` | `init_messages()` | ❌ |
| `chart_user_question()` | `generate_chart()` | ❌ |
| `analysis_sys_question()` | `generate_analysis()` (已死) | ❌ |
| `analysis_user_question()` | `generate_analysis()` (已死) | ❌ |
| `predict_sys_question()` | `generate_predict()` (已死) | ❌ |
| `predict_user_question()` | `generate_predict()` (已死) | ❌ |
| `datasource_sys_question()` | `select_datasource()` | ❌ |
| `datasource_user_question()` | `select_datasource()` | ❌ |
| `guess_sys_question()` | `generate_recommend_questions_task()` | ❌ |
| `guess_user_question()` | `generate_recommend_questions_task()` | ❌ |
| `filter_sys_question()` | `generate_filter()` | ❌ |
| `filter_user_question()` | `generate_filter()` | ❌ |
| `dynamic_sys_question()` | `generate_assistant_dynamic_sql()` | ❌ |
| `dynamic_user_question()` | `generate_assistant_dynamic_sql()` | ❌ |

**结论**: 所有这些方法都可删除。但它们与 SQLModel 定义在同一个文件中，删除时需要小心保留模型类。

---

## 六、`apps/datasource/` 辅助模块复用分析

| 函数 | 位置 | 旧管线 | Agent |
|------|------|--------|-------|
| `get_table_schema()` | `crud/datasource.py:508` | ✅ `choose_table_schema()` | ❌ Agent 有自己的 `_build_table_schema_core/assistant` |
| `get_tables_sample_data()` | `crud/datasource.py:490` | ✅ | ✅ `schema_tools.py:341` |
| `calc_table_embedding()` | `embedding/table_embedding.py:43` | ✅ | ✅ `schema_tools.py:175,200` |
| `get_ds_embedding()` | `embedding/ds_embedding.py:18` | ✅ `select_datasource()` | ❌ |
| `get_row_permission_filters()` | `crud/permission.py` | ✅ | ✅ `permission_tools.py:51` |
| `is_normal_user()` | `crud/permission.py` | ✅ | ✅ `permission_tools.py:48` |

---

## 七、`agent-data-demo/` 目录

**状态**: ❌ **完全废弃，可整个删除**

这是早期 8-agent 流水线实验项目（Router→Schema→Metric→Entity→Segment→SQL→Checker→Explainer），与当前 SQLBot-main 的 ReAct Agent 架构完全不同。代码不被任何生产代码引用。

---

## 八、推荐清理策略

### Phase 1: 纯死代码（零风险，可立即删除）

**`llm.py` 中 6 个方法** (约 400 行):
- `run_analysis_or_predict_task_async()`
- `run_analysis_or_predict_task_cache()`
- `run_analysis_or_predict_task()`
- `generate_analysis()`
- `generate_predict()`
- `check_save_predict_data()`

**`chat.py` 中已注释代码** (L566-569):
- 旧 `run_analysis_or_predict_task_async` 调用注释

**`chat_model.py` 中对应模板方法** (可删除):
- `analysis_sys_question()`, `analysis_user_question()`
- `predict_sys_question()`, `predict_user_question()`

**模板模块** (可删除):
- `apps/template/generate_predict/`

**`agent-data-demo/` 整个目录**

### Phase 2: Fallback 路径代码（需确认无外部调用）

删除条件: 确认以下条件全部满足:
1. 没有 API 调用方显式传 `engine=pipeline`
2. 没有 MCP 调用方使用旧管线
3. 没有定时任务/后台任务使用旧管线
4. 前端没有硬编码 `engine=pipeline`

**`llm.py` 中约 35 个方法** (约 1300 行):
- `run_task_async()`, `run_task_cache()`, `run_task()` 及所有内部方法
- `run_recommend_questions_task_*` 系列
- `select_datasource()`, `validate_history_ds()`
- 所有 `filter_*`, `generate_*`, `check_*`, `save_*` 方法
- 所有 standalone 函数

**`chat.py` 中 fallback 分支**:
- `stream_sql()` L401-434 (engine≠agent 分支)
- `ask_recommend_questions()` L255-264 (engine≠agent 分支)

**`chat_model.py` 中旧模板方法** (约 16 个):
- 所有 `*_question()` 方法 (保留 SQLModel 类定义)

**模板模块** (6 个):
- `apps/template/generate_sql/`
- `apps/template/generate_dynamic/`
- `apps/template/generate_guess_question/`
- `apps/template/select_datasource/`
- `apps/template/filter/`
- `apps/template/template.py` (如果只剩 chart+analysis 则保留)

**`datasource/crud/datasource.py`**:
- `get_table_schema()` 函数 (Agent 有自己的实现)

### Phase 3: 重构 LLMService 为轻量 Factory

当前 Agent 只使用 `LLMService` 的 4 个属性 (`ds`, `llm`, `current_user`, `record`) 和 3 个方法 (`create()`, `init_record()`, `get_record()`)。

建议: 将 `LLMService` 重构为 ~100 行的 `AgentSessionFactory`，只保留数据源解析 + LLM 配置 + ChatRecord 创建，删除所有管线方法。

---

## 九、影响范围汇总

| 文件 | 当前行数 | 预计删除 | 预计保留 | 说明 |
|------|---------|---------|---------|------|
| `task/llm.py` | ~1885 | ~1700 | ~185 | Phase 3 重构为 Factory |
| `chat/api/chat.py` | 663 | ~40 | ~623 | 删除 fallback 分支 |
| `models/chat_model.py` | ~350 | ~90 | ~260 | 删除模板方法，保留模型 |
| `template/generate_sql/` | - | 全部 | 0 | 旧管线专用 |
| `template/generate_predict/` | - | 全部 | 0 | 已死代码 |
| `template/generate_dynamic/` | - | 全部 | 0 | 旧管线专用 |
| `template/generate_guess_question/` | - | 全部 | 0 | 旧管线专用 |
| `template/select_datasource/` | - | 全部 | 0 | 旧管线专用 |
| `template/filter/` | - | 全部 | 0 | 旧管线专用 |
| `template/generate_chart/` | - | 0 | 全部 | **Agent 复用** |
| `template/generate_analysis/` | - | 0 | 全部 | **Agent 复用** |
| `agent-data-demo/` | - | 全部 | 0 | 废弃实验项目 |
| **合计** | - | **~2100 行** | - | |

---

## 十、验证方法

在删除前，对每个待删除方法执行:

```bash
# 1. 确认无外部引用
grep -rn "method_name" backend/ --include="*.py" | grep -v "llm.py"

# 2. 确认测试不依赖
grep -rn "method_name" backend/tests/ --include="*.py"

# 3. 运行现有测试套件
cd SQLBot-main/backend && python -m pytest tests/ -v

# 4. 手动验证关键路径
# - POST /chat/question (QA)
# - POST /chat/record/{id}/analysis
# - POST /chat/record/{id}/predict
# - POST /chat/recommend_questions/{id}
```
