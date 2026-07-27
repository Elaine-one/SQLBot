# Metabase AI (Metabot) 架构分析

> 编写日期: 2026-07-13
> 基于代码: Metabase OSS + Enterprise 源码 (Clojure/ClojureScript/TypeScript)

---

## 一、架构总览

### 1.1 与 Dataease/SQLBot 的根本区别

| 维度 | Metabase (Metabot) | Dataease + SQLBot |
|------|-------------------|-------------------|
| **AI 引擎位置** | **集成在 Clojure 后端内部**（同一 JVM 进程） | **独立 Python 服务** (SQLBot FastAPI) |
| **架构模式** | **Agent Loop + Tool Calling**（迭代式多轮工具调用） | **线性 Pipeline**（一次 LLM 调用生成 SQL） |
| **AI 能力范围** | SQL 生成 + 图表创建 + 仪表板 + 文档 + Slack + 搜索 + 导航 | 仅 SQL 生成 + 图表配置 |
| **LLM 集成方式** | 自建多 Provider 抽象层 (Anthropic/OpenAI/DeepSeek/Azure/Bedrock) | LangChain 封装 (LLMFactory) |
| **嵌入方式** | Embedding SDK + iframe (`/api/metabot/agent-streaming`) | `assistant.js` + iframe (Chat API) |
| **SSE 协议** | AI SDK v4 Line Protocol | 自定义 SSE 事件类型 |
| **数据源访问** | 通过 Metabase 自身的 Driver 层（与 QP 共用） | 直连目标数据库 (SQLAlchemy) |

### 1.2 核心架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    Metabase JVM 进程                         │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                  Metabot Agent                        │   │
│  │                                                       │   │
│  │  POST /api/metabot/agent-streaming                    │   │
│  │    │                                                  │   │
│  │    ▼                                                  │   │
│  │  run-agent-loop() ← 核心 Agent 循环                   │   │
│  │    │                                                  │   │
│  │    ├─→ System Prompt (Selmer 模板渲染)                 │   │
│  │    ├─→ LLM call (via metabase.metabot.self.*)        │   │
│  │    ├─→ Tool Call(s) ← 35 scoped tools               │   │
│  │    │    ├─ SQL: create_sql_query, edit_sql_query      │   │
│  │    │    ├─ Query: construct_query, execute_query      │   │
│  │    │    ├─ Chart: create_chart, edit_chart            │   │
│  │    │    ├─ Dashboard: create_dashboard                │   │
│  │    │    ├─ Search: search_content                     │   │
│  │    │    ├─ Metadata: get_table_metadata               │   │
│  │    │    └─ ... 更多工具                               │   │
│  │    ├─→ Memory 更新 (状态管理)                          │   │
│  │    └─→ 循环直到 terminal-tool 或 max-iterations       │   │
│  │                                                       │   │
│  │  输出: AI SDK v4 Line Protocol (SSE)                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │               LLM Provider Layer                       │   │
│  │  metabase.metabot.self.{claude, openai, deepseek,     │   │
│  │                           azure, bedrock, openrouter} │   │
│  │  + metabase.llm.{anthropic, settings, api}            │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │          Query Processor (共享 Metabase QP)            │   │
│  │  执行 SQL / MBQL 查询，复用 Metabase Driver 层         │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 二、两种入口模式

### 2.1 独立聊天模式（Metabot Chat）

**前端入口**: Metabase 主界面 → Metabot 聊天面板

**API 调用链**:
```
前端 RTK Query → POST /api/metabot/agent-streaming
  ↓
metabot.api/streaming-request
  ↓
metabot.api/native-agent-streaming-request
  ↓
metabot.agent/run-agent-loop
```

**请求体结构** ([metabot/api.clj:242-257](metabase/src/metabase/metabot/api.clj#L242-L257)):
```clojure
{:profile_id    "internal"           ; Agent 配置
 :metabot_id    "1"                  ; Metabot 实例 ID
 :message       "上月销售额最高的10个产品？"
 :context       {:user_is_viewing [...]  ; 当前浏览上下文
                 :capabilities #{...}}   ; 权限能力
 :conversation_id "uuid-xxx"
 :history       [{:role "user" :content "..."} ...]  ; 对话历史
 :state         {:queries {} :charts {} :chart-configs {}}  ; Agent 状态
 :debug         false}
```

### 2.2 嵌入/侧边栏模式（Embedding SDK）

Metabase 通过 **Embedding SDK** 支持将 Metabot 嵌入到外部应用。其机制与 Dataease 的 iframe 模式类似，但在 Metabase 体系内：

```
外部应用
  │
  ├─→ Metabase Embedding SDK (JS library)
  │     │
  │     ├─ 初始化: metabot-state-channel.ts (postMessage 通信)
  │     └─ 渲染: 聊天 UI 组件
  │
  └─→ POST /api/metabot/agent-streaming (与独立模式同一端点)
```

**关键文件**:
- [frontend/src/embedding-sdk-shared/lib/metabot-state-channel.ts](metabase/frontend/src/embedding-sdk-shared/lib/metabot-state-channel.ts) — postMessage 状态同步
- [frontend/src/embedding-sdk-bundle/types/metabot.ts](metabase/frontend/src/embedding-sdk-bundle/types/metabot.ts) — Embedding SDK 类型定义
- [e2e/test-component/scenarios/embedding-sdk/metabot-question.cy.spec.tsx](metabase/e2e/test-component/scenarios/embedding-sdk/metabot-question.cy.spec.tsx) — E2E 测试

### 2.3 Slack Bot 模式

Metabase 还支持 Slack Bot 集成（[metabase/src/metabase/slackbot/](metabase/src/metabase/slackbot/)），通过 `@metabot` 在 Slack 中提问。复用同一个 Agent 系统。

---

## 三、Agent 循环详细流程

### 3.1 入口：`POST /api/metabot/agent-streaming`

**文件**: [metabase/src/metabase/metabot/api.clj:242-266](metabase/src/metabase/metabot/api.clj#L242-L266)

```clojure
(defendpoint :post "/agent-streaming"
  "Send a chat message to the LLM via the AI Proxy."
  [body :- [包含 profile_id, metabot_id, message, context, conversation_id, history, state]]
  req]
  ;; 1. 升级旧 MBQL 查询格式
  (let [body* (upgrade-viewing-queries body)
        request-info {:origin embed-referrer :user-agent ... :ip-address ...}]
    ;; 2. 进入主流程
    (streaming-request body* request-info)))
```

### 3.2 主流程：`streaming-request`

**文件**: [metabase/src/metabase/metabot/api.clj:181-214](metabase/src/metabase/metabot/api.clj#L181-L214)

```clojure
(defn streaming-request [request-info]
  ;; 1. 校验 Metabot 启用状态
  (check-metabot-enabled! metabot-id)
  ;; 2. 检查使用限制
  (check-metabase-managed-free-limit!)
  ;; 3. 解析 profile (确定 Agent 行为模式)
  (resolve-dynamic-profile-id profile_id metabot-id)
  ;; 4. 创建 Assistant 消息行 (数据库持久化)
  (start-turn! conversation_id profile-id message)
  ;; 5. 启动 Native Agent 流
  (native-agent-streaming-request {...}))
```

### 3.3 Agent 循环：`run-agent-loop`

**文件**: [metabase/src/metabase/metabot/agent/core.clj:597-640](metabase/src/metabase/metabot/agent/core.clj#L597-L640)

```
┌───────────────────────────────────────────────────────────────┐
│ run-agent-loop                                                │
│                                                               │
│ 1. init-agent():                                              │
│    ├── assign-context-ids() → 给上下文项分配 UUID             │
│    ├── profiles/get-profile() → 加载 Profile                  │
│    │   │   ├── :internal              — 通用聊天                  │
│    │   │   ├── :sql                   — SQL 专用                  │
│    │   │   ├── :nlq / :nlq-fallback   — 自然语言查询              │
│    │   │   ├── :embedding_next        — 嵌入 SDK 用               │
│    │   │   ├── :slackbot              — Slack Bot                 │
│    │   │   ├── :transforms_codegen    — 数据转换代码生成          │
│    │   │   └── :document-generate-content — 文档内容生成          │
│    ├── profiles/profile->tools() → 按能力过滤工具集           │
│    ├── seed-state() → 从上下文提取查询注入状态                │
│    └── memory/initialize() → 初始化 Agent 记忆                │
│                                                               │
│ 2. 迭代循环 (最多 N 轮, 默认由 profile 定义):                  │
│    │                                                          │
│    ├── call-llm():                                            │
│    │   ├── messages/build-system-message() → 系统 Prompt       │
│    │   │   └── prompts/build-system-message-content()          │
│    │   │       ├── 加载 Selmer 模板 (internal.selmer 等)      │
│    │   │       ├── 渲染上下文: 用户信息, 浏览内容, SQL方言     │
│    │   │       ├── 构建技能清单 (skills catalog)               │
│    │   │       └── 条件性注入: SQL引导 / NLQ引导 / 自定义指令  │
│    │   ├── messages/build-message-history() → 对话历史         │
│    │   ├── invert-links() → 链接反解析                         │
│    │   └── self/call-llm() → 调用 LLM Provider               │
│    │       └── 返回 reducible stream of AI SDK parts           │
│    │                                                          │
│    ├── 处理 LLM 响应:                                          │
│    │   ├── text parts → 流式输出                               │
│    │   ├── tool-input parts → 执行工具调用                     │
│    │   │   └── tools/execute-tool-calls()                     │
│    │   │       ├── 验证工具名和参数                             │
│    │   │       ├── 通过 scope 检查权限                          │
│    │   │       ├── 执行工具函数                                 │
│    │   │       └── 返回 tool-output parts                      │
│    │   └── tool-output parts → 更新 memory                     │
│    │                                                          │
│    ├── update-memory():                                        │
│    │   ├── memory/add-step() → 记录本轮                        │
│    │   ├── extract-queries() → 提取查询状态                    │
│    │   └── extract-charts() → 提取图表状态                     │
│    │                                                          │
│    └── should-continue? → 判断是否继续迭代:                    │
│        ├── iteration >= max-iterations? → :max-iterations     │
│        ├── terminal-tool-call? → :terminal-tool               │
│        │   (如 create_sql_query 成功后终止)                    │
│        └── 还有 tool-calls? → 继续                             │
│                                                               │
│ 3. 返回: reducible sequence of AI SDK v4 parts                │
└───────────────────────────────────────────────────────────────┘
```

---

## 四、Prompt 系统

### 4.1 模板系统

与 SQLBot 的 YAML 模板不同，Metabase 使用 **Selmer 模板引擎**（类似 Jinja2/Django Templates）。

**模板位置**: `resources/metabot/prompts/system/`

**核心模板文件**:
- `internal.selmer` — 通用聊天 Profile
- `sql-querying-only.selmer` — 纯 SQL 生成
- `natural-language-querying-only.selmer` — NLQ（自然语言查询，基于 curated models）
- `natural-language-querying-fallback.selmer` — NLQ 回退（搜索全部表）

### 4.2 Prompt 构建

**文件**: [metabase/src/metabase/metabot/agent/prompts.clj:137-211](metabase/src/metabase/metabot/agent/prompts.clj#L137-L211)

```clojure
(defn build-system-message-content [profile context tools capabilities]
  (let [template-context
        {:metabot_name         "Metabot"
         :current_time         "2026-07-13 14:30:00"
         :current_user_info    "用户权限/角色信息..."
         :first_day_of_week    "Sunday"
         :sql_dialect          "postgresql"
         :sql_dialect_loaded   true
         :skill_catalog        [...]      ; 可用技能目录
         :skill_always_on      [...]      ; 常驻技能内容
         :viewing_context      [...]      ; 用户当前浏览上下文
         :recent_views         [...]      ; 最近查看
         :has_sql_generation   true
         :has_nlq              false
         :has_query_tools      true
         :has_other_tools      true
         :custom_instructions  "..."}]    ; 管理员自定义指令
    (selmer/render template template-context)))
```

### 4.3 Skills 系统（轻量级 RAG）

不同于 SQLBot 的 embedding 表选择，Metabase 使用 **Skills 系统**按需加载知识：

```
┌─ Skill Manifest (在 System Prompt 中) ─────────────────┐
│                                                         │
│  # Available skills (load the skill(s) you need):       │
│  - sql-postgresql                                       │
│  - sql-mysql                                            │
│  - sql-bigquery                                         │
│  - ...                                                  │
│                                                         │
│  LLM 可以通过 load_skill 工具按需加载:                    │
│  → tool: load_skill                                     │
│    args: {name: "sql-postgresql"}                       │
│    → 返回 PostgreSQL 特定的 SQL 编写指南                 │
└─────────────────────────────────────────────────────────┘
```

**文件**: [metabase/src/metabase/metabot/skills.clj](metabase/src/metabase/metabot/skills.clj)

---

## 五、工具系统 (Tools)

### 5.1 工具架构

Metabot 的核心差异在于其**丰富的工具系统**——Agent 不是直接生成 SQL，而是通过调用工具来完成各种操作。

**工具注册**: [metabase/src/metabase/metabot/tools/](metabase/src/metabase/metabot/tools/)

| 工具类别 | 工具名 | 功能 |
|---------|--------|------|
| **SQL** | `create_sql_query` | 创建新的 SQL 查询 |
| | `edit_sql_query` | 编辑已有 SQL 查询 |
| | `replace_sql_query` | 替换 SQL 查询中的片段 |
| | `ask_for_sql_clarification` | 要求用户澄清 SQL 意图 |
| **Query** | `construct_notebook_query` | 构建 MBQL 查询 |
| **Chart** | `create_chart` | 创建图表 |
| | `edit_chart` | 编辑图表配置 |
| | `analyze_chart` | 分析图表内容 (视觉) |
| **Dashboard** | `create_autogenerated_dashboard` | 创建自动仪表板 |
| | `create_dashboard_subscription` | 创建仪表板订阅 |
| **Search** | `search` | 搜索 Metabase 内容 (多 profile 变体: `sql-search`, `nlq-search`, `transform-search`) |
| **Metadata** | `list_available_data_sources` | 列出可用数据源 |
| | `list_available_fields` | 列出可用字段 |
| | `get_field_values` | 获取字段值 |
| **Snippets** | `list_snippets` | 列出 SQL 代码片段 |
| | `get_snippet_details` | 获取片段详情 |
| **Navigation** | `navigate_user` | 导航到实体 |
| **Resources** | `load_skill` | 加载按需技能 |
| | `read_resource` | 读取资源 (表/字段/度量/仪表板详情) |
| **Document** | `document_schema_collect` | 收集文档 Schema |
| | `document_construct_sql_chart` | 构建 SQL 图表文档 |
| | `document_construct_model_chart` | 构建 Model 图表文档 |
| **Alert** | `create_alert` | 创建告警 |
| **Todo** | `todo_write` | 写入待办事项 |
| | `todo_read` | 读取待办事项 |
| **Transforms** | `get_transform_details` | 获取数据转换详情 |
| | `write_transform_sql` | 写入 SQL 转换 |
| | `write_transform_python` | 写入 Python 转换 |
| **Visualization** | `static_viz` | 静态图表渲染 (Slack 用) |
| **NLQ** | `retrieve_library_entities` | 检索 curated library 实体 |

### 5.2 SQL 生成工具详解

**文件**: [metabase/src/metabase/metabot/tools/sql/create.clj](metabase/src/metabase/metabot/tools/sql/create.clj)

```clojure
(defn create-sql-query [{:keys [database-id sql name]}]
  ;; 1. 验证数据库访问权限
  (validate-database-access database-id)
  ;; 2. 获取 SQL 方言
  (let [dialect (database-id->dialect database-id)
  ;; 3. 验证 SQL 语法 (使用 sqlglot/macaw)
        {:keys [valid? transpiled-sql]} (validate-sql dialect sql)]
    ;; 4. 创建内存查询结构 (MBQL)
    (when valid?
      (create-native-query database-id transpiled-sql)
      ;; → 返回 {:query-id "xxx" :query-content "SELECT ..." :database 1}
      )))
```

**关键设计**: LLM 不直接输出 SQL 字符串，而是通过调用 `create_sql_query` 工具来创建 SQL 查询。工具的返回值（query-id, chart-id 等）会存储在 Agent 的 memory/state 中，后续工具可以引用。

### 5.3 权限范围 (Scopes)

**文件**: [metabase/src/metabase/metabot/scope.clj](metabase/src/metabase/metabot/scope.clj)

每个工具调用都受 Scope 控制（35 scopes），例如:

```clojure
;; SQL
(agent-sql-construct "agent:sql:construct")   ;; 构建 SQL
(agent-sql-create "agent:sql:create")         ;; 创建 SQL 查询
(agent-sql-edit "agent:sql:edit")             ;; 编辑 SQL 查询
(agent-sql-read "agent:sql:read")             ;; 读取并澄清 SQL 查询
(agent-sql-execute "agent:sql:execute")       ;; 执行原始 SQL

;; Query
(agent-query "agent:query")                   ;; 构建并执行查询
(agent-query-construct "agent:query:construct")
(agent-query-execute "agent:query:execute")
(agent-notebook-create "agent:notebook:create")

;; Chart
(agent-viz-create "agent:viz:create")         ;; 创建图表
(agent-viz-edit "agent:viz:edit")             ;; 编辑图表
(agent-viz-read "agent:viz:read")             ;; 分析图表

;; Dashboard
(agent-dashboard-create "agent:dashboard:create")
(agent-dashboard-update "agent:dashboard:update")

;; Search, Metadata, Resources
(agent-search "agent:search")                 ;; 搜索
(agent-metadata-read "agent:metadata:read")   ;; 元数据读取
(agent-resource-read "agent:resource:read")   ;; 资源读取

;; Todos, Alerts, Transforms, Snippets
(agent-todo-write "agent:todo:write")
(agent-alert-create "agent:alert:create")
(agent-transforms-write "agent:transforms:write")
(agent-snippets-read "agent:snippets:read")
```

用户权限检查在工具执行时通过 `metabot/permission` 设置进行。

---

## 六、LLM Provider 层

### 6.1 多 Provider 支持

**文件**: [metabase/src/metabase/metabot/self/](metabase/src/metabase/metabot/self/)

```
metabase.metabot.self
├── core.clj          — 通用接口 + SSE 流处理
├── claude.clj        — Anthropic Claude (Messages API)
├── openai.clj        — OpenAI (Chat Completions API)
├── deepseek.clj      — DeepSeek
├── azure.clj         — Azure OpenAI
├── bedrock.clj       — AWS Bedrock
├── openrouter.clj    — OpenRouter
├── chat_completions.clj — Chat Completions 格式转换
├── features.clj      — 功能检测
└── schema.clj        — 响应 Schema
```

**Provider 配置**:
- 后端设置: `llm-metabot-provider` (如 `"anthropic/claude-sonnet-4-5"`)
- API Key 设置: `llm-anthropic-api-key`, `llm-openai-api-key`, 等
- 模型列表通过 Provider API 动态获取

### 6.2 统一请求接口

**文件**: [metabase/src/metabase/metabot/self/core.clj:35-59](metabase/src/metabase/metabot/self/core.clj#L35-L59)

所有 Provider Adapter 接受统一的 `LLMRequestOpts`:

```clojure
{:model       "claude-sonnet-4-5"
 :system      "You are Metabot..."
 :input       [{:role :user :content "..."} ...]  ;; AI SDK parts
 :tools       [{:tool-name "..." :schema {...} :fn ...} ...]
 :tool_choice "auto"                     ;; 或 "required"
 :temperature 0.7
 :max-tokens  4096}
```

---

## 七、SQL 生成流程对比：Metabot vs SQLBot

### 7.1 架构差异

| 维度 | Metabase (Metabot) | Dataease + SQLBot |
|------|-------------------|-------------------|
| **生成方式** | Agent 调用 `create_sql_query` 工具 | LLM 直接输出 JSON `{"sql":"...","tables":[...]}` |
| **迭代能力** | 多轮迭代，支持工具反馈和自我修正 | 单次生成（/regenerate 除外，需要用户手动触发） |
| **Schema 给法** | Skills 系统按需加载 SQL 方言知识 | M-Schema 直接嵌入 Prompt |
| **表选择** | 通过 `get_table_metadata` / `search_content` 工具按需查询 | embedding 相似度排序 → top N 全部嵌入 |
| **SQL 验证** | 工具内通过 sqlglot/macaw 验证并转译 | sqlglot 只做只读校验 |
| **查询执行** | 通过 Metabase Driver 层 (复用 QP) | SQLAlchemy 直连各数据库 |
| **结果可视化** | `create_chart` 工具创建图表配置 | LLM 生成图表 JSON → MCPService 渲染图片 |

### 7.2 SQL 生成工具链

```
用户: "上月销售额最高的10个产品"
  │
  ▼
Agent Loop:
  [Iteration 1]
  ├─ LLM 分析: 需要先获取表结构
  └─ Tool: get_table_metadata(database-id=1, table-name="orders")
      → 返回字段列表
  [Iteration 2]
  ├─ LLM 分析: 需要 product 表的字段
  └─ Tool: get_table_metadata(database-id=1, table-name="products")
      → 返回字段列表
  [Iteration 3]
  ├─ LLM 分析: 已有足够信息，生成 SQL
  └─ Tool: create_sql_query({
        database-id: 1,
        sql: "SELECT p.name, SUM(o.amount) as total
              FROM orders o JOIN products p ON o.product_id = p.id
              WHERE o.created_at >= '2026-06-01'
              GROUP BY p.name ORDER BY total DESC LIMIT 10"
      })
      → 返回 {:query-id "abc123" :query-content "..."}
  [Iteration 4]
  ├─ LLM 决定: 执行查询并创建图表
  ├─ Tool: execute_query(query-id="abc123")
  │   → 返回查询结果
  └─ Tool: create_chart(query-id="abc123", chart-type="bar")
      → 返回 {:chart-id "def456"}
  ← Agent 停止 (terminal tools 已完成)
```

### 7.3 Prompt 大小控制策略

| 策略 | Metabase | SQLBot |
|------|----------|--------|
| **表 Schema** | 不在 Prompt 中，通过 `get_table_metadata` 工具按需获取 | 嵌入全部 top N 表的完整 M-Schema |
| **SQL 方言知识** | Skills 系统按需 `load_skill` | 嵌入 template.yaml + sql_examples/*.yaml |
| **Sample Data** | 不在 Prompt 中，通过工具按需查询 | `SELECT * LIMIT 3` 每张表 |
| **上下文** | 用户浏览上下文 + recent views | 对话历史 + 术语 embedding |
| **Prompt 初始大小** | **较小** (~1000-2000 tokens) | **较大** (~3000-5000 tokens) |
| **运行时 Token** | 随工具调用增加（但按需） | 一次性全部加载 |

---

## 八、核心代码文件索引

### Clojure 后端

| 文件 | 说明 |
|------|------|
| [src/metabase/metabot/api.clj](metabase/src/metabase/metabot/api.clj) | **主 API**: `/agent-streaming`, `/settings`, `/feedback` |
| [src/metabase/metabot/agent/core.clj](metabase/src/metabase/metabot/agent/core.clj) | **Agent 循环**: `run-agent-loop`, `call-llm`, `init-agent` |
| [src/metabase/metabot/agent/prompts.clj](metabase/src/metabase/metabot/agent/prompts.clj) | **Prompt 系统**: Selmer 模板加载/渲染/缓存 |
| [src/metabase/metabot/agent/messages.clj](metabase/src/metabase/metabot/agent/messages.clj) | 消息构建: system message, message history |
| [src/metabase/metabot/agent/memory.clj](metabase/src/metabase/metabot/agent/memory.clj) | Agent 状态记忆管理 |
| [src/metabase/metabot/agent/profiles.clj](metabase/src/metabase/metabot/agent/profiles.clj) | Profile 定义: internal/sql/nlq/slackbot... |
| [src/metabase/metabot/agent/streaming.clj](metabase/src/metabase/metabot/agent/streaming.clj) | 流式输出后处理 (链接/格式化) |
| [src/metabase/metabot/tools/sql/create.clj](metabase/src/metabase/metabot/tools/sql/create.clj) | **SQL 创建工具** |
| [src/metabase/metabot/tools/sql/edit.clj](metabase/src/metabase/metabot/tools/sql/edit.clj) | SQL 编辑工具 |
| [src/metabase/metabot/tools/sql/validation.clj](metabase/src/metabase/metabot/tools/sql/validation.clj) | SQL 验证 (sqlglot/macaw) |
| [src/metabase/metabot/tools/charts/create.clj](metabase/src/metabase/metabot/tools/charts/create.clj) | 图表创建工具 |
| [src/metabase/metabot/tools/construct.clj](metabase/src/metabase/metabot/tools/construct.clj) | 查询构建工具 |
| [src/metabase/metabot/tools.clj](metabase/src/metabase/metabot/tools.clj) | 工具注册和分发 |
| [src/metabase/metabot/self/core.clj](metabase/src/metabase/metabot/self/core.clj) | **Provider 抽象**: 统一 LLM 请求接口 + SSE 流处理 |
| [src/metabase/metabot/self/claude.clj](metabase/src/metabase/metabot/self/claude.clj) | Anthropic Claude Adapter |
| [src/metabase/metabot/self/openai.clj](metabase/src/metabase/metabot/self/openai.clj) | OpenAI Adapter |
| [src/metabase/metabot/self/deepseek.clj](metabase/src/metabase/metabot/self/deepseek.clj) | DeepSeek Adapter |
| [src/metabase/metabot/scope.clj](metabase/src/metabase/metabot/scope.clj) | **权限 Scope**: 40+ 工具操作范围定义 |
| [src/metabase/metabot/context.clj](metabase/src/metabase/metabot/context.clj) | 用户上下文构建 (浏览内容/最近查看) |
| [src/metabase/metabot/persistence.clj](metabase/src/metabase/metabot/persistence.clj) | 对话持久化 |
| [src/metabase/metabot/skills.clj](metabase/src/metabase/metabot/skills.clj) | Skills 按需加载系统 |
| [src/metabase/metabot/settings.clj](metabase/src/metabase/metabot/settings.clj) | Metabot 配置管理 |
| [src/metabase/metabot/config.clj](metabase/src/metabase/metabot/config.clj) | 动态配置解析 |
| [src/metabase/metabot/core.clj](metabase/src/metabase/metabot/core.clj) | 公共 API 导出 |
| [src/metabase/llm/settings.clj](metabase/src/metabase/llm/settings.clj) | LLM 基础设置 (API keys) |
| [src/metabase/llm/anthropic.clj](metabase/src/metabase/llm/anthropic.clj) | Anthropic 基础客户端 |

### TypeScript 前端

| 文件 | 说明 |
|------|------|
| [frontend/src/metabase/api/metabot.ts](metabase/frontend/src/metabase/api/metabot.ts) | Metabot RTK Query API |
| [frontend/src/metabase/redux/store/metabot.ts](metabase/frontend/src/metabase/redux/store/metabot.ts) | Metabot Redux Store |
| [frontend/src/metabase/plugins/oss/metabot.ts](metabase/frontend/src/metabase/plugins/oss/metabot.ts) | OSS 插件注册 |
| [frontend/src/embedding-sdk-shared/lib/metabot-state-channel.ts](metabase/frontend/src/embedding-sdk-shared/lib/metabot-state-channel.ts) | **Embedding SDK**: postMessage 通信 |
| [enterprise/frontend/src/metabase-enterprise/api/metabot.ts](metabase/enterprise/frontend/src/metabase-enterprise/api/metabot.ts) | 企业版 Metabot API |
| [enterprise/frontend/src/metabase-enterprise/](metabase/enterprise/frontend/src/metabase-enterprise/) | 企业版 Metabot 功能 |

---

## 九、SSE 流式协议

### AI SDK v4 Line Protocol

Metabot 使用 **AI SDK v4 Line Protocol**，而非自己定义 SSE 事件格式：

```
f:{"messageId":"msg_abc123"}
0:"我正在"
0:"分析"
0:"您的"
0:"问题..."
9:["create_sql_query","{\"sql\":\"SELECT...\"}"]
a:{"toolCallId":"call_001","toolName":"create_sql_query","args":{"sql":"..."}}
3:{"toolCallId":"call_001","output":"ok","queryId":"abc123"}
d:{"finishReason":"stop","usage":{"promptTokens":1500,"completionTokens":300}}
```

**流类型码**:
| 码 | 类型 | 说明 |
|----|------|------|
| `0` | text-start | 文本开始 |
| `1` | text-delta | 文本增量 |
| `2` | text-end | 文本结束 |
| `9` | tool-input-start | 工具调用开始 |
| `a` | tool-input-delta | 工具参数增量 |
| `b` | tool-input-available | 工具参数完整 |
| `3` | tool-output-available | 工具执行结果 |
| `d` | finish-step | 步骤完成 |
| `e` | finish | 流结束 |

---

## 十、与 Dataease/SQLBot 的关键差异总结

| 维度 | Metabase (Metabot) | Dataease + SQLBot |
|------|-------------------|-------------------|
| **AI 定位** | 全功能 AI 助手（SQL + 图表 + 仪表板 + 搜索 + 导航） | 专注 SQL 问答 + 图表 |
| **技术栈** | Clojure (单一 JVM) | Java + Python (双服务) |
| **交互模式** | **Agent 多轮迭代** | **单次 Pipeline** |
| **工具调用** | 40+ scoped tools, Function Calling | 无（LLM 直接输出结构化 JSON） |
| **Schema 获取** | 通过 `get_table_metadata` 工具按需获取 | embedding 预选 + 全部嵌入 Prompt |
| **SQL 执行** | 通过 Metabase Driver 层 (统一 QP) | SQLAlchemy 直连各数据库 |
| **图表生成** | `create_chart` 工具 → 后端渲染 | LLM 生成图表 JSON → 独立图片服务 |
| **嵌入方式** | Embedding SDK (iframe + postMessage) | assistant.js (iframe + postMessage) |
| **SSE 协议** | AI SDK v4 Line Protocol (业界标准) | 自定义 JSON SSE 事件 |
| **权限控制** | Scope + Metabot Permission 设置 | Dataease 行权限 + xpack 过滤器 |
| **对话持久化** | `metabot_conversation` + `metabot_message` 表 | `chat` + `chat_record` + `chat_log` 表 |
| **多用户/多租户** | Metabot 实例 → 用户/群组权限 | Chat → 用户/工作空间 (oid) |
| **离线能力** | 需要 LLM API | 需要 LLM API |
| **依赖** | 仅 JVM + Metabase | JVM (Dataease) + Python (SQLBot) + PostgreSQL |
