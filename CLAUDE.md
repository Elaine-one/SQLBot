# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作提供指导。

## 项目概述

SQLBot 是一款基于大语言模型和 RAG 的开源 ChatBI（对话式数据分析）系统。用户用自然语言提问，系统自动探索数据库 Schema、生成 SQL、执行查询并渲染图表。支持多租户工作空间、DataEase 数据集集成、MCP Server 嵌入。

- **后端**：Python 3.11、FastAPI、LangChain/LangGraph、pgvector、SQLAlchemy (SQLModel)、uv 包管理
- **前端**：Vue 3 + TypeScript、Vite 6、Element Plus、Pinia、AntV/G2/S2
- **数据库**：PostgreSQL 15 + pgvector 扩展
- **LLM**：OpenAI 兼容协议，支持 DeepSeek / 阿里百炼 / 腾讯混元 / Gemini 等 12+ 供应商
- **包管理器**：uv（后端）、npm（前端）

## 构建、运行与测试

### 后端

```bash
cd backend

# 安装依赖（CPU 版 PyTorch）
uv sync --extra cpu

# 开发服务器（热重载）
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 运行全部测试
uv run pytest tests/ -v

# 运行单个测试
uv run pytest tests/test_agent.py::函数名 -v

# 代码检查 + 类型检查
uv run ruff check .
uv run mypy apps/

# 数据库迁移（通常由 lifespan 自动执行）
uv run alembic upgrade head
```

### 前端

```bash
cd frontend && npm install
npm run dev        # Vite 开发服务器（:5173，自动代理 /api 到 8000）
npm run build      # 生产构建到 dist/
npm run lint       # ESLint 自动修复
```

### Docker（全栈）

```bash
docker compose up -d              # API :8000, MCP :8001, PostgreSQL :5432
```

### 环境变量

后端从 `../.env`（`backend/` 上一级）读取配置，核心配置类 `backend/common/core/config.py`——`Settings(BaseSettings)`。关键变量：

| 变量 | 说明 |
|------|------|
| `SECRET_KEY` | JWT 签名密钥 |
| `POSTGRES_SERVER/PORT/USER/PASSWORD/DB` | 业务数据库 |
| `SQLBOT_DB_URL` | 如果设置，覆盖上述 PG 变量（支持 MySQL） |
| `CACHE_TYPE` | `redis` / `memory` / `None` |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` |
| `TABLE_EMBEDDING_ENABLED` | 是否启用表名语义搜索 |
| `EMBEDDING_DEFAULT_SIMILARITY` | 相似度阈值（默认 0.4） |
| `GENERATE_SQL_QUERY_LIMIT_ENABLED` | 是否强制 LIMIT |
| `PARSE_REASONING_BLOCK_ENABLED` | 是否解析 DeepSeek `<think>` 标签 |

## 架构

### 后端结构

```
backend/
├── main.py                    # FastAPI 应用入口
│   ├── lifespan()             # 启动：迁移DB → 缓存 → 嵌入向量预热 → 加密模型密钥
│   ├── TokenMiddleware         # 三路鉴权：Bearer JWT / Assistant JWT / Embedded JWT
│   ├── ResponseMiddleware      # 统一响应格式
│   └── FastApiMCP              # MCP Server 挂载在 8001 端口暴露 7 个端点
├── alembic/ + alembic.ini     # 数据库迁移
├── apps/
│   ├── api.py                 # 集中路由聚合——所有模块 router 在此注册到 /api/v1
│   ├── chat/                  # ⭐ 核心：问答引擎 + Agent 系统
│   │   ├── api/chat.py        # REST 端点（SSE 流式），route /chat/question
│   │   ├── curd/chat.py       # 所有 DB 操作：CRUD、权限过滤、Excel 导出、图表数据获取
│   │   ├── models/chat_model.py  # SQLModel 定义 + Pydantic schema + 旧 pipeline 提示词模板
│   │   ├── task/llm.py        # 旧 pipeline LLMService（逐步废弃，agent 模式替代）
│   │   └── agent/             # ⭐ Agent 引擎（详见下文）
│   ├── datasource/            # 数据源管理
│   │   ├── api/               # REST：datasource, table_relation, recommended_problem
│   │   ├── crud/datasource.py # 获取表对象、样本数据
│   │   ├── embedding/         # pgvector 嵌入向量：table_embedding.py (EmbeddingModelCache)
│   │   └── models/datasource.py  # CoreDatasource, CoreDatatable, TableField 等
│   ├── ai_model/              # LLM 供应商抽象
│   │   ├── model_factory.py   # LLMFactory + LLMConfig，LRU 缓存，支持 OpenAI/VLLM/Azure
│   │   ├── openai/llm.py      # BaseChatOpenAI（自定义 OpenAI wrapper）
│   │   └── embedding.py       # 嵌入模型调用
│   ├── db/                    # 多数据库查询引擎
│   │   ├── db.py              # get_uri() + exec_sql()——支持 12+ 数据库类型
│   │   ├── db_sql.py          # 按 DB 类型生成 metadata/sample 查询
│   │   ├── engine.py          # SQLAlchemy engine 构建 + create_table/insert_data
│   │   ├── constant.py        # DB 枚举 + 连接参数
│   │   └── es_engine.py       # Elasticsearch 支持
│   ├── system/                # 系统管理
│   │   ├── middleware/auth.py # TokenMiddleware（三路鉴权：Bearer/Ask/ApiKey/Assistant/Embedded）
│   │   ├── schemas/permission.py  # 权限检查装饰器 + 请求上下文
│   │   ├── api/               # login, user, aimodel, workspace, assistant, parameter, apikey
│   │   ├── crud/              # 对应 CRUD
│   │   └── models/            # AiModelDetail, User, Workspace 等 SQLModel
│   ├── settings/              # 系统设置 API (base.py)
│   ├── terminology/           # 业务术语库：API + CRUD + Model（带 pgvector embedding）
│   ├── data_training/         # SQL 训练示例：API + CRUD + Model（带 pgvector embedding）
│   ├── template/              # LLM 提示词模板系统
│   │   ├── template.yaml      # 主模板（300+ 行 YAML）
│   │   ├── business/          # 语义层技能文件：*_metrics.md、*_segments.md
│   │   ├── sql_examples/      # 各 DB 方言的 SQL 示例
│   │   ├── generate_sql/      # SQL 生成提示词 + 模板生成器
│   │   ├── generate_chart/    # 图表生成提示词
│   │   ├── generate_analysis/ # 分析提示词
│   │   ├── generate_predict/  # 预测提示词
│   │   ├── generate_dynamic/  # 动态 SQL 提示词
│   │   └── filter/            # 权限过滤器提示词
│   ├── mcp/                   # MCP Server 端点（mcp_question, mcp_start, mcp_assistant 等）
│   ├── dashboard/             # 仪表板 CRUD
│   └── swagger/               # 国际化 OpenAPI 文档（zh/en/ko/zh-TW）
├── common/
│   ├── core/config.py         # Settings (Pydantic BaseSettings)
│   ├── core/deps.py           # FastAPI Depends：SessionDep, CurrentUser, CurrentAssistant, Trans
│   ├── core/db.py             # 全局 engine 实例
│   ├── core/sqlbot_cache.py   # fastapi-cache2 配置
│   ├── utils/                 # 加密、工具函数、数据格式化
│   └── audit/                 # 审计日志模型 + 装饰器
└── templates/
    ├── template.yaml          # 主提示词模板
    ├── business/              # 语义层技能文件（*.md）
    └── sql_examples/          # SQL 方言指南
```

### Agent 系统（`backend/apps/chat/agent/`）——核心创新

Agent 用基于 LangGraph 的 ReAct 风格 Agent 图替换了旧的线性 Pipeline：

```
POST /api/v1/chat/question (SSE)
  → chat.py: question_answer() → stream_sql(engine="agent")
    → _stream_sql_agent(): LLMService.create() + init_record()
      → stream_agent() (adapter.py):
        1. init_agent_memory(llm_service)  → 运行时上下文
        2. _load_memory_from_db(session, chat_id)  → 恢复跨轮次状态
        3. 构建对话历史（如是追问）
        4. AgentExecutor(llm, memory, queue).run()  → 后台线程
        5. 从 asyncio.Queue 消费 SSE 事件
        6. _save_memory_to_db(session, chat_id, memory)

AgentExecutor._run_agent() 内部：
  → graph.astream(initial_state)  →  LangGraph ReAct 循环
    → agent_node: LLM.invoke(messages + SystemMessage)  →  返回 AIMessage
    → 解析 AIMessage: reasoning(thinking面板) | text-delta(回复区) | tool_call
    → tools_node: ToolRegistry.execute(name, args, memory)
    → ToolMessage 返回 → agent_node 继续
    → 循环直到：terminal_triggered | max_iterations | 无 tool_call
  → _post_process():
    - execute_and_chart (QA): 执行 SQL → 生成图表 → 保存 → emit SSE
    - text_and_chart (analysis/predict): 保存文本 → 在已有数据上生成图表
  → emit execution-stats + finish
  → _finalize_record_and_chat(): 保存 sql_answer + execution_log + finish
```

**关键文件及其职责**：

| 文件 | 职责 |
|------|------|
| `engine.py` | `AgentProfile` 数据类 + `dispatch()` 入口。定义 3 种 Agent 角色 |
| `graph.py` | `build_agent_graph()`——构建 LangGraph 状态图。系统提示词注入、条件路由（`tools`↔`end`）、工具 schema 过滤 |
| `executor.py` | `AgentExecutor`——核心执行器，1800+ 行。后台线程 → asyncio.Queue SSE 桥接、Token 统计、执行日志、图表生成自纠错循环（最多 3 次 LLM 调用） |
| `state.py` | `AgentState(TypedDict)`——LangGraph 状态，仅 messages + iteration。AgentMemory 不在此状态中（含不可序列化对象） |
| `memory.py` | `AgentMemory` + `QueryRecord` + `ChartRecord`——结构化 Agent 状态，持久化到 `Chat.memory_state` JSON 列。包含跨轮次持久化与恢复方法 |
| `tools/registry.py` | `ToolRegistry`——全局工具注册表，`ToolDef` 含 name/description/parameters/fn/terminal/category |
| `tools/register_all.py` | 15 个完整工具定义 + 懒加载导入 + 嵌入模型预热。`register_all_tools()` 幂等 |
| `tools/schema_tools.py` | 表探索：`search_relevant_tables`（双模式：CoreDatasource 用 embedding 搜索，AssistantOutDs 返回全部表）、`get_table_metadata`（缓存到 explored_tables）、`get_table_sample_data` |
| `tools/sql_tools.py` | SQL CRUD：`create_sql_query`（语法校验 + 只读检查 + EXPLAIN 验证 + JOIN 条件审查）、`edit_sql_query`（字符串替换编辑）。两个均为 terminal 工具 |
| `tools/query_tools.py` | 执行：`execute_sql_query`（含编译、权限过滤、连表警告）、`get_field_values` |
| `tools/chart_tools.py` | 图表 CRUD：`create_chart`、`edit_chart`。含 `_normalize_chart_config_dict()` 用于 LLM 输出 → 前端格式 |
| `tools/advanced_tools.py` | `replace_sql_fragment`、`ask_for_clarification`、`analyze_query_result`、`load_skill`、`get_data_summary`（纯内存统计）、`get_data_preview`（分页）、`search_web`（Bing/异步/5min 缓存） |
| `tools/permission_tools.py` | `apply_permissions()`——将行级安全谓词包裹到 SQL |
| `compiler.py` | `DatasetSQLCompiler`——DataEase 数据集逻辑表名 → 派生表子查询。纯正则替换，在 FROM/JOIN 上下文匹配 |
| `chart_registry.py` | **图表类型唯一配置源**（SSOT）。`ChartTypeDef` 定义 5 种图表类型（table/column/bar/line/pie），含关键词、数据约束、required_channels、settings_schema。所有 LLM 提示词 + 前端配置均由此派生 |
| `chart_knowledge.py` | 图表语义校验——验证 LLM 生成的 channel 映射是否与 `required_channels` 兼容 |
| `adapter.py` | 桥接 `LLMService` → Agent。提供 `init_agent_memory()`、`_load_memory_from_db()`、`_save_memory_to_db()`、`_build_history_context()`、`stream_agent()`、`stream_agent_recommend()` |

**三种 Agent 角色**（`engine.py` → `AgentProfile`）：

| 角色 | 工具数 | 工具列表 | 最大迭代 | 后处理 | 触发方式 |
|------|--------|---------|----------|--------|---------|
| **qa** | 13 | 完整探索链 + SQL/图表 CRUD | 50 | `execute_and_chart` | 默认用户提问，engine=agent |
| **analysis** | 4 | `get_data_summary`, `get_data_preview`, `analyze_query_result`, `ask_for_clarification` | 10 | `text_and_chart` | `/analysis` 命令或直接调用 |
| **predict** | 5 | 同上 + `search_web` | 15 | `text_and_chart` | `/predict` 命令或直接调用 |

qa 角色的 13 个工具（按类别）：
- **探索**: `search_relevant_tables`, `get_table_metadata`, `get_table_sample_data`, `get_field_values`, `load_skill`
- **创建**: `create_sql_query`, `create_chart`
- **编辑**: `edit_sql_query`, `edit_chart`, `replace_sql_fragment`
- **执行**: `execute_sql_query`, `analyze_query_result`
- **交互**: `ask_for_clarification`

终端工具（成功时触发 `terminal_triggered = True`）：`create_sql_query`, `edit_sql_query`, `edit_chart`, `replace_sql_fragment`, `ask_for_clarification`

**跨轮次记忆持久化**：

```
Chat.memory_state (JSONB)
  → version: 1
  → queries: { r_1: {sql, compiled_sql, tables_used, status, row_count, ...} }
  → charts:  { chart_r_1: {chart_ref, record_id, chart_config} }
  → explored_tables: { table_name: {fields[], sql?, is_dataset} }
  → _search_cache: { "query:5": {tables[]} }
  → conversation_summary: "Q1: 销售额 | Q2: 按月份分组 | Q3: 用柱状图"
```

注意：`AgentMemory.queries[].data`（完整查询结果）**不**持久化到 memory_state，只持久化元数据（row_count, result_fields）。完整数据存在 `ChatRecord.data` 中。

### 图表类型系统（双注册表 SSOT）

**后端 SSOT**：`backend/apps/chat/agent/chart_registry.py`——`CHART_TYPE_REGISTRY: Dict[str, ChartTypeDef]`

每个 `ChartTypeDef` 包含：
- **标识**：type_id, keywords（中英文）, display_name, icon, category
- **选择规则**：selection_rule——注入 LLM 提示词（如"柱/条图 X 轴禁止用时间列"）
- **数据约束**：min/max_metrics, min/max_dimensions, required_channels（含 preferred/forbidden/max_cardinality）
- **专属配置**：settings_schema——每类型的可选配置项（如折线图 smooth/show_area，饼图 donut）
- **兼容性**：compatible_with——可切换到的类型列表

新增图表类型只需在 `CHART_TYPE_REGISTRY` 添加一条记录，所有派生函数（`get_selection_rules()`, `get_frontend_config()`, `validate_chart_type()` 等）自动生效。

**图表生成流程**（executor.py `_generate_chart()`）：

```
1. 构建提示词（从 chart_registry 获取规则 + 约束 + settings_schema）
2. LLM 流式生成 chart JSON
3. 解析 + 校验：
   a. _validate_and_fix_chart_json(): JSON 解析 → type 校验 → axis 列名校验 → title 兜底
   b. validate_chart_semantics(): channel 类型兼容性检查
4. 如果无错误 → 保存 + emit
5. 如果有错误：
   attempt 2: 错误反馈回 LLM → 重新生成
   attempt 3: 再次失败 → _resolve_chart_fallback()（数据驱动的类型降级）
```

**前端**：
- `DisplayChartBlock.vue`——图表容器，处理类型切换、SSR 回退、轴字段兼容性
- `ChartCompatibility.ts`——对标 Metabase `getSensibleDisplays`，评分排序（0-100 分）
- `CartesianChart.ts`——G2 笛卡尔图表渲染器（column/bar/line）
- `charts/Pie.ts`——饼图渲染器
- `ChartComponent.vue`——AntV/S2 表格渲染器
- `useChartTypeConfig.ts`——从 `/api/v1/chat/chart-types` 获取后端注册表的响应式配置
- `chartIcons.ts`——图标映射
- `SettingsMigration.ts`——旧 settings 迁移到新 schema

### 问答请求完整数据流

```
浏览器
  → POST /api/v1/chat/question {chat_id, question}
    → TokenMiddleware 鉴权（Bearer JWT）
    → question_answer() (chat.py:290)
      → parse_quick_command() 检测 /regenerate, /analysis, /predict
      → stream_sql(engine="agent") (chat.py:396)
        → _stream_sql_agent() (chat.py:437)
          → LLMService.create(session, user, question, assistant)
            → model_factory.get_default_config() 获取 LLM 配置
            → BaseChatOpenAI 初始化（enable_thinking=True）
          → llm_service.init_record() 创建 ChatRecord（status=pending）
          → stream_agent(llm_service, session, question, is_followup)
            → init_agent_memory() + _load_memory_from_db()
            → AgentExecutor(llm, memory, queue).run()     ← 后台线程
              → build_agent_graph() → graph.astream()
                → agent_node: SystemMessage + HumanMessage → LLM
                → AIMessage: tool_calls → tools_node
                  → ToolRegistry.execute() → 工具返回 ToolMessage
                → 循环...
              → _post_process(): execute_sql_query() + _generate_chart()
              → emit("finish") + queue.put(None)
            → asyncio.Queue 消费 SSE chunks → StreamingResponse
```

**SSE 事件类型**（前端逐事件渲染）：

| 事件 type | 触发时机 | 前端渲染位置 |
|-----------|---------|-------------|
| `id` | 请求开始，record 创建后 | 更新 record.id |
| `question` | 同上 | 显示用户问题 |
| `reasoning` | LLM 思考过程（thinking 面板） | 可折叠的思考区 |
| `text-delta` | LLM 最终回答（回复区） | Markdown 渲染 |
| `tool-call` | LLM 决定调用工具 | 工具执行日志面板 |
| `tool-result` | 工具执行完成（含摘要） | 工具执行日志面板 |
| `sql` | SQL 生成完成（格式化后） | 代码高亮块 |
| `sql-data` | SQL 执行成功 | 触发 data 获取 |
| `chart-result` | 图表 LLM 流式生成中 | 可选的流式展示 |
| `chart` | 图表 JSON 已确定 | DisplayChartBlock 渲染 |
| `execution-stats` | Agent 结束，含 iterations/tokens/tools | 执行统计面板 |
| `finish` | Agent 完成 | 隐藏 loading，保存状态 |
| `error` | 异常 | 错误提示 |
| `clarify` | ask_for_clarification 被调用 | 追问提示 |

### 前端结构

```
frontend/src/
├── main.ts                    # Vue 应用入口，注册 Pinia/Router/I18n
├── App.vue                    # 根组件
├── api/                       # API 客户端
│   └── chat.ts                # Chat/ChatRecord 类 + chatApi (SSE Stream + REST)
├── router/index.ts            # hash 路由：/chat, /set/*, /system/*, /dashboard, /canvas
├── stores/                    # Pinia
│   ├── user.ts                # 用户信息 + Token
│   ├── chatConfig.ts          # 聊天设置（模型、数据源）
│   ├── dashboard.ts           # 仪表板状态
│   └── appearance.ts          # 外观设置
├── i18n/                      # Vue I18n（zh-CN, zh-TW, en, ko-KR）
├── views/
│   ├── chat/index.vue         # 主聊天视图——消息列表、输入框、数据源选择
│   ├── chat/answer/           # 按类型答案组件
│   │   ├── BaseAnswer.vue     # SQL + 文本答案展示
│   │   ├── ChartAnswer.vue    # 图表渲染容器
│   │   ├── AnalysisAnswer.vue # 数据分析结果展示
│   │   └── PredictAnswer.vue  # 数据预测结果展示
│   ├── chat/component/        # 图表渲染引擎
│   │   ├── DisplayChartBlock.vue  # 图表外层容器：类型切换工具栏、SSR 兜底、设置面板
│   │   ├── CartesianChart.ts      # G2 笛卡尔图（column/bar/line 共享逻辑）
│   │   ├── CartesianChartModel.ts # 数据模型构建（字段分类、轴推荐）
│   │   ├── BaseG2Chart.ts         # G2 基类（初始化、销毁、响应式）
│   │   ├── ChartCompatibility.ts  # 评分系统（对各类型计算 0-100 分）
│   │   ├── useChartTypeConfig.ts  # 响应式图表类型配置（从后端 API）
│   │   ├── chartIcons.ts          # 图标注册表
│   │   ├── ChartRenderErrors.ts   # 渲染错误收集与诊断
│   │   ├── SettingsMigration.ts   # 旧 settings 迁移
│   │   ├── charts/                # 按类型渲染器
│   │   │   ├── Pie.ts             # 饼图（含 donut）
│   │   │   └── utils.ts           # classifyColumn, recommendAxes 等
│   │   ├── ChartComponent.vue     # AntV/S2 表格
│   │   ├── MdComponent.vue        # Markdown 渲染
│   │   └── SQLComponent.vue       # SQL 高亮展示
│   ├── system/                # 系统管理页面（用户/工作空间/模型/嵌入/参数/审计）
│   ├── dashboard/             # 仪表板编辑器和预览
│   ├── ds/                    # 数据源表列表
│   ├── work/                  # 工作台
│   └── embedded/              # 嵌入式助手页面
└── components/
    ├── layout/                # 应用外壳（侧边栏、顶栏）
    └── ...                    # 通用组件
```

### 数据库

PostgreSQL 15 + pgvector。核心表：

| 表 | 主要字段 | 说明 |
|----|---------|------|
| `chat` | id, oid, create_by, brief, datasource, engine_type, origin, memory_state(JSON) | 会话 |
| `chat_record` | id, chat_id, question, sql_answer, sql, data, chart, analysis, predict, execution_log(JSON) | 问答记录（每次提问一条） |
| `core_datasource` | id, name, type, configuration(加密JSON), oid | 数据源 |
| `core_datatable` | id, ds_id, table_name, embedding(pgvector), custom_comment | 表 |
| `table_field` | id, table_id, field_name, field_type, field_comment, custom_comment, embedding(pgvector) | 字段 |
| `terminology` | id, ds_id, term, definition, embedding(pgvector) | 业务术语 |
| `data_training` | id, ds_id, question, sql, embedding(pgvector) | SQL 训练示例 |
| `ai_model_detail` | id, name, base_model, api_domain(加密), api_key(加密), protocol, default_model | LLM 模型配置 |
| `chat_log` | id, type, operate, messages(JSONB), token_usage(JSONB) | 操作日志 |

Alembic 管理迁移（`backend/alembic/versions/`），在应用 `lifespan` 中自动执行 `command.upgrade(alembic_cfg, "head")`。

### 鉴权系统（三路鉴权）

`TokenMiddleware`（`backend/apps/system/middleware/auth.py`）按优先级处理：

1. **X-SQLBOT-ASK-TOKEN**（API Key）——前缀 `sk`，JWT 验证 + secret_key 签名
2. **X-SQLBOT-ASSISTANT-TOKEN**（助手/嵌入）——前缀 `assistant` 或 `embedded`
   - `assistant`：标准 JWT 验证，payload 含 `assistant_id`
   - `embedded`：签名验证关闭的 JWT（payload 含 appId/embeddedId/account），用 `app_secret` 二次验证
3. **X-SQLBOT-TOKEN**（默认）——前缀 `bearer`，标准 JWT 用户验证

白名单路径（`whiteUtils.is_whitelisted`）和 OPTIONS 请求跳过鉴权。

### 关键设计模式

**① 无处不在的 Embedding**
表名、字段名、术语、训练数据全部有 pgvector 嵌入向量用于语义搜索。`EmbeddingModelCache`（单例）懒加载 `sentence-transformers` 模型。默认模型：`shibing624/text2vec-base-chinese`。搜索时计算余弦相似度，按配置的阈值和 Top-N 过滤。

**② 语义层（Semantic Layer）**
`load_skill` 工具加载 `templates/business/` 下的技能文件：
- `business-{ds_name}_metrics.md`——指标 SQL 片段（如"销售额 = SUM(price * qty)"）
- `business-{ds_name}_segments.md`——可复用筛选条件
- `business-{ds_name}_derived_metrics.md`——派生指标公式
- `sql-{dialect}.md`——数据库方言指南

Agent 被指示优先使用语义层，仅对语义层未覆盖的字段进行探索。

**③ DataEase 数据集编译器**
`DatasetSQLCompiler`（`compiler.py`）：当 `memory.out_ds_instance is not None` 时，DataEase 的视图被标记为 `is_dataset=true`。编译器用纯正则替换将逻辑表名替换为 `(backing_sql) AS ds_N` 子查询。仅在 FROM/JOIN 上下文中替换，避免误匹配 SELECT 列或 WHERE 值中的同名文本。别名黑名单防止 SQL 关键字被误认为表别名。

**④ 权限过滤**
`apply_permissions()`（`permission_tools.py`）：在 SQL 执行前，将行级安全（RLS）谓词包裹到 SQL 中。调用 LLM 将权限规则翻译为 WHERE 条件，然后包装为 `SELECT * FROM (original_sql) AS _filtered WHERE <rls_predicate>`。

**⑤ 国际化（i18n）**
- OpenAPI 文档：`swagger/i18n.py` 使用 `PLACEHOLDER_PREFIX` 占位符 + 按语言的翻译表，按请求 lang 参数或 Accept-Language 头动态生成
- 系统提示词：`chart_registry.py` 提供 zh-CN/en 双语的 selection_rule 和 display_name
- 用户界面提示词：模板变量 `{lang}` 控制输出语言

**⑥ MCP 集成**
`FastApiMCP` 在 8001 端口挂载独立的 FastAPI 应用。暴露的操作（白名单）：`mcp_datasource_list`, `get_model_list`, `mcp_question`, `mcp_start`, `mcp_assistant`, `mcp_ws_list`。图表图片通过 `/images` 静态文件挂载提供。

**⑦ 快速命令系统**
`parse_quick_command()` 解析用户输入中的命令：
- `/regenerate`——针对上一条记录重新生成 SQL/图表
- `/analysis`——对当前结果进行 AI 分析
- `/predict`——对当前数据进行趋势预测

命令可带 `record_id` 指定目标，也可通过 `text_before_command`（命令前的自然语言文本）追加上下文。

### 旧 Pipeline vs 新 Agent

代码库同时存在两套引擎，通过 `engine` 参数切换：

| | 旧 Pipeline | 新 Agent |
|---|---|---|
| 入口 | `chat.py:stream_sql(engine="pipeline")` | `chat.py:stream_sql(engine="agent")` |
| 执行器 | `LLMService.run_task_async()` | `AgentExecutor.run()` |
| 架构 | 线性 Pipeline（选择表→生成SQL→执行→图表） | ReAct 循环（LLM 自主调用工具） |
| 提示词 | `template.yaml` + `AiModelQuestion.*_question()` | `graph.py:_build_system_prompt()` + `engine.py` |
| 状态 | 无持久化（每次提问全新） | `AgentMemory` 持久化到 `Chat.memory_state` |
| 默认值 | 旧（逐步废弃） | `engine="agent"` 已是 `stream_sql()` 的默认值 |

### 依赖关系注意事项

- `sqlbot-xpack`（来自 test PyPI `https://test.pypi.org/simple`）提供企业版扩展，在 `main.py` 的 lifespan 中初始化
- `dmpython`（达梦数据库驱动）仅在非 macOS 平台安装
- `torch` 按 extra 选择 CPU 版（`uv sync --extra cpu`）或 CUDA 版（`--extra cu128`）
- Oracle Instant Client 需安装在 `/opt/sqlbot/db_client/oracle_instant_client`，否则自动降级为 thin 模式

## 当前分支：`feature/agent-v2`

当前工作分支正在活跃开发基于 Agent 的 V2 架构。修改涉及的文件：
- **Agent 引擎核心**：`engine.py`, `executor.py`, `graph.py`——Agent 角色定义、执行循环、图表生成自纠错
- **工具实现**：`chart_registry.py`, `chart_knowledge.py`——图表类型 SSOT + 语义校验
- **前端**：`AnalysisAnswer.vue`, `BaseAnswer.vue`, `ChartAnswer.vue`, `PredictAnswer.vue`——Agent SSE 事件渲染
- **前端图表**：`CartesianChartModel.ts`, `DisplayChartBlock.vue`, `Pie.ts`, `utils.ts`——数据驱动类型切换 + 通道兼容性
- **后端 API**：`chat.py` (curd)——save_sql_answer / save_analysis_answer / save_predict_answer
- **模型层**：`model_factory.py`——enable_thinking 支持
