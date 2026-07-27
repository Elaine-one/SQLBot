# SQLBot 连续对话架构修复方案

> 编写日期: 2026-07-14
> 状态: 设计方案，待评审
> 解决: 连续对话"失忆"问题（每个新问题都是全新 session，agent 不知道之前做了什么）

---

## 目录

1. [问题诊断：不是 bug，是架构缺陷](#一问题诊断)
2. [设计原则：根本修复 vs 打补丁](#二设计原则)
3. [核心约束：Agent Memory ≠ 前端展示](#三核心约束agent-memory--前端展示)
4. [目标架构：Session-Scoped State](#四目标架构)
5. [状态持久化设计](#五状态持久化设计)
6. [Agent 循环设计](#六agent-循环设计)
7. [SSE 协议统一](#七sse-协议统一)
8. [前端适配](#八前端适配)
9. [实施计划](#九实施计划)
10. [文件变更清单](#十文件变更清单)

---

## 一、问题诊断

### 1.1 核心症状

从日志验证（连续两个问题在同一 chat_id=28 中）：

```
问题1: "商品主要发往那些国家？" → Agent 10 轮迭代（search → metadata → create_sql → execute）
问题2: "各国家的订单量排名如何"  → Agent 9 轮迭代（search → metadata → create_sql → execute）
                                    ↑ 完全重新探索，不知道问题1已经发现了 erp_orders、erp_countries
```

问题 2 的 Agent **完全不知道**问题 1 做过什么——探索了哪些表、创建了什么查询、得到了什么结果。

### 1.2 根本原因：State Lifecycle 错误

当前架构中，**Agent 的状态生命周期是 request-scoped（一次请求），但应该是 session-scoped（整个对话会话）**。

```
当前（错误）:
  Question 1 → new AgentMemory() → Agent runs → AgentMemory 销毁
  Question 2 → new AgentMemory() → Agent runs → AgentMemory 销毁
               ↑ 全新状态，零历史

目标（正确）:
  Chat 创建   → new AgentMemory() → 持久化到 DB
  Question 1 → 从 DB 恢复 AgentMemory → Agent runs → 保存回 DB
  Question 2 → 从 DB 恢复 AgentMemory → Agent runs → 保存回 DB
               ↑ 继承上一轮的所有状态
```

### 1.3 五个具体缺陷

| # | 缺陷 | 文件位置 | 影响 |
|---|------|---------|------|
| D1 | **AgentMemory 按请求创建**，不持久化 | [adapter.py:105](backend/apps/chat/agent/adapter.py#L105) | queries/charts/explored_tables/conversation_summary 全部丢失 |
| D2 | **LangGraph 无 checkpointer** | [graph.py:184](backend/apps/chat/agent/graph.py#L184) | `workflow.compile()` 没有 MemorySaver，跨轮消息不保存 |
| D3 | **历史消息不注入** | [adapter.py:121-126](backend/apps/chat/agent/adapter.py#L121-L126) | `is_followup=True` 只设布尔标记，前几轮 user/assistant 交换不注入 |
| D4 | **SSE 事件集不完整** | [executor.py:61-64](backend/apps/chat/agent/executor.py#L61-L64) vs [ChartAnswer.vue:161-215](frontend/src/views/chat/answer/ChartAnswer.vue#L161-L215) | Agent 发出的 `text-delta`/`tool-call`/`tool-result` 前端不处理 |
| D5 | **SQL 双重执行** | [executor.py:213-267](backend/apps/chat/agent/executor.py#L213-L267) | Agent tool `execute_sql_query` + post-processing `_execute_and_chart` 各执行一次 |

### 1.4 为什么现有文档没发现这些问题

[升级方案](sqlbot-agent-upgrade-plan.md) 中设计了 MemorySaver + thread_id 的 checkpoint 机制（第 3.1、6.2 节），但实际实现时：

- [graph.py:184](backend/apps/chat/agent/graph.py#L184) 调用了 `workflow.compile()` **没有传入 checkpointer**
- [adapter.py:105](backend/apps/chat/agent/adapter.py#L105) 每次都 `init_agent_memory()` 创建全新内存
- 升级方案中设计的 `edit_*` 工具虽然实现了，但因为 memory 是空的，追问时 `get_context_for_llm()` 返回空字符串

**结论**：不是设计错了，而是实现不完整。需要补齐状态持久化这一层。

---

## 二、设计原则

### 2.1 根本修复的定义

"打补丁"的做法：
```
if is_followup:
    load_previous_queries_from_db()  # 在每个工具入口加 ad-hoc 恢复逻辑
```

"根本修复"的做法：
```
# AgentMemory 的生命周期与 Chat 绑定
class Chat:
    memory_state: JSON  # AgentMemory 序列化存储
    
# 每次请求自动恢复/保存
memory = AgentMemory.from_db(chat_id)   # 透明恢复
agent.run(memory)
memory.to_db(chat_id)                   # 透明保存
```

判断标准：**状态管理逻辑集中在 2 个地方（序列化/反序列化），而不是散落在 10+ 个工具和 adapter 中。**

### 2.2 设计原则

| 原则 | 说明 |
|------|------|
| **State 生命周期 = Chat 生命周期** | AgentMemory 在 Chat 创建时初始化，随 Chat 删除而销毁 |
| **透明持久化** | Agent 代码不感知持久化；adapter 负责 save/restore |
| **关注点分离** | PersistentState（可序列化） vs RuntimeContext（每次注入） |
| **向后兼容** | 新字段允许 NULL；旧 Chat 没有 memory_state 时等同于首次对话 |
| **最小侵入** | Chat/ChatRecord 表结构不变，SSE 格式不变，前端 API 不变 |

### 2.3 不改的组件

| 组件 | 原因 |
|------|------|
| `Chat` / `ChatRecord` 表结构 | 加一个 JSON 列即可，不影响现有查询 |
| `POST /chat/question` API | 端点不变，内部逻辑改变 |
| SSE 外层格式 `data:{...}\n\n` | 前端解析框架不变 |
| LangGraph `StateGraph` 结构 | agent_node ↔ tools_node 循环不变 |
| 14 个工具的实现 | 工具本身不感知持久化（通过 memory 读写状态） |
| 前端 `ChartAnswer.vue` 核心流程 | 只增加新 event type 的 case |

---

## 三、核心约束：Agent Memory ≠ 前端展示

### 3.1 这是整个方案最容易误解的地方

Agent Memory 持久化后，一个自然的担忧是：

> "edit_sql_query 修改了 memory 中的查询，是不是会覆盖掉之前的图表？"

**答案：不会。Agent Memory 和 ChatRecord 是两个完全独立的层，互不覆盖。**

### 3.2 两层分离架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    Agent Memory（agent 的工作区）                   │
│                                                                    │
│  用途: 帮助 agent 做更聪明的决策                                     │
│  生命周期: 与 Chat 绑定，跨轮累积                                    │
│  可变性: **可变的** — edit_sql_query/edit_chart 直接修改            │
│  内容:                                                              │
│    queries: {r_1: {sql: "SELECT...", status: "executed"}}          │
│    charts:  {chart_r_1: {type: "column", ...}}                     │
│    explored_tables: {erp_orders: {fields: [...]}}                  │
│                                                                    │
│  不进入前端 ← 纯后端概念，前端永远看不到                              │
└──────────────────────────────────────────────────────────────────┘
                              │
                              │ 每轮结束时: 将执行结果写入新的 ChatRecord
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    DB ChatRecord（前端展示的数据）                    │
│                                                                    │
│  用途: 记录每一轮对话的完整结果                                       │
│  生命周期: 永久，直到用户删除 Chat                                    │
│  可变性: **不可变的** — 一旦保存，绝不回写                            │
│  内容:                                                              │
│    Record 70: question="商品发往哪些国家" chart={柱状图} data={...}  │
│    Record 71: question="用饼图展示"       chart={饼图}   data={复用} │
│    Record 72: question="只看华东区"       chart={折线图} data={新数据}│
│                                                                    │
│  进入前端 ← 前端按时间顺序展示所有 Record，只增不减                    │
└──────────────────────────────────────────────────────────────────┘
```

**核心原则：Agent Memory 是用来帮助 agent 做更聪明的决策的，不是用来决定前端显示什么的。前端永远按 ChatRecord 时间顺序展示所有历史图表——这是不可变的、只增不减的。**

### 3.3 三种追问场景详解

#### 场景 1：改图表类型（同一数据，不同呈现）— `edit_chart`

```
初始状态:
  Agent Memory:
    queries: {r_1: {sql: "SELECT c.name, COUNT(o.id) FROM ...", status: "executed",
                     data: {fields: [...], data: [10行]}}}
    charts:  {chart_r_1: {type: "column", record_id: "r_1"}}
  DB:
    Record 70: question="商品发往哪些国家", chart={type:"column",...}, data={10行}

第2轮用户: "用饼图展示"
  Agent 决策:
    → 用户要改变呈现方式，SQL 不用改
    → edit_chart(chart_ref="chart_r_1", type="pie")
    → memory.charts["chart_r_1"].chart_config.type = "pie"  ← memory 中更新

  Post-processing:
    → 数据已在 r_1.data 中，无需重新执行 SQL
    → 生成新图表配置 → 保存到 DB

  Agent Memory (更新后):
    charts: {chart_r_1: {type: "pie", ...}}  ← type 变了

  DB (只增不减):
    Record 70: question="商品发往哪些国家", chart={type:"column",...}  ← 不变！
    Record 71: question="用饼图展示",       chart={type:"pie",...}     ← 新增

前端显示:
  ┌─ Record 70 ───────────────────┐
  │ 商品发往哪些国家                │
  │ ┌────────────────────────────┐ │
  │ │  [柱状图]                   │ │  ← 保留，不变
  │ └────────────────────────────┘ │
  └────────────────────────────────┘
  ┌─ Record 71 ───────────────────┐
  │ 用饼图展示                     │
  │ ┌────────────────────────────┐ │
  │ │  [饼图]                     │ │  ← 新生成，复用 Record 70 的数据
  │ └────────────────────────────┘ │
  └────────────────────────────────┘
```

#### 场景 2：改查询条件（同一话题，不同维度）— `edit_sql_query`

```
初始状态: 同上

第2轮用户: "只看华东区"
  Agent 决策:
    → 用户要缩小数据范围，需要修改 SQL
    → 先检查 explored_tables → 知道 erp_orders 有 region 字段（已缓存）
    → edit_sql_query(record_id="r_1", edits=[
        {"old_string": "FROM \"erp_orders\"", "new_string": "FROM \"erp_orders\" WHERE \"region\" = '华东'"}
      ])
    → memory.queries["r_1"].sql 更新  ← memory 中 SQL 变了
    → terminal_triggered = True

  Post-processing:
    → 检测到 r_1.status = "edited" → 需要重新执行 SQL
    → 执行 → r_1.data 更新为新数据
    → 保存到 DB 作为新 Record

  Agent Memory (更新后):
    queries: {r_1: {sql: "SELECT ... WHERE region='华东'",  ← SQL 变了
                    data: {5行},                             ← 数据变了
                    status: "executed"}}

  DB (只增不减):
    Record 70: question="商品发往哪些国家", chart={柱状图, 全部地区}  ← 不变！
    Record 71: question="只看华东区",       chart={柱状图, 华东区}    ← 新增

前端显示:
  ┌─ Record 70 ───────────────────┐
  │ 商品发往哪些国家                │
  │ [柱状图: 全部地区, 10行]        │  ← 保留，不变
  └────────────────────────────────┘
  ┌─ Record 71 ───────────────────┐
  │ 只看华东区                     │
  │ [柱状图: 华东区, 5行]           │  ← 新数据，新图表
  └────────────────────────────────┘
```

#### 场景 3：全新话题（不同数据，不同查询）— `create_sql_query`

```
初始状态: 同上

第2轮用户: "库存周转率如何"
  Agent 决策:
    → "库存" vs "商品发往国家" → 完全不同的话题
    → Agent 可以用 search_relevant_tables 搜索（因为首次搜索"库存"）
    → creat_sql_query → r_2  ← 产生新 record_id
    → terminal_triggered = True

  Post-processing:
    → 执行 r_2 → 保存到 DB

  Agent Memory (累积):
    queries: {
      r_1: {sql: "...countries...", ...},  ← 保留
      r_2: {sql: "...inventory...", ...}   ← 新增
    }
    explored_tables: {
      erp_orders: {...},       ← r_1 的缓存，保留
      erp_countries: {...},    ← r_1 的缓存，保留
      erp_inventory: {...},    ← r_2 新缓存的
    }

  DB (只增不减):
    Record 70: question="商品发往哪些国家", chart={柱状图}    ← 保留
    Record 71: question="库存周转率如何",   chart={新图表}    ← 新增
```

### 3.4 追问时 Agent 如何决定 edit vs create

System prompt 中注入的决策规则：

```
## 追问模式决策规则

1. 用户要求修改图表类型/样式（"用柱状图""换个饼图""加标题"）
   → edit_chart  ← 不改 SQL，不复执行

2. 用户要求修改数据范围/条件（"只看华东区""加上利润列""去掉已取消订单"）
   → edit_sql_query  ← 字符串替换，重新执行

3. 用户要求修改查询逻辑（"换成按月份统计""改成按客户分组"）
   → 检查已有查询是否覆盖该维度
   → 覆盖 → edit_sql_query
   → 不覆盖 → create_sql_query

4. 用户提出全新话题（"库存周转率如何"）
   → create_sql_query  ← 需要新表、新字段、新逻辑

5. 用户说"导出""下载"
   → 数据已在 memory 中，直接使用，不调工具

关键约束:
- 你在 memory 中的 edit 操作不会删除历史 ChatRecord
- 前端独立于你的工作区，用户总能看到所有历史图表
- 不要试图"清理"或"替换"之前的结果——每次只产出当前这轮的结果
```

### 3.5 为什么 Metabase 也是这样做的

Metabase 的 chat UI 中可以看到所有历史卡片：

```
┌─ Metabase Chat ──────────────────────────┐
│                                           │
│  🙋 上月销售额趋势                          │
│  🤖 ┌──────────────────────────────────┐  │
│     │ [折线图: 全部地区]                 │  │  ← Card #1, 还在
│     └──────────────────────────────────┘  │
│                                           │
│  🙋 只看华东区                             │
│  🤖 ┌──────────────────────────────────┐  │
│     │ [折线图: 华东区]                   │  │  ← Card #2, 新 card
│     └──────────────────────────────────┘  │
│                                           │
│  🙋 用饼图展示                             │
│  🤖 ┌──────────────────────────────────┐  │
│     │ [饼图]                            │  │  ← Card #3, 复用 Card #1 的数据
│     └──────────────────────────────────┘  │
│                                           │
│  💬 输入你的问题...                         │
└───────────────────────────────────────────┘
```

每个 card 对应一个 `MetabotMessage`（类似 SQLBot 的 `ChatRecord`），不可变，只增不减。Agent 内部的 `memory.clj` 中的 queries/charts map 是可变的（为新 card 提供上下文），但它不影响已保存 card 的展示。

---

## 四、目标架构

### 4.1 State 分层

```
┌──────────────────────────────────────────────────────────────┐
│                   Chat Session (chat_id)                      │
│                                                               │
│  ┌─────────────────────────┐  ┌────────────────────────────┐ │
│  │  AgentMemory             │  │  LangGraph Checkpoint       │ │
│  │  (PersistentState)       │  │  (MemorySaver)              │ │
│  │                          │  │                             │ │
│  │  queries: {r_1: {...}}   │  │  messages: [System, Human,  │ │
│  │  charts: {c_1: {...}}    │  │    AI(tool_calls), Tool,    │ │
│  │  explored_tables: {...}  │  │    AI, ...]                 │ │
│  │  conversation_summary    │  │                             │ │
│  │  _search_cache: {...}    │  │  ← 自动管理（LangGraph）     │ │
│  │                          │  │  ← thread_id = chat_id      │ │
│  │  ← 序列化到 DB JSON 列    │  │  ← 仅在内存中（可切换       │ │
│  │  ← 请求边界恢复/保存      │  │    SqliteSaver）            │ │
│  └─────────────────────────┘  └────────────────────────────┘ │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  RuntimeContext (每次请求注入，不持久化)                    │ │
│  │  session, current_user, ds, out_ds_instance, record       │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### 4.2 关键决策：两层状态，各司其职

| | AgentMemory (DB) | LangGraph Checkpoint (MemorySaver) |
|---|---|---|
| **存什么** | 业务产出物（queries, charts）+ 探索缓存 + 对话摘要 | Agent 循环中的消息历史 |
| **用途** | 下一轮的 system prompt 注入 + 工具引用 | Agent 内部的状态恢复（工具链上下文） |
| **大小** | 小（几 KB JSON） | 可能大（消息历史 accumulated） |
| **持久化** | DB JSON 列 | 内存（MemorySaver），可选 DB |
| **跨进程** | ✅ | ❌（MemorySaver 在进程内存中） |

为什么不用 LangGraph checkpointer 存一切？因为 AgentMemory 中的 `session`、`current_user`、`ds` 是不可序列化的。分离后，可序列化的部分进 DB，不可序列化的部分每次注入。

### 4.3 完整数据流（两个连续问题）

```
═══════════════════════════════════════════════════════════════
Chat 创建 (POST /chat/start)
═══════════════════════════════════════════════════════════════

  Chat 表新增一行
  Chat.memory_state = NULL  （首次对话，无历史状态）

═══════════════════════════════════════════════════════════════
问题 1: "商品主要发往那些国家？"
═══════════════════════════════════════════════════════════════

  1. adapter.stream_agent()
     ├─ memory = AgentMemory.from_db(chat_id)  → NULL，初始化为空
     ├─ 注入 RuntimeContext (session, user, ds, record)
     └─ memory.conversation_summary = ""  （第一轮）

  2. AgentExecutor.run_async()
     ├─ graph.astream(initial_state, config={"thread_id": "chat_28"})
     │   └─ MemorySaver 记录每一轮 agent→tools→agent 的消息
     │
     ├─ Agent 调用 search_relevant_tables → 结果缓存到 memory._search_cache
     ├─ Agent 调用 get_table_metadata("erp_orders") → 缓存到 memory.explored_tables
     ├─ Agent 调用 get_table_metadata("erp_countries") → 缓存到 memory.explored_tables
     ├─ Agent 调用 create_sql_query(sql) → memory.queries["r_1"] = QueryRecord(...)
     │   └─ terminal_triggered = True → Agent 循环结束
     │
     └─ Post-processing: _execute_and_chart(query)
         ├─ 执行 SQL → memory.queries["r_1"].data = {...}
         ├─ 生成 chart → memory.charts["chart_r_1"] = ChartRecord(...)
         └─ memory.conversation_summary = "用户询问商品发往的国家。生成了查询r_1，统计各国家的订单数量。返回10行数据。"

  3. AgentMemory.to_db(chat_id)  → Chat.memory_state = JSON

═══════════════════════════════════════════════════════════════
问题 2: "各国家的订单量排名如何"（追问，同一 chat_id=28）
═══════════════════════════════════════════════════════════════

  1. adapter.stream_agent()
     ├─ memory = AgentMemory.from_db(chat_id)  → 恢复完整状态！
     │   ├─ memory.queries = {"r_1": QueryRecord(sql="SELECT ...", status="executed", data={...})}
     │   ├─ memory.charts = {"chart_r_1": ChartRecord(...)}
     │   ├─ memory.explored_tables = {"erp_orders": {...}, "erp_countries": {...}}
     │   └─ memory.conversation_summary = "用户询问商品发往的国家..."
     ├─ 注入 RuntimeContext
     └─ memory.is_followup = True

  2. AgentExecutor.run_async()
     ├─ System Prompt 包含:
     │   <conversation-history>用户询问商品发往的国家。生成了查询r_1，统计各国家的订单数量。</conversation-history>
     │   <last-query record_id="r_1">SELECT c.name, COUNT(o.id) FROM erp_orders o LEFT JOIN erp_countries c...</last-query>
     │
     ├─ Agent 看到已有 r_1 查询了 country + order count
     ├─ Agent 判断：用户要的是"排名"，r_1 的数据已经包含 country 和 count
     │   → 只需 edit_chart(r_1, 加排序) 或 edit_sql_query(r_1, 加 ORDER BY)
     │   → 不需要重新 search_relevant_tables！
     │
     └─ Agent 调用 edit_sql_query(record_id="r_1", edits=[...]) → 2-3 轮迭代完成

  3. AgentMemory.to_db(chat_id)  → 更新 Chat.memory_state
```

**关键差异**：问题 2 从 9 轮迭代降为 2-3 轮，因为 Agent 知道表结构已探索、查询已存在。

---

## 五、状态持久化设计

### 5.1 DB Schema 变更

```sql
-- Chat 表新增一个 JSON 列
ALTER TABLE chat ADD COLUMN memory_state JSON NULL;
```

不需要新表。`Chat` 是会话级别，一个 Chat 对应一个 AgentMemory。`memory_state` 列存 AgentMemory 的可序列化部分。

### 5.2 AgentMemory 序列化格式

```python
# AgentMemory 中需要持久化的字段（PersistentState）
PERSISTENT_FIELDS = [
    "queries",            # dict[str, QueryRecord]
    "charts",             # dict[str, ChartRecord]
    "explored_tables",    # dict[str, dict] — 表结构缓存
    "_search_cache",      # dict — 搜索结果缓存
    "conversation_summary",  # str — 对话摘要
    "iteration",          # int — 本次迭代计数（恢复时重置为0）
    "terminal_triggered", # bool — 恢复时重置为 False
    "sql_retry_count",    # int — 恢复时重置为 0
]

# 不持久化的字段（RuntimeContext，每次注入）
RUNTIME_FIELDS = [
    "session",
    "current_user",
    "ds",
    "out_ds_instance",
    "record",
    "is_followup",        # 由 adapter 根据 DB 查询结果设置
    "user_question",      # 本次请求的参数
    "chat_id",            # 本次请求的参数
]
```

序列化格式（存储在 `Chat.memory_state`）：

```json
{
  "queries": {
    "r_1": {
      "record_id": "r_1",
      "sql": "SELECT c.name AS country_name, COUNT(o.id) AS order_count ...",
      "tables_used": ["erp_orders", "erp_countries"],
      "status": "executed",
      "sql_history": [
        {"version": 1, "previous_sql": "", "modified_at": "2026-07-14T14:07:35"}
      ],
      "data": {"fields": [...], "data": [...]},
      "executed_at": "2026-07-14T14:07:35"
    }
  },
  "charts": {
    "chart_r_1": {
      "chart_ref": "chart_r_1",
      "record_id": "r_1",
      "chart_config": {"type": "column", "title": "商品发往国家分布", ...}
    }
  },
  "explored_tables": {
    "erp_orders": {
      "fields": [{"name": "id", "type": "int"}, ...],
      "row_count": 50000,
      "cached_at": "2026-07-14T14:07:15"
    }
  },
  "_search_cache": {
    "商品 发往 国家": [
      {"table_name": "erp_v_store_daily", "cosine_similarity": 0.457},
      {"table_name": "erp_orders", "cosine_similarity": 0.428}
    ]
  },
  "conversation_summary": "用户询问商品发往的国家。生成了查询r_1统计各国家订单数量。返回10行数据。",
  "version": 1
}
```

### 5.3 序列化/反序列化实现

```python
# agent/memory.py 新增方法

class AgentMemory:
    # ... 现有字段 ...

    def to_dict(self) -> dict:
        """序列化可持久化字段为 dict"""
        data = {"version": 1}
        data["queries"] = {
            k: {
                "record_id": v.record_id,
                "sql": v.sql,
                "tables_used": v.tables_used,
                "status": v.status,
                "sql_history": v.sql_history,
                "data": v.data,
                "executed_at": v.executed_at.isoformat() if v.executed_at else None,
            }
            for k, v in self.queries.items()
        }
        data["charts"] = {
            k: {
                "chart_ref": v.chart_ref,
                "record_id": v.record_id,
                "chart_config": v.chart_config,
                "image_url": v.image_url,
            }
            for k, v in self.charts.items()
        }
        data["explored_tables"] = self.explored_tables
        data["_search_cache"] = self._search_cache
        data["conversation_summary"] = self.conversation_summary
        return data

    @classmethod
    def from_dict(cls, data: dict | None) -> "AgentMemory":
        """从 dict 反序列化"""
        memory = cls()
        if not data:
            return memory

        for k, v in data.get("queries", {}).items():
            memory.queries[k] = QueryRecord(
                record_id=v["record_id"],
                sql=v["sql"],
                tables_used=v.get("tables_used", []),
                status=v.get("status", "created"),
                sql_history=v.get("sql_history", []),
                data=v.get("data"),
                executed_at=datetime.fromisoformat(v["executed_at"]) if v.get("executed_at") else None,
            )
        for k, v in data.get("charts", {}).items():
            memory.charts[k] = ChartRecord(
                chart_ref=v["chart_ref"],
                record_id=v["record_id"],
                chart_config=v.get("chart_config", {}),
                image_url=v.get("image_url"),
            )
        memory.explored_tables = data.get("explored_tables", {})
        memory._search_cache = data.get("_search_cache", {})
        memory.conversation_summary = data.get("conversation_summary", "")
        return memory
```

### 5.4 DB 读写

```python
# agent/adapter.py 新增

def _load_memory_from_db(session, chat_id: int) -> AgentMemory:
    """从 Chat.memory_state 恢复 AgentMemory"""
    from apps.chat.models.chat_model import Chat
    chat = session.get(Chat, chat_id)
    if chat and chat.memory_state:
        return AgentMemory.from_dict(chat.memory_state)
    return AgentMemory()  # 首次对话


def _save_memory_to_db(session, chat_id: int, memory: AgentMemory):
    """保存 AgentMemory 到 Chat.memory_state"""
    from apps.chat.models.chat_model import Chat
    chat = session.get(Chat, chat_id)
    if chat:
        # 仅保存可序列化部分
        chat.memory_state = memory.to_dict()
        session.add(chat)
        session.commit()
```

---

## 六、Agent 循环设计

### 6.1 修改后的 adapter.stream_agent()

```python
# agent/adapter.py

async def stream_agent(llm_service, session, question: str, is_followup: bool = False):
    # Lazy-init tools (once per process)
    if not hasattr(stream_agent, "_tools_registered"):
        register_all_tools()
        stream_agent._tools_registered = True

    llm = llm_service.llm
    record = llm_service.record
    chat_id = getattr(llm_service.chat_question, "chat_id", None)

    # ═══ 关键变更 1: 从 DB 恢复 AgentMemory ═══
    memory = _load_memory_from_db(session, chat_id) if chat_id else AgentMemory()

    # 注入 RuntimeContext（每次请求重新绑定）
    memory = init_agent_memory(llm_service)  # 仍调用此函数获取 ds 等信息
    # 但覆盖 queries/charts/explored_tables 为 DB 中的值
    if chat_id:
        restored = _load_memory_from_db(session, chat_id)
        memory.queries = restored.queries
        memory.charts = restored.charts
        memory.explored_tables = restored.explored_tables
        memory._search_cache = restored._search_cache
        memory.conversation_summary = restored.conversation_summary

    memory.session = session
    memory.user_question = question
    memory.is_followup = is_followup
    memory.record = record
    memory.chat_id = chat_id

    # ═══ 关键变更 2: 注入前几轮对话历史 ═══
    if is_followup and chat_id:
        history_context = _build_history_context(session, chat_id)
        memory.conversation_history = history_context

    # 发送 record ID
    if memory.record:
        yield sse("id", id=memory.record.id)
        yield sse("question", question=question)

    executor = AgentExecutor(llm, memory)
    executor.run_async()

    for chunk in executor.await_result():
        yield chunk

    # ═══ 关键变更 3: 对话完成后保存状态 ═══
    if chat_id:
        _save_memory_to_db(session, chat_id, memory)
```

### 6.2 修改后的 System Prompt 构建

```python
# agent/graph.py

def _build_system_prompt(memory: AgentMemory, is_followup: bool = False) -> str:
    ds_type = memory.datasource_type or "unknown"

    base = f"""你是 SQLBot，一个数据分析助手..."""  # 现有基础 prompt

    # ═══ 关键变更: 注入结构化上下文 ═══

    # 1. 对话历史摘要
    if memory.conversation_summary:
        base += f"""
## 历史对话摘要
{memory.conversation_summary}
"""

    # 2. 已有的查询列表（让 Agent 知道可以引用什么）
    if memory.queries:
        base += "\n## 已有查询\n"
        for qid, q in memory.queries.items():
            tables = ", ".join(q.tables_used)
            sql_preview = q.sql[:120] + "..." if len(q.sql) > 120 else q.sql
            base += f"- `{qid}`: {sql_preview} (表: {tables}, 状态: {q.status})\n"

    # 3. 已有的图表列表
    if memory.charts:
        base += "\n## 已有图表\n"
        for cid, c in memory.charts.items():
            ctype = c.chart_config.get("type", "unknown")
            base += f"- `{cid}`: 类型={ctype}, 绑定查询={c.record_id}\n"

    # 4. 追问模式指令（仅当有可引用的产物时）
    if is_followup and (memory.queries or memory.charts):
        base += """
## 追问模式
你正在处理追问。已有查询和图表已在上方列出。
- 修改图表类型/样式 → `edit_chart`
- 修改查询条件/列 → `edit_sql_query`
- 修改 SQL 片段 → `replace_sql_fragment`
- 全新话题 → 可以 `create_sql_query`
- 已探索的表结构已缓存，无需重新调用 `get_table_metadata`
"""

    # 5. 已探索的表（简要提示，不注入完整 schema）
    if memory.explored_tables:
        table_names = list(memory.explored_tables.keys())
        base += f"\n已缓存的表结构: {', '.join(table_names)}（无需重新获取）\n"

    return base
```

### 6.3 对话摘要生成

```python
# agent/executor.py — 在 _generate_chart 完成后

async def _finalize_turn(self):
    """生成本轮对话摘要，追加到 conversation_summary"""
    if not self.memory.queries:
        return

    latest = self.memory.get_latest_query()
    if not latest:
        return

    # 构造摘要（不调用 LLM，直接用结构化信息拼接）
    summary_parts = []
    summary_parts.append(f"用户问: {self.memory.user_question[:100]}")
    summary_parts.append(
        f"生成了查询 {latest.record_id}，涉及表 {', '.join(latest.tables_used)}"
    )
    if latest.data:
        row_count = len(latest.data.get("data", [])) if isinstance(latest.data, dict) else 0
        summary_parts.append(f"返回 {row_count} 行数据")
    if latest.status == "executed":
        summary_parts.append("查询已执行")

    # 追加到累积摘要
    new_summary = " | ".join(summary_parts)
    if self.memory.conversation_summary:
        self.memory.conversation_summary += f"\n第{len(self.memory.queries)}轮: {new_summary}"
    else:
        self.memory.conversation_summary = f"第1轮: {new_summary}"
```

### 6.4 解决 SQL 双重执行

```python
# agent/executor.py — _execute_and_chart 增加缓存检查

async def _execute_and_chart(self, query) -> None:
    """Execute a query (skip if already executed by agent tool)."""
    # ═══ 关键变更: 检查是否已执行 ═══
    if query.status == "executed" and query.data:
        _log.info(f"[Agent] query {query.record_id} already executed, reusing cached data")
        # 直接从缓存数据生成图表
        self._emit("sql-data", content="execute-success")
        await self._generate_chart(query)
        return

    # ... 原有执行逻辑 ...
```

### 6.5 LangGraph Checkpointer（可选优化）

```python
# agent/graph.py

from langgraph.checkpoint.memory import MemorySaver

# 进程级共享的 MemorySaver（所有 chat 共享）
# 注意：MemorySaver 在进程重启后丢失，但对同一进程内的连续对话有效
_shared_checkpointer = MemorySaver()

def build_agent_graph(llm, memory: AgentMemory):
    # ... 图定义不变 ...
    return workflow.compile(checkpointer=_shared_checkpointer)
    #                        ↑ 新增 checkpointer
```

这使 LangGraph 能跨轮恢复 agent 内部的消息状态（tool chain 上下文），但**不依赖它作为主要持久化机制**。AgentMemory 的 DB 持久化是主要机制。

---

## 七、SSE 协议统一

### 7.1 Agent 引擎完整事件集

| 事件类型 | 发出位置 | 含义 | 当前前端状态 |
|---------|---------|------|------------|
| `id` | adapter | 本次 ChatRecord ID | ✅ 已处理 |
| `question` | adapter | 用户问题文本 | ✅ 已处理 |
| `text-delta` | executor | Agent 文本输出（思考过程） | ❌ 未处理 |
| `tool-call` | executor | Agent 调用工具（工具名+参数） | ❌ 未处理 |
| `tool-result` | executor | 工具返回结果 | ❌ 未处理 |
| `sql` | executor | 最终 SQL（格式化后） | ✅ 已处理 |
| `sql-data` | executor | SQL 执行成功 | ✅ 已处理 |
| `chart-result` | executor | 图表 LLM 流式输出 | ✅ 已处理 |
| `chart` | executor | 最终图表配置 JSON | ✅ 已处理 |
| `error` | executor | 错误信息 | ✅ 已处理 |
| `finish` | executor | 本轮完成 | ✅ 已处理 |

### 7.2 事件 payload 格式规范

```python
# 统一的事件格式
def _emit(self, event_type: str, **kwargs) -> None:
    payload = {"type": event_type, **kwargs}
    self._chunk_list.append(
        "data:" + orjson.dumps(payload).decode() + "\n\n"
    )

# tool-call 事件示例
_emit("tool-call", tool_name="search_relevant_tables", args={"query": "商品 国家"})
# → data:{"type":"tool-call","tool_name":"search_relevant_tables","args":{"query":"商品 国家"}}

# tool-result 事件示例
_emit("tool-result", tool_name="search_relevant_tables", success=True,
      summary="找到10张候选表: erp_orders, erp_countries...")
# → data:{"type":"tool-result","tool_name":"search_relevant_tables","success":true,"summary":"找到10张候选表..."}

# text-delta 事件示例
_emit("text-delta", content="让我先搜索相关表...")
# → data:{"type":"text-delta","content":"让我先搜索相关表..."}
```

### 7.3 前端适配（最小改动）

```typescript
// ChartAnswer.vue — switch 中增加以下 case:

case 'text-delta':
    // Agent 的推理文本，追加到 sql_answer 展示
    sql_answer += (data.content || '')
    _currentChat.value.records[index.value].sql_answer = sql_answer
    break

case 'tool-call':
    // Agent 正在调用工具 — 可用于展示进度
    if (!currentRecord.tool_calls_log) {
        currentRecord.tool_calls_log = []
    }
    currentRecord.tool_calls_log.push({
        tool: data.tool_name,
        args: data.args,
        time: new Date()
    })
    break

case 'tool-result':
    // 工具调用完成 — 可用于更新进度
    if (currentRecord.tool_calls_log?.length > 0) {
        const last = currentRecord.tool_calls_log[currentRecord.tool_calls_log.length - 1]
        last.result = data.summary || (data.success ? 'success' : 'failed')
    }
    break
```

---

## 八、前端适配

### 8.1 ChatRecord 模型扩展

```typescript
// api/chat.ts — ChatRecord 新增字段

export class ChatRecord {
    // ... 现有字段 ...

    // 新增: Agent 工具调用日志（用于展示进度）
    tool_calls_log?: Array<{
        tool: string
        args: any
        result?: string
        time: Date
    }> = []
}
```

### 8.2 可视化 Agent 进度

当前的 BaseAnswer.vue 只展示"思考中..."的 loading 状态。改为展示 Agent 的实际进度：

```
┌────────────────────────────────────────────┐
│ 🔍 正在搜索相关表...                        │  ← tool-call 事件
│ ✅ 找到 10 张候选表                          │  ← tool-result 事件
│ 📋 正在获取 erp_orders 表结构...             │  ← tool-call 事件
│ ✅ 已获取表结构 (12 个字段)                   │  ← tool-result 事件
│ ✍️ 正在生成 SQL 查询...                      │  ← text-delta 事件
│ ✅ SQL 已生成                               │
│ 📊 正在生成图表...                           │  ← chart-result 事件
└────────────────────────────────────────────┘
```

这个改动较大，可在 Phase 2 实现。Phase 1 先确保数据到达前端。

---

## 九、实施计划

### Phase 1: 状态持久化（核心修复，3-4 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| P1.1 | AgentMemory 序列化/反序列化 | [memory.py](backend/apps/chat/agent/memory.py) | `to_dict()` / `from_dict()` |
| P1.2 | Chat 表新增 memory_state 列 | alembic migration | JSON NULL |
| P1.3 | ChatModel 新增 memory_state 字段 | [chat_model.py](backend/apps/chat/models/chat_model.py) | `Column(JSON, nullable=True)` |
| P1.4 | adapter 恢复/保存逻辑 | [adapter.py](backend/apps/chat/agent/adapter.py) | `_load_memory_from_db()` / `_save_memory_to_db()` |
| P1.5 | System Prompt 注入历史上下文 | [graph.py](backend/apps/chat/agent/graph.py) | `_build_system_prompt()` 读取 memory 中的 queries/charts/摘要 |
| P1.6 | 对话摘要生成 | [executor.py](backend/apps/chat/agent/executor.py) | `_finalize_turn()` 追加摘要 |
| P1.7 | SQL 双重执行修复 | [executor.py](backend/apps/chat/agent/executor.py) | `_execute_and_chart` 检查 `query.data` 缓存 |

**验收标准**：同一 chat 内，第二个问题不需要重新 `search_relevant_tables`，能引用第一个问题的查询。

### Phase 2: SSE 协议统一 + 前端进度展示（2-3 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| P2.1 | tool-call / tool-result 事件发出 | [executor.py](backend/apps/chat/agent/executor.py) | 在 agent 循环中 emit |
| P2.2 | 前端 ChartAnswer 处理新事件 | [ChartAnswer.vue](frontend/src/views/chat/answer/ChartAnswer.vue) | switch 新增 case |
| P2.3 | ChatRecord 新增 tool_calls_log | [chat.ts](frontend/src/api/chat.ts) | TypeScript 类型 |
| P2.4 | BaseAnswer 展示 Agent 进度 | [BaseAnswer.vue](frontend/src/views/chat/answer/BaseAnswer.vue) | 替换"思考中..."为具体进度 |

**验收标准**：前端能看到 Agent 的每一步操作（搜索表→获取元数据→生成SQL→执行→图表）。

### Phase 3: LangGraph Checkpointer + 润色（1-2 天）

| # | 任务 | 文件 | 说明 |
|---|------|------|------|
| P3.1 | graph.compile(checkpointer=MemorySaver()) | [graph.py](backend/apps/chat/agent/graph.py) | LangGraph 内部消息持久化 |
| P3.2 | 探索缓存 TTL（7 天过期） | [memory.py](backend/apps/chat/agent/memory.py) | `explored_tables` 带时间戳 |
| P3.3 | 集成测试：3 轮连续对话 | tests/ | 验证追问不重复搜索 |

---

## 十、文件变更清单

### 修改（8 个文件）

```
backend/apps/chat/agent/memory.py          ← +to_dict() / +from_dict()
backend/apps/chat/agent/adapter.py         ← +_load_memory / +_save_memory / +_build_history
backend/apps/chat/agent/graph.py           ← _build_system_prompt 注入历史
backend/apps/chat/agent/executor.py        ← 双重执行修复 + 摘要生成 + tool-call 事件
backend/apps/chat/models/chat_model.py     ← Chat 表 +memory_state 列
backend/apps/chat/api/chat.py              ← adapter 调用传入 chat_id
frontend/src/views/chat/answer/ChartAnswer.vue  ← +text-delta / +tool-call / +tool-result
frontend/src/api/chat.ts                   ← ChatRecord +tool_calls_log
```

### 新增（1 个文件）

```
backend/alembic/versions/XXX_add_chat_memory_state.py  ← migration
```

### 不改的文件

```
agent/tools/*.py           ← 工具不感知持久化
agent/state.py             ← LangGraph state 不变
frontend/src/views/chat/index.vue  ← 主视图不变
assistant.js               ← 嵌入脚本不变
```

---

## 附录 A：对比 Metabase 的状态管理

| 维度 | Metabase | SQLBot 修复后 |
|------|---------|-------------|
| 状态载体 | `MetabotConversation` + `MetabotMessage` (两张 DB 表) | `Chat.memory_state` (一个 JSON 列) |
| 查询引用 | `memory.clj` 中的 `queries` map，键为 card-id | `memory.queries` dict，键为 record_id |
| 图表引用 | `charts` map，绑定到 card | `memory.charts` dict，绑定到 record_id |
| 表缓存 | `explored_tables`（DDL 格式） | `memory.explored_tables`（field dict 格式） |
| 对话历史 | 所有消息存在 `MetabotMessage` 中，每次全量发送给 LLM | 仅注入摘要 + 最近查询/图表，~200 tokens |
| 上下文注入方式 | System Prompt 中的 `<context>` 块 | System Prompt 中的"历史对话摘要"+"已有查询"+"已有图表" |
| 会话边界 | "新对话"按钮清除状态，不可恢复 | 同一 Chat 内状态累积，"新对话"创建新 Chat |

---

## 附录 B：风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| `memory_state` JSON 列过大 | 低 | queries/charts 的 `data` 字段限制前 100 行；定期清理旧 Chat |
| 恢复的状态与当前 DB schema 不一致 | 中 | `version` 字段 + 迁移逻辑；`from_dict()` 容错 |
| MemorySaver 内存膨胀 | 中 | Phase 3 评估 SqliteSaver；按 chat_id 设置 checkpoint TTL |
| 摘要质量影响追问效果 | 中 | 首版用结构化拼接（不调 LLM）；Phase 2 可改为 LLM 摘要 |

---

> **文档版本**: v1.0
> **核心思路**: 将 Agent 状态的生命周期从 request-scoped 改为 session-scoped。通过 `Chat.memory_state` JSON 列持久化 AgentMemory，在每轮对话开始时自动恢复、结束时自动保存。不改变 Agent 工具实现，不改变 API 签名，不改变 SSE 外层格式。
