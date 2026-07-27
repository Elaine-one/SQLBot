# SQLBot 问答执行流程与 Dataease 集成模式分析

> 编写日期: 2026-07-13
> 基于代码版本: SQLBot-main (Python/FastAPI) + Dataease 2.10.25 (Java/Spring Boot)

---

## 一、架构总览

### 1.1 系统关系

SQLBot 是一个独立的 AI 问答引擎（Python/FastAPI），Dataease 通过**嵌入式助理（Assistant Embedding）**将 SQLBot 集成到自己的仪表板中。

```
┌────────────────────────────────────────────────────────────────────┐
│                        Dataease (Java BI 平台)                      │
│                                                                     │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────┐ │
│  │ 数据源管理     │  │ 数据集建模        │  │ 仪表板               │ │
│  │ (连接配置)     │  │ (Union/Join/SQL) │  │ (iframe 嵌入 SQLBot) │ │
│  └──────────────┘  └────────┬─────────┘  └──────────┬───────────┘ │
│                              │                        │             │
│                              │  元数据 API            │ 加载        │
│                              │  /sqlbot/datasource    │ assistant.js│
│                              │  /sqlbot/dataset       │             │
└──────────────────────────────┼────────────────────────┼─────────────┘
                               │                        │
                               │  (高级应用 type=1 时    │
                               │   动态调用)             │
                               ▼                        ▼
┌────────────────────────────────────────────────────────────────────┐
│                        SQLBot (Python AI 引擎)                      │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 助理管理   │  │ LLM 集成  │  │ SQL生成   │  │ 图表生成          │  │
│  │ (嵌入配置) │  │ (多模型)  │  │ (NL→SQL) │  │ (Chart Config)   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              LLMService.run_task() 统一 Pipeline               │  │
│  │  选择数据源 → 构建Schema → LLM生成SQL → 执行SQL → 生成图表     │  │
│  └──────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

### 1.2 Dataease 自己的 AI 能力

Dataease **自身没有任何 AI 能力**。其 Java 端仅有三个桥接组件：

| 组件 | 文件 | 作用 |
|------|------|------|
| `AiBaseService` | `.../ai/service/AiBaseService.java` | 仅返回一个 `ai.baseUrl` 配置值 |
| `DatasetSQLBotServer` | `.../dataset/server/DatasetSQLBotServer.java` | 暴露 `/sqlbot/datasource` 和 `/sqlbot/dataset` API |
| `DatasetSQLBotManage` | `.../dataset/manage/DatasetSQLBotManage.java` | 构建数据集元数据（权限过滤+SQL重建） |

Dataease 前端的 `assistant.vue` 仅做三件事：
1. 从 SQLBot 域名加载 `assistant.js` 脚本
2. 在 SQLBot iframe 的 DOM 中注入数据集选择器组件（`SQDatasetSelect.vue`）
3. 通过 `window['sqlbot_assistant_handler']` 调用 SQLBot 的方法（如 `createConversation`）

---

## 二、两种运行模式

SQLBot 支持两种问答入口，底层共用同一套 `LLMService.run_task()` Pipeline：

| | SQLBot 独立模式 | Dataease 嵌入模式 (Assistant) |
|---|---|---|
| **入口** | SQLBot 前端页面 (`/chat`) | Dataease 仪表板 → iframe (Assistant) |
| **API** | `POST /api/v1/chat/start` (origin=0) | `POST /api/v1/chat/assistant/start` (origin=2) |
| **数据源来源** | SQLBot 自己的 `core_datasource` 表 | SQLBot 的 `core_datasource` 表（基本应用 type=0）或 **Dataease 的 `/sqlbot/datasource` API**（高级应用 type=1） |
| **表选择** | embedding 相似度排序 + top N | 助理配置中预定义的表（无 embedding） |
| **Schema 构建** | `get_table_schema()` (datasource/crud/datasource.py) | `AssistantOutDs.get_db_schema()` (system/crud/assistant.py) |
| **LLM Pipeline** | **完全相同** `LLMService.run_task()` | **完全相同** `LLMService.run_task()` |

---

## 三、SQLBot 独立模式完整执行流程

### 3.1 入口：创建对话

```
POST /api/v1/chat/start
```

**文件**: [backend/apps/chat/api/chat.py:185-199](backend/apps/chat/api/chat.py#L185-L199)

请求体 `CreateChat`（[chat_model.py:166-170](backend/apps/chat/models/chat_model.py#L166-L170)）:
```python
class CreateChat(BaseModel):
    id: int = None
    question: str = None          # 可选的第一个问题
    datasource: int = None        # 数据源ID
    origin: Optional[int] = 0     # 0=页面, 1=MCP, 2=Assistant
```

创建 `Chat` 记录后返回 `ChatInfo`。

### 3.2 提交流：处理问题

```
POST /api/v1/chat/question
```

**文件**: [backend/apps/chat/api/chat.py:270-274](backend/apps/chat/api/chat.py#L270-L274)

```python
async def question_answer(session, current_user, request_question, current_assistant):
    return await question_answer_inner(session, current_user, request_question,
                                        current_assistant, embedding=True)
```

`question_answer_inner`（[chat.py:277-351](backend/apps/chat/api/chat.py#L277-L351)）的处理逻辑：

```
解析用户输入
├── 包含快捷命令 (/regenerate, /analysis, /predict)
│   ├── /regenerate → stream_sql()
│   ├── /analysis   → analysis_or_predict()
│   └── /predict    → analysis_or_predict()
└── 普通问题 → stream_sql()
```

### 3.3 核心：`stream_sql()` → `LLMService.run_task()`

**文件**: [backend/apps/chat/api/chat.py:370-407](backend/apps/chat/api/chat.py#L370-L407), [backend/apps/chat/task/llm.py:1176-1475](backend/apps/chat/task/llm.py#L1176-L1475)

```python
# stream_sql:
llm_service = await LLMService.create(session, current_user, request_question, current_assistant)
llm_service.init_record(session)           # 创建 ChatRecord
llm_service.run_task_async(in_chat=True)   # 提交到线程池异步执行
# ...
return StreamingResponse(llm_service.await_result(), media_type="text/event-stream")
```

### 3.4 LLMService.run_task() 完整 Pipeline

**文件**: [backend/apps/chat/task/llm.py:1176-1475](backend/apps/chat/task/llm.py#L1176-L1475)

```
┌─────────────────────────────────────────────────────────────────────┐
│ Phase 1: 数据源选择                                                  │
│                                                                      │
│ ┌─ self.ds 已存在? ─────────────────────────────────────────────┐   │
│ │ YES → validate_history_ds() 验证历史数据源有效性               │   │
│ │ NO  → select_datasource()                                      │   │
│ │       ├── 1个数据源 → 自动选择 (跳过LLM)                       │   │
│ │       ├── 多个数据源 → LLM选择                                  │   │
│ │       │    └── TABLE_EMBEDDING_ENABLED时: embedding排序数据源   │   │
│ │       └── 返回 JSON {"id": N}                                  │   │
│ └────────────────────────────────────────────────────────────────┘   │
│                            │                                         │
│                            ▼                                         │
│ Phase 2: 连接检查                                                    │
│   check_connection(ds) → 验证数据库可达                              │
│                            │                                         │
│                            ▼                                         │
│ Phase 3: 过滤上下文信息                                               │
│   ├── filter_terminology_template()  → embedding匹配术语              │
│   ├── filter_training_template()     → embedding匹配SQL训练示例       │
│   └── filter_custom_prompts()        → 自定义提示词                   │
│                            │                                         │
│                            ▼                                         │
│ Phase 4: init_messages() 构建 Prompt                                  │
│   ├── choose_table_schema()                                         │
│   │   ├── get_table_schema()                                        │
│   │   │   ├── get_table_obj_by_ds() → 读取所有表的字段元数据         │
│   │   │   ├── 构建 M-Schema 格式:                                    │
│   │   │   │   【DB_ID】 xxx                                         │
│   │   │   │   【Schema】                                            │
│   │   │   │   # Table: db.table_name, comment                       │
│   │   │   │   [                                                     │
│   │   │   │   (field_name:field_type, comment)                       │
│   │   │   │   ...                                                   │
│   │   │   │   ]                                                     │
│   │   │   ├── calc_table_embedding() ← embedding相似度排序          │
│   │   │   │   ├── embed_query(question) → 1次embedding API调用      │
│   │   │   │   └── 余弦相似度排序 → 取 top TABLE_EMBEDDING_COUNT    │
│   │   │   └── 关联表的字段关系处理                                   │
│   │   └── get_tables_sample_data() → SELECT * LIMIT 3 × N表        │
│   │                                                                  │
│   └── 组装完整的 messages 列表:                                      │
│       [System] 系统角色定义                                          │
│       [Human]  SQL生成规则 (引号/limit/多表/JSON格式/图表类型)        │
│       [AI]     "我已掌握所有规则..."                                  │
│       [Human]  Schema + Sample Data                                  │
│       [AI]     "我已确认数据库信息..."                                 │
│       [Human]  (可选) 术语/Training/Custom Prompt                    │
│       [AI]     (可选) "我已确认..."                                   │
│       [Human]  历史对话 (最近 N 轮, 默认3)                            │
│                            │                                         │
│                            ▼                                         │
│ Phase 5: generate_sql() LLM生成SQL                                   │
│   ├── 追加用户问题到 sql_message                                     │
│   ├── llm.stream(sql_message)                                       │
│   ├── process_stream() 解析 streaming chunk                          │
│   └── LLM返回 JSON:                                                  │
│       {"success":true, "sql":"...", "tables":[...],                  │
│        "chart-type":"bar", "brief":"..."}                            │
│                            │                                         │
│                            ▼                                         │
│ Phase 6: check_sql() 解析SQL                                         │
│   ├── JSON解析 → 提取 sql, tables, chart-type, brief                │
│   └── 保存到 ChatRecord                                              │
│                            │                                         │
│                            ▼                                         │
│ Phase 7: 权限过滤 (条件触发)                                          │
│   ├── 普通用户 → generate_filter() → LLM注入行权限WHERE条件           │
│   └── 助理模式 → generate_assistant_dynamic_sql() → 动态子查询       │
│                            │                                         │
│                            ▼                                         │
│ Phase 8: execute_sql() 执行SQL                                       │
│   ├── sqlparse 格式化SQL                                             │
│   ├── 直接连接目标数据库 (MySQL/PG/SQLServer/Oracle/ClickHouse...)   │
│   ├── sqlglot 校验只读 (禁止 INSERT/UPDATE/DELETE/DROP)              │
│   ├── 执行并返回 {"fields":[...], "data":[{...}], "sql":"base64"}  │
│   ├── 大数字安全转换                                                 │
│   └── 保存到 ChatRecord.data                                        │
│                            │                                         │
│                            ▼                                         │
│ Phase 9: generate_chart() LLM生成图表                                 │
│   ├── 重新获取使用的表 schema (table_list=tables)                    │
│   ├── llm.stream(chart_message) → 图表JSON配置                       │
│   └── check_save_chart() 验证并保存                                  │
│                            │                                         │
│                            ▼                                         │
│ Phase 10: request_picture() 渲染图表图片 (可选)                        │
│   └── POST chart config + data → MCP_IMAGE_HOST → PNG                │
│                                                                      │
│ 全部输出通过 SSE (Server-Sent Events) 流式返回                        │
│ 事件类型: id|question|datasource-result|sql-result|sql|sql-data|     │
│          chart-result|chart|brief|error|finish                        │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.5 LLM Prompt 结构

**文件**: [backend/apps/chat/models/chat_model.py:235-262](backend/apps/chat/models/chat_model.py#L235-L262), [backend/templates/template.yaml](backend/templates/template.yaml)

发给 LLM 的消息列表（`init_messages()` 构建）：

```
1. [System]    角色定义: "你是SQLBot，一个专业的SQL生成助手..."
2. [Human]     SQL规则: 引号规则 + LIMIT规则 + 多表规则 + JSON格式规则 + 图表类型规则
               + 数据库特定的SQL示例 (template.yaml + sql_examples/{DB}.yaml)
               + 严格的标识符保持规则 (priority=critical)
3. [AI]        "我已掌握所有规则..."
4. [Human]     Schema信息: <db-engine> + <m-schema> + <sample-data>
5. [AI]        "我已确认数据库信息..."
6. [Human]     (可选) 术语/Training/Custom Prompt
7. [AI]        (可选) 确认消息
8. [Human]     历史对话 (最近N轮, 默认3轮, 可配置 chat.context_record_count)
9. [Human]     用户当前问题 + 当前时间 + 上次SQL错误(如有)
```

**期望的 LLM 返回格式**:

```json
// 成功:
{"success":true, "sql":"SELECT ...", "tables":["table1"], "chart-type":"bar", "brief":"标题"}

// 失败:
{"success":false, "message":"无法生成的原因"}
```

---

## 四、Dataease 嵌入模式 (Assistant) 完整执行流程

### 4.1 助理类型

SQLBot 助理支持以下类型（[sys_assistant.type](backend/apps/system/models/system_model.py) 字段）：

| type | 名称 | 数据源来源 |
|------|------|-----------|
| 0 | 基本应用 | SQLBot 自己的 `core_datasource` 表（按工作空间过滤） |
| 1 | 高级应用 | 调用配置的 API endpoint（如 Dataease 的 `/sqlbot/datasource`）动态获取，属 `dynamic_ds_types` |
| 2 | （同 type 0） | SQLBot 自己的 `core_datasource` 表 |
| 3 | 高级应用（动态） | 同 type 1，也在 `dynamic_ds_types = [1, 3]` 列表中 |
| 4 | 页面嵌入 | 特殊类型，用于全屏嵌入（`is_page_embedded` 逻辑） |

> **注意**: Dataease 使用的是 type 0 还是 type 1 由 SQLBot 管理员在创建助理应用时配置，不是由 Dataease 代码决定的。从架构上看 type=1 更合理（避免数据源配置重复），但具体取决于实际部署配置。

### 4.2 Dataease 端初始化流程

**文件**: [Dataease core-frontend/src/views/sqlbot/assistant.vue](dataease-2.10.25/core/core-frontend/src/views/sqlbot/assistant.vue)

```
1. Dataease 仪表板加载 assistant.vue
   │
2. loadSqlbotInfo() → GET /sysParameter/sqlbot
   │  获取 {domain: "https://sqlbot-host", id: "assistant-app-id",
   │         enabled: true, valid: true}
   │
3. loadSqlbotPage()
   │  → 创建 <script> 标签加载 SQLBot 的 assistant.js:
   │    src = "{domain}/assistant.js?id={id}&online=true&userFlag={uid}"
   │
4. assistant.js 初始化 (详细见下方 4.3)
   │
5. mountedEmbeddedPage()
   │  → 在 SQLBot iframe 容器中挂载 Dataease 组件:
   │    ├── SQDatasetSelect → 数据集选择下拉框
   │    └── AssistantHead  → 头部提示文字
   │
6. SQDatasetSelect 初始化:
   │  → GET /sqlbot/dataset/{dvInfo.id} (Dataease Java API)
   │  → 获取数据集列表 [{tableId, tableName, dsId, dsName}]
   │  → 默认选第一个, 存入 localStorage:
   │      localStorage.setItem('dsId', dsId)
   │      localStorage.setItem('tableId', tableId)
   │  → 调用 handler.createConversation() 通知 SQLBot iframe
```

### 4.3 assistant.js 执行流程

**文件**: [SQLBot-main/frontend/public/assistant.js](SQLBot-main/frontend/public/assistant.js)

```
1. 扫描所有 <script id^="sqlbot-assistant-float-script-"> 标签
   │
2. 对每个标签的 src 提取 id 参数
   │
3. loadScript(src, id):
   │  ├── GET {domain}/api/v1/system/assistant/info/{id}
   │  │   获取 AssistantModel (含 type, configuration, domain)
   │  │
   │  ├── 解析 configuration JSON → 合并配置 (UI主题, float_icon, logo等)
   │  │
   │  ├── initsqlbot_assistant(data):
   │  │   ├── 创建浮动按钮 (chat_button)
   │  │   └── 创建 iframe: src="{domain}/#/assistant?id={id}&online=..."
   │  │
   │  └── registerMessageEvent(id, data):
   │       ├── 监听 SQLBot iframe 的 postMessage
   │       ├── 收到 busi='ready':
   │       │   ├── IF data.type === 1 (高级应用):
   │       │   │   └── parsrCertificate() → 从 localStorage/cookie/sessionStorage
   │       │   │       读取凭证 → 发送 busi='certificate' + certificate
   │       │   └── ELSE (基本应用 type=0):
   │       │       └── 仅发送 hostOrigin
   │       └── 暴露全局方法: setOnline(), refresh(), destroy(),
   │           setHistory(), createConversation()
   │
4. window.sqlbot_assistant_handler[id] = {
       setOnline, refresh, destroy, setHistory, createConversation
   }
```

### 4.4 SQLBot Assistant 页面初始化

**文件**: [SQLBot-main/frontend/src/views/embedded/index.vue](SQLBot-main/frontend/src/views/embedded/index.vue)

```
1. onBeforeMount():
   │  ├── 从 URL 读取参数: id, online, userFlag, history
   │  │
   │  ├── GET /api/v1/system/assistant/validator?id={id}&virtual={userFlag}
   │  │   ├── 从数据库读取助理配置 (type, configuration)
   │  │   └── 生成 access_token → 返回 {valid:true, token:"..."}
   │  │
   │  ├── assistantStore.setToken(token)
   │  ├── assistantStore.setAssistant(true)
   │  │
   │  ├── window.parent.postMessage({busi:'ready', ready:true}) → 通知外部页面
   │  │
   │  └── GET /api/v1/system/assistant/{id} → 获取UI配置(主题/logo/欢迎语)
   │
2. 监听外部 postMessage:
   │  ├── busi='certificate':
   │  │   └── assistantStore.setCertificate(certificate)
   │  │       → 用于调用 type=1 时的外部 API 认证
   │  ├── busi='createConversation':
   │  │   └── createChat() → chatApi.startAssistantChat()
   │  │       → POST /api/v1/chat/assistant/start
   │  ├── busi='setOnline': → assistantStore.setOnline()
   │  └── busi='setHistory': → assistantStore.setHistory()
```

### 4.5 助理模式数据源获取

**type=0（基本应用）**:
```python
# assistant.py get_ds_from_api() [line 39-51]
# 直接查询 SQLBot 自己的 core_datasource 表
stmt = select(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description).where(
    CoreDatasource.oid == oid)
if not assistant.online:
    stmt = stmt.where(CoreDatasource.id.in_(public_list))
```

**type=1（高级应用）**:
```python
# assistant.py:123-157 [AssistantOutDs.get_ds_from_api()]
config = json.loads(self.assistant.configuration)
endpoint = config['endpoint']              # ← Dataease 的 /sqlbot/datasource
certificateList = json.loads(self.certificate)

# 构造认证信息
for item in certificateList:
    if item['target'] == 'header': header[item['key']] = item['value']
    if item['target'] == 'cookie': cookies[item['key']] = item['value']
    if item['target'] == 'param': param[item['key']] = item['value']

# 🔴 调用 Dataease API 获取数据源
res = requests.get(url=endpoint, params=param, headers=header, cookies=cookies)

# 转换 Dataease 的响应 → AssistantOutDsSchema
for item in result_json.get('data', []):
    ds = self.convert2schema(item, config)
```

**Dataease 端如何处理此请求**:

```
GET /sqlbot/datasource → DatasetSQLBotServer.getDatasourceList()
    → DatasetSQLBotManage.getDatasourceList(dsId, datasetId)
        │
        ├── 查询数据库: core_datasource + core_dataset_group
        │               + core_dataset_table + core_dataset_table_field
        │
        ├── 构建 DataSQLBotAssistantVO:
        │   ├── buildDs()    → 数据源连接信息 (host/port/user/password/type)
        │   ├── buildTable() → 表信息 (name, comment, sql, needTransform)
        │   └── buildField() → 字段信息 (name=cdtf_origin_name, comment=cdtf_name)
        │
        ├── filterPermissions() → 列权限过滤 + 行权限注入
        │   └── rebuildTable()  → 当 needTransform=true 时:
        │       ├── 解析 Union/Join 数据集定义
        │       ├── 生成完整的子查询 SQL
        │       ├── 应用权限 WHERE 条件
        │       └── table.sql = querySQL  (数据集子查询)
        │
        └── 返回 List<DataSQLBotAssistantVO>
```

**DataSQLBotAssistantVO 数据结构**（[SQLBotAssistantField.java](dataease-2.10.25/sdk/api/api-base/src/main/java/io/dataease/api/dataset/vo/SQLBotAssistantField.java)）:

```java
public class SQLBotAssistantField {
    private String name;          // cdtf_origin_name → SQL中的列别名(如 "行小计USD")
    private String comment;       // cdtf_name → 用户重命名的显示名(如 "订单总额usd")
    private String type;          // cdtf_type → 字段类型
    // @JsonIgnore
    private String dataeaseName;  // Dataease内部名称 — 不传给SQLBot!
}
```

### 4.6 助理模式 Schema 构建

**文件**: [backend/apps/system/crud/assistant.py:183-225](backend/apps/system/crud/assistant.py#L183-L225)

```python
def get_db_schema(self, ds_id, question='', embedding=True, table_list=None):
    ds = self.get_ds(ds_id)  # 获取 AssistantOutDsSchema
    schema_str = ""
    db_name = ds.db_schema or ds.dataBase
    schema_str += f"【DB_ID】 {db_name}\n【Schema】\n"

    for table in ds.tables:
        # 表名 + comment
        schema_table = f"# Table: {db_name}.{table.name}, {table.comment}\n[\n"

        for field in table.fields:
            # 🔴 当 comment ≠ name 时标注"业务名称"
            if field.comment and field.comment != field.name:
                field_str = f"({field.name}:{field.type}, 业务名称:{field.comment})"
            else:
                field_str = f"({field.name}:{field.type}, {field.comment})"
            field_list.append(field_str)

        schema_table += ",\n".join(field_list) + '\n]\n'

    # 🔴 注意: embedding 被注释掉了!
    # if embedding and tables and settings.TABLE_EMBEDDING_ENABLED:
    #     tables = get_table_embedding(tables, question)

    return schema_str, []
```

**关键差异**：
- 独立模式：`calc_table_embedding()` → embedding API调用 → 排序 → 取 top N
- 助理模式：**embedding 被注释掉**，所有配置的表直接全部包含在 prompt 中

---

## 五、两种模式的关键差异对比

### 5.1 元数据来源

| | SQLBot 独立模式 | Dataease 嵌入模式 |
|---|---|---|
| **数据源配置** | 在 SQLBot 中手动添加 (`POST /api/v1/datasource`) | type=0: SQLBot 配置; type=1: Dataease 配置 |
| **表元数据** | `sync_table()` 从数据库自动拉取 | type=0: SQLBot 同步; type=1: Dataease API 返回 |
| **字段元数据** | `sync_fields()` 从数据库自动拉取 | type=0: SQLBot 同步; type=1: Dataease API 返回 |
| **字段名来源** | `field_name` (物理列名) | `name` (cdtf_origin_name, SQL别名); `comment` (cdtf_name, 用户重命名) |
| **数据建模** | ❌ 无（仅物理表） | type=1: ✅ 支持 Union/Join/自定义SQL/计算字段 |

### 5.2 Prompt 构建

| | SQLBot 独立模式 | Dataease 嵌入模式 |
|---|---|---|
| **表选择方式** | embedding 相似度排序 → top N | 助理配置预定义（全部包含） |
| **embedding 开销** | ✅ 1次 embed_query + N次余弦计算 | ❌ 被注释掉，跳过 |
| **sample data** | 每张表 `SELECT * LIMIT 3` | ❌ 不获取（没有此步骤） |
| **表数量** | 最多 N 张（默认10，可配置） | 取决于助理配置（通常1-3张） |
| **Prompt 大小** | 大（3000-5000 tokens） | 小（500-1500 tokens） |

### 5.3 字段命名处理

这是两个模式共有的关键设计：

```python
# 当字段的业务名称(comment)与列名(name)不同时：
# 格式: (列名:类型, 业务名称:别名)
# 示例: (行小计USD:numeric, 业务名称:订单总额usd)
#        ↑ SQL中必须使用这个      ↑ LLM绝对不能用于SQL
```

对应的提示词规则（[template.yaml:99-110](backend/templates/template.yaml#L99-L110)）:

```
<rule priority="critical" id="column-business-name-rule">
  当<m-schema>中的字段定义包含"业务名称:"标注时：
  1. "业务名称:"后面的文本绝对不能作为SQL中的列名使用
  2. SQL中必须使用括号内逗号前的第一个标识符
  3. 示例：(行小计USD:numeric, 业务名称:订单总额usd)
     → SQL中必须使用 "行小计USD"，绝对不能使用 "订单总额usd"
</rule>
```

### 5.4 性能差异

| 环节 | 独立模式 (30张表→取10) | 嵌入模式 (2张表) |
|---|---|---|
| 读取表元数据 | ~50ms | ~10ms |
| embedding API | ~200-500ms | **0ms** (跳过) |
| sample data查询 | 10次 × ~50ms = 500ms | **0ms** (不执行) |
| LLM Prompt tokens | ~3000-5000 | ~500-1500 |
| LLM 首token延迟 | ~3-5s | ~1-2s |
| LLM 生成时间 | ~5-10s | ~2-3s |
| **总计** | **~10-16s** | **~4-6s** |

### 5.5 后续的 run_task() Pipeline

**完全相同**。一旦 Schema 构建完成，后续的 SQL 生成、执行、图表生成走的是同一套 `LLMService.run_task()` 代码。

---

## 六、核心代码文件索引

### SQLBot (Python)

| 文件 | 说明 |
|------|------|
| [backend/apps/chat/api/chat.py](backend/apps/chat/api/chat.py) | Chat API 路由: `/start`, `/question`, `/assistant/start` |
| [backend/apps/chat/task/llm.py](backend/apps/chat/task/llm.py) | **核心**: `LLMService` 类, `run_task()` Pipeline (1900+行) |
| [backend/apps/chat/models/chat_model.py](backend/apps/chat/models/chat_model.py) | 数据模型: `Chat`, `ChatRecord`, `CreateChat`, `AiModelQuestion` |
| [backend/apps/ai_model/model_factory.py](backend/apps/ai_model/model_factory.py) | LLM 工厂: `LLMFactory`, `LLMConfig` (支持 OpenAI/Azure/Tongyi/VLLM) |
| [backend/apps/datasource/crud/datasource.py](backend/apps/datasource/crud/datasource.py) | 数据源 CRUD, `get_table_schema()`, `get_tables_sample_data()` |
| [backend/apps/datasource/embedding/table_embedding.py](backend/apps/datasource/embedding/table_embedding.py) | `calc_table_embedding()` — 表 embedding 排序 |
| [backend/apps/system/crud/assistant.py](backend/apps/system/crud/assistant.py) | **助理核心**: `AssistantOutDs`, `AssistantOutDsFactory`, `get_db_schema()` |
| [backend/apps/system/api/assistant.py](backend/apps/system/api/assistant.py) | 助理 API: `/info/{id}`, `/validator`, `/ds` |
| [backend/apps/db/db.py](backend/apps/db/db.py) | `exec_sql()` — 多数据库 SQL 执行引擎 |
| [backend/templates/template.yaml](backend/templates/template.yaml) | **Prompt 模板**: 系统角色/SQL规则/图表规则/输出格式 |
| [backend/templates/sql_examples/*.yaml](backend/templates/sql_examples/) | 各数据库特定的 SQL 示例 (MySQL/PG/SQLServer/Oracle...) |
| [frontend/public/assistant.js](frontend/public/assistant.js) | **助理嵌入脚本**: 浮动窗口创建/postMessage通信/凭证解析 |
| [frontend/src/views/embedded/index.vue](frontend/src/views/embedded/index.vue) | **助理 iframe 页面**: 初始化/认证/通信 |

### Dataease (Java)

| 文件 | 说明 |
|------|------|
| [core-backend/.../dataset/server/DatasetSQLBotServer.java](dataease-2.10.25/core/core-backend/src/main/java/io/dataease/dataset/server/DatasetSQLBotServer.java) | `/sqlbot/datasource` 和 `/sqlbot/dataset` API |
| [core-backend/.../dataset/manage/DatasetSQLBotManage.java](dataease-2.10.25/core/core-backend/src/main/java/io/dataease/dataset/manage/DatasetSQLBotManage.java) | **数据集元数据构建**: 权限过滤/SQL重建/字段映射 |
| [sdk/api/api-base/.../vo/SQLBotAssistantField.java](dataease-2.10.25/sdk/api/api-base/src/main/java/io/dataease/api/dataset/vo/SQLBotAssistantField.java) | 字段 VO: `name` (SQL别名), `comment` (显示名), `dataeaseName` (@JsonIgnore) |

### Dataease (前端)

| 文件 | 说明 |
|------|------|
| [core-frontend/src/views/sqlbot/assistant.vue](dataease-2.10.25/core/core-frontend/src/views/sqlbot/assistant.vue) | 仪表板侧边栏助理入口 |
| [core-frontend/src/views/sqlbot/SQDatasetSelect.vue](dataease-2.10.25/core/core-frontend/src/views/sqlbot/SQDatasetSelect.vue) | 数据集选择下拉框组件 |
| [core-frontend/src/views/sqlbot/index.vue](dataease-2.10.25/core/core-frontend/src/views/sqlbot/index.vue) | 全屏嵌入入口 |
| [core-frontend/src/views/system/parameter/third-party/ThirdEdit.vue](dataease-2.10.25/core/core-frontend/src/views/system/parameter/third-party/ThirdEdit.vue) | SQLBot 连接配置页面 |
| [core-frontend/src/api/aiSqlBot.ts](dataease-2.10.25/core/core-frontend/src/api/aiSqlBot.ts) | 前端 API: `findDvSqlBotDataset()` |

---

## 七、SSE 事件类型参考

`run_task()` 通过 Server-Sent Events 流式返回以下事件类型：

| 事件类型 | 说明 | 包含字段 |
|----------|------|---------|
| `id` | 记录ID | `id` |
| `question` | 用户问题 | `question` |
| `datasource-result` | 数据源选择结果 | `content`, `reasoning_content` |
| `datasource` | 选中的数据源 | `id`, `datasource_name`, `engine_type` |
| `sql-result` | LLM SQL生成流 | `content`, `reasoning_content` |
| `info` | 状态通知 | `msg` ("sql generated", "chart generated") |
| `brief` | 对话标题 | `brief` |
| `sql` | 格式化SQL | `content` (sqlparse格式化后) |
| `sql-data` | SQL执行结果 | `content` ("execute-success") |
| `chart-result` | LLM图表生成流 | `content`, `reasoning_content` |
| `chart` | 图表配置JSON | `content` (JSON字符串) |
| `error` | 错误 | `content`, `traceback` |
| `finish` | 完成 | 无额外字段 |
