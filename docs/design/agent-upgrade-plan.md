# SQLBot Agent 架构升级方案

> 编写日期: 2026-07-14
> 基于: SQLBot-main 源码 + Metabase Metabot 架构分析 + Dataease 嵌入场景
> 技术选型: LangGraph（已在依赖中）+ 现有 LangChain LLMFactory

---

## 目录

1. [为什么要改造](#一为什么要改造)
2. [技术选型：LangGraph](#二技术选型langgraph)
3. [目标架构](#三目标架构)
4. [Prompt 设计（借鉴 Metabase）](#四prompt-设计借鉴-metabase)
5. [工具系统设计](#五工具系统设计)
6. [Agent 主循环（LangGraph 实现）](#六agent-主循环langgraph-实现)
7. [嵌入模式适配](#七嵌入模式适配)
8. [实施路线图](#八实施路线图)
9. [风险与缓解](#九风险与缓解)

---

## 一、为什么要改造

### 1.1 当前 SQLBot 的本质局限：每次提问都是"从零开始"

SQLBot 当前采用线性 Pipeline（[llm.py:1176](backend/apps/chat/task/llm.py#L1176)），每轮对话独立执行：

```
用户问题 → Schema全量嵌入 → LLM单次返回JSON → 解析SQL → 执行 → LLM生成图表 → 完成
```

用一个具体的 Dataease 嵌入场景来说明差距：

| 轮次 | 用户问题 | SQLBot 当前行为 | Metabase 做法 |
|------|---------|---------------|-------------|
| 第 1 轮 | "上月销售额趋势" | 生成 SQL → 执行 → 折线图 | 生成 SQL → 获得 query-id → 执行 → 获得 chart-id |
| 第 2 轮 | "用柱状图" | **重新**生成 SQL → **重新**执行 → 柱状图 | `edit_chart(chart-id, type="column")` **不改 SQL** |
| 第 3 轮 | "只看华东区" | **重新**生成 SQL（可能和之前不同）→ 执行 | `edit_sql_query(query-id, "加 WHERE")` → 执行 |

**核心差距**：Metabase 的工具产出的是**带 ID 的可引用对象**。有了 ID，后续工具就可以**编辑**而非**重建**。

### 1.2 三个关键设计差距

| # | 维度 | Metabase | SQLBot 当前 |
|---|------|---------|-----------|
| 1 | 工具产出物 | 带 ID 的结构化对象（query-id, chart-id） | 裸字符串 / 裸 JSON |
| 2 | 状态管理 | 结构化 Memory（queries{}, charts{}），不进入 LLM 上下文 | `self.sql_message` 消息列表，持续追加 |
| 3 | 工具语义 | 创建 / 编辑 / 替换，三种操作粒度 | 只有"生成"（每次全量重建） |

### 1.3 不改什么

| 组件 | 决定 | 理由 |
|------|------|------|
| `LLMFactory` | 保持不动 | LangChain `BaseChatModel` 直接作为 LangGraph 的 LLM |
| 数据源切换（type=0/1） | 保持不动 | "基础应用"/"高级应用"已满足需求 |
| `template.yaml` | 保留，但使用方式改变 | 从"全量嵌入 Prompt"变为"按需注入 System Prompt 规则段" |
| `assistant.js` / postMessage | 保持不动 | 前端嵌入机制不变 |
| 数据库表结构 | 保持不动 | Chat/ChatRecord/ChatLog 不变 |
| SSE 格式 | 保持当前 JSON 格式 | 前端零改动 |

---

## 二、技术选型：LangGraph

### 2.1 为什么用 LangGraph

`langgraph>=0.3` 已在 [pyproject.toml](backend/pyproject.toml#L28) 中。当前 `LLMFactory` 产出的是 LangChain `BaseChatModel`，可以直接作为 LangGraph 的模型节点。

选择 LangGraph 而非手写 Agent 循环，核心原因：

| 能力 | 手写 Agent 循环 | LangGraph |
|------|----------------|-----------|
| 状态管理 | 需要自己写 AgentMemory 的序列化/恢复 | `TypedDict` + `Annotated` 自动管理，`MemorySaver` 提供 checkpoint |
| 工具执行 | 自己解析 tool_calls、执行、拼接 ToolMessage | `ToolNode` 自动处理 |
| 条件路由 | if/else 嵌套 | `add_conditional_edges` 声明式路由 |
| 跨轮次持久化 | 自己写 DB 读写 | `MemorySaver` + `SqliteSaver` 开箱即用 |
| 流式输出 | 自己生成 SSE chunk | `graph.astream()` 原生支持多种 stream mode |
| 代码量 | ~200 行循环逻辑 | ~80 行图定义 |

### 2.2 不用 LangGraph 的哪些部分

| 不用 | 原因 |
|------|------|
| `create_react_agent` 预构建 | 太通用，SQLBot 的循环有明确终止条件（终端工具），需要自定义图 |
| LangGraph Cloud / LangSmith | SQLBot 是自部署服务，不需要 |
| `AgentExecutor` (LangChain) | LangGraph 的 `StateGraph` 已替代 |

### 2.3 LLM 集成方式

```
现有 LLMFactory (不变)
  └─→ BaseChatModel (LangChain)
        └─→ .bind_tools(tool_schemas)  ← LangChain 原生 function calling
              └─→ LangGraph StateGraph 的 agent_node
```

`LLMFactory` 产出的 `BaseChatModel` 实例通过 `.bind_tools()` 挂载工具 Schema 后，直接作为 LangGraph 图中的节点使用。LLM 的流式响应通过 `graph.astream()` 输出。

---

## 三、目标架构

### 3.1 LangGraph 状态图

```
                    ┌──────────────────────────────┐
                    │        AgentState              │
                    │  messages: list[BaseMessage]   │  ← LangGraph 自动管理的消息
                    │  memory:   AgentMemory         │  ← 结构化产出物（queries, charts）
                    │  iteration: int                │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │         agent_node               │
                    │  llm.bind_tools(tools).invoke()  │
                    │  流式输出 → SSE (text parts)      │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │     should_continue (路由)       │
                    │  ├─ last_message.tool_calls?     │
                    │  │   → "tools" (执行工具)         │
                    │  ├─ memory.terminal_triggered?   │
                    │  │   → END (终端工具已触发)       │
                    │  └─ iteration >= max?            │
                    │      → END (达到上限)             │
                    └──────────────┬─────────────────┘
                                   │
                    ┌──────────────▼─────────────────┐
                    │         tools_node               │
                    │  ToolNode(tools).invoke()        │
                    │  执行 → 更新 memory → 返回结果    │
                    │  流式输出 → SSE (tool results)    │
                    └──────────────┬─────────────────┘
                                   │
                                   ▼
                              agent_node (循环)
```

关键点：
- `agent_node` ↔ `tools_node` 之间循环直到终端工具触发或达到上限
- `AgentMemory` 在工具执行时被读写（产生/更新 record_id、chart_ref）
- LangGraph 的 `MemorySaver` 做跨轮次 checkpoint，追问时从 checkpoint 恢复

### 3.2 是 ReAct 还是线性？

**明确回答：有状态的 ReAct Agent。**

但与通用 ReAct 的关键区别——SQLBot 的 Agent 有**明确目标导向和终端条件**：

| 维度 | 通用 ReAct | SQLBot Agent |
|------|----------|-------------|
| 目标 | 开放式问题求解 | 生成 SQL → 执行 → 图表 |
| 循环次数 | 不可预测 | 通常 2-5 轮 |
| 终止条件 | LLM 自行判断 | 终端工具（create_sql_query / edit_sql_query / edit_chart 成功后） |
| 追问 | 不支持 | `MemorySaver` 恢复 checkpoint → `edit_*` 工具 |

不是线性流程——因为 Agent 可以在以下路径间跳转：
- SQL 验证失败 → 回到 agent_node 修正
- 表结构不够 → 回到 tools_node 获取更多
- 追问"用柱状图" → 跳过 SQL 生成，直接 `edit_chart`
- 追问"只看华东区" → `edit_sql_query` 增量修改

### 3.3 一次完整交互的时序

```
═══════════════════════════════════════════════════════════
第 1 轮对话: "上月销售额趋势"
═══════════════════════════════════════════════════════════

  agent_node  → LLM: "搜索相关表"
  tools_node  → search_relevant_tables → ["orders", "products"]

  agent_node  → LLM: "获取表结构"
  tools_node  → get_table_metadata("orders") → {fields: [...]}
  tools_node  → get_table_metadata("products") → {fields: [...]}

  agent_node  → LLM: "生成 SQL"
  tools_node  → create_sql_query(sql) → {success: true, record_id: "r_001"}
                 ↑ terminal_triggered = True → END

  后处理: execute → create_chart → 用户看到图表

═══════════════════════════════════════════════════════════
第 2 轮对话: "用柱状图"（追问，checkpoint 恢复）
═══════════════════════════════════════════════════════════

  checkpoint 恢复: memory.queries["r_001"], memory.charts["chart_001"]

  agent_node  → LLM: "已有 chart_001，改类型即可"
  tools_node  → edit_chart(chart_ref="chart_001", type="column")
                 ↑ terminal_triggered = True → END

  无需重新执行 SQL ← 关键优势

═══════════════════════════════════════════════════════════
第 3 轮对话: "只看华东区"（追问）
═══════════════════════════════════════════════════════════

  checkpoint 恢复: memory.queries["r_001"]

  agent_node  → LLM: "检查 orders 表是否有 region 字段"
  tools_node  → get_table_metadata("orders") → {fields: [..., {region, varchar}]}

  agent_node  → LLM: "基于 r_001 的 SQL 添加 WHERE"
  tools_node  → edit_sql_query(record_id="r_001", modification="追加 region='华东'")
                 ↑ terminal_triggered = True → END

  后处理: execute with new SQL → create_chart → 用户看到华东区数据
```

### 3.4 借鉴 Metabase 源码的关键设计

基于对 Metabase 源码的深入分析（[profiles.clj](src/metabase/metabot/agent/profiles.clj)、[memory.clj](src/metabase/metabot/agent/memory.clj)、[create.clj](src/metabase/metabot/tools/sql/create.clj)、[edit.clj](src/metabase/metabot/tools/sql/edit.clj)、[validation.clj](src/metabase/metabot/tools/sql/validation.clj)、[discovery.selmer](resources/metabot/prompts/shared/prompt_snippets/discovery.selmer)、[sql-querying-only.selmer](resources/metabase/prompts/system/sql-querying-only.selmer)），提取以下可直接落地的设计模式：

#### 模式 1：终端工具仅成功时终止，失败时允许重试

[profiles.clj:158-162](src/metabase/metabot/agent/profiles.clj#L158-L162)：

```clojure
;; :terminal-tools 定义为 tool-name 的集合
;; 成功调用 → 终止 Agent 本轮
;; 失败调用 → 不终止，Agent 可以修正后重试
:terminal-tools #{"create_sql_query" "edit_sql_query" "replace_sql_query"
                   "ask_for_sql_clarification"}
```

映射到 SQLBot：

```python
class ToolDef:
    terminal: bool = False  # True = 成功后终止，失败不终止
```

`create_sql_query` 返回 `{success: false, error: "..."}` → Agent 继续 → LLM 修正 → 重新调用。返回 `{success: true}` → Agent 终止。

#### 模式 2：Memory 的 find 方法返回可用 ID 列表（帮助 LLM 自修正）

[memory.clj:62-74](src/metabase/metabot/agent/memory.clj#L62-L74)：

```clojure
;; find-query 失败时抛出 available-queries，帮助 LLM 知道可以引用什么
(throw (ex-info (str "Query with ID " query-id " not found. "
                     "Available queries: [" (str/join ", " (keys queries)) "]")
                {:agent-error? true
                 :available-queries (keys queries)}))
```

映射到 SQLBot：当 `edit_sql_query(record_id="nonexistent")` 时，错误消息包含可用的 record_id 列表：

```python
if record_id not in memory.queries:
    return {
        "success": False,
        "error": f"查询 {record_id} 不存在。可用查询: {list(memory.queries.keys())}"
    }
```

#### 模式 3：SQL 编辑用字符串替换，而非 LLM 重新生成

[edit.clj:40-78](src/metabase/metabot/tools/sql/edit.clj#L40-L78)：

```clojure
;; edit-sql-query 接收 query-id + edits（old_string → new_string 的替换列表）
;; 逐个应用字符串替换 → 验证新 SQL 有效性 → 返回更新后的查询
(let [new-sql (reduce apply-sql-edit current-sql edits)]
  (validate-sql dialect new-sql)  ;; 验证
  {:action-result {:query-id query-id :query-content new-sql ...}})
```

**关键差异**：我之前的方案设计中 `edit_sql_query` 是让 LLM 基于原 SQL + 修改描述生成新 SQL。Metabase 的做法是让 LLM 直接给出 `old_string → new_string` 的替换，工具内部执行替换并验证。

**为什么 Metabase 的方式更好**：
- 确定性：字符串替换不会改变 SQL 的其他部分
- 可验证：替换后的 SQL 不合法 → 精确报错（哪一步替换出了问题）
- LLM 更擅长：给出精确的 diff 是 LLM 的强项

映射到 SQLBot：

```python
async def edit_sql_query(record_id: str, edits: list[dict], memory: AgentMemory) -> dict:
    """
    edits: [{"old_string": "o.amount", "new_string": "o.amount * 1.1", "replace_all": false}, ...]
    """
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在",
                "available_queries": list(memory.queries.keys())}
    
    sql = query.sql
    for edit in edits:
        old, new = edit["old_string"], edit["new_string"]
        if edit.get("replace_all"):
            sql = sql.replace(old, new)
        else:
            sql = sql.replace(old, new, 1)  # 仅替换第一次出现
    
    # 验证
    valid, error = _validate_sql(sql, memory.datasource_type)
    if not valid:
        return {"success": False, "error": f"编辑后的 SQL 语法有误: {error}"}
    
    query.sql = sql
    query.status = "edited"
    memory.terminal_triggered = True
    return {"success": True, "record_id": record_id}
```

#### 模式 4：发现策略 — search → drill，不绕圈

[discovery.selmer](resources/metabot/prompts/shared/prompt_snippets/discovery.selmer) 核心要点：

1. **search 和 read_resource 两种发现方式**，各有适用场景
2. **"Search first, then drill in"** — search 找到入口 → read_resource 深入确认
3. **"Read a source before you build on it"** — 读表结构后再构建查询，不凭表名猜测
4. **"Don't go in circles"** — 第一次搜索没结果 → 扩大范围；有结果 → 选最好的推进，不要反复搜索

映射到 SQLBot Prompt 设计（详见第四章）。

#### 模式 5：Profile 配置式工具管理

[profiles.clj:94-168](src/metabase/metabot/agent/profiles.clj#L94-L168) 定义了 8 个 Profile，每个 Profile 指定：
- `prompt-template`：哪个 Selmer 模板
- `max-iterations`：最大迭代次数
- `tools`：可用工具集合
- `terminal-tools`：哪些工具的**成功调用**终止循环
- `required-tool-call?`：是否强制每轮都调用工具
- `always-on-skills`：哪些技能的正文直接注入 System Prompt

映射到 SQLBot：SQLBot 的模式较少（独立模式 / 嵌入模式），不需要完整的 Profile 系统。但核心思想——**工具集合 + 终端工具 + 最大迭代**作为可配置项——应该在 `AgentExecutor` 初始化时传入。

---

## 四、Prompt 设计（借鉴 Metabase）

### 4.1 设计原则

对比 [Metabase Prompt 系统](docs/metabase-ai-analysis.md#四prompt-系统) 的设计：

| Metabase 做法 | SQLBot 借鉴 |
|-------------|-----------|
| Selmer 模板渲染：`template.render(variables)` | 复用当前 `template.yaml` 的 `str.format()` |
| System Prompt 轻量（~2000 tokens），**不含 Schema** | System Prompt ~600 tokens，Schema 通过工具获取 |
| `skill_catalog` 在 Prompt 中列出可用技能名 | 工具列表本身即能力清单 |
| `viewing_context` 注入当前浏览内容 | 嵌入模式下注入 DataEase 仪表板上下文 |
| `custom_instructions` 注入管理员自定义指令 | 保留现有 custom_prompts |

### 4.2 System Prompt 模板（完整版）

借鉴 [sql-querying-only.selmer](resources/metabot/prompts/system/sql-querying-only.selmer) 的结构——角色定义 → 发现策略 → 核心原则（Know Your Data First）→ 工具选择指南 → 响应风格：

```yaml
# agent/templates/agent_system.yaml

system: |
  # 角色定义
  你是 SQLBot，一个数据分析助手。你的核心任务是：通过调用工具，帮助用户生成正确的 SQL 查询并可视化结果。

  # 发现数据：search → drill，不绕圈
  
  你有两种发现数据的方式：
  - `search_relevant_tables`：通过语义相似度查找相关表。返回排名候选——命中是线索，空结果不代表数据不存在。
  - `get_table_metadata`：按表名获取完整字段结构。返回精确的列名、类型、注释。
  
  工作流：
  1. 用 `search_relevant_tables` 找到入口（不要猜表名）
  2. 用 `get_table_metadata` 深入确认（不要凭表名就构建查询）
  3. 如果第一次搜索返回空 → 扩大搜索词 → 用你的跨语言知识展开概念。例如中文"会员等级" → 英文: member_level, membership_tier, customer_type, loyalty_status
  4. 如果搜索返回多个候选 → 选最好的推进。不要用稍有不同的措辞反复搜索——这不产生新信息
  
  # 工具选择指南
  
  | 你想做什么 | 用哪个工具 | 备注 |
  |-----------|-----------|------|
  | 探索有哪些表 | `search_relevant_tables` | 语义搜索，返回表名+描述 |
  | 看一张表的结构 | `get_table_metadata` | 返回完整字段列表 |
  | 看字段有哪些值 | `get_field_values` | DISTINCT LIMIT 20，用于精确 WHERE |
  | 看几行样本数据 | `get_table_sample_data` | 帮助理解数据格式 |
  | 创建新查询 | `create_sql_query` | 成功后自动执行并生成图表 |
  | 修改已有查询 | `edit_sql_query` | 通过 old_string→new_string 替换编辑 |
  | 只改图表类型/样式 | `edit_chart` | 不重新执行 SQL |
  
  ## 终端工具
  
  `create_sql_query`、`edit_sql_query`、`edit_chart` 是终端工具：
  - 成功 → 你的生成任务完成，系统自动执行后续步骤
  - 失败 → Agent 继续，你根据错误信息修正后重试
  - 失败不消耗额外的"重试次数"，只有反复相同的错误才算

  # 核心原则
  
  ## 1. Know Your Data First
  
  生成 SQL 之前：
  - 检查表结构、列名、数据类型（通过 `get_table_metadata`）
  - 验证分类字段的实际值格式（通过 `get_field_values`）
  - 理解表关系和主键/外键
  - 采样数据看实际格式（通过 `get_table_sample_data`）
  - 绝不猜测字段名或数据格式——始终验证
  
  示例：一个 "country" 字段可能存 "US" 或 "United States" 或 "USA"——写 WHERE 之前先查 `get_field_values`。

  ## 2. 写正确的 SQL
  
  - 使用 Schema 中的精确列名
  - 只生成 SELECT 查询（SQLBot 是只读的）
  - 多表查询时所有字段加表别名限定
  - 始终添加数据量限制（默认 LIMIT 1000，除非用户指定）
  - 标识符引用规则遵循数据库引擎规范
  
  ## 3. 追问时优先编辑而非重建
  
  - 已有 record_id → 用 `edit_sql_query`（字符串替换），不要 `create_sql_query`
  - 已有 chart_ref → 用 `edit_chart` 修改图表配置
  - 全新话题 → 可以 `create_sql_query`
  
  ## 4. 何时澄清 vs 何时提交
  
  - 结构性的模糊（不同选择导致完全不同的 SQL）→ 向用户澄清。例如："Top customers by revenue or by order count?"
  - 判断性的选择（"是否包含已取消订单"、"上月"指自然月还是30天）→ 选择一个合理默认值，提交 SQL，并在简短说明中标注你做的假设
  - 能通过工具发现的信息（"有没有用户表？"）→ 用工具查，不要问

  # 上下文
  
  数据源类型: {datasource_type}
  当前时间: {current_time}
  {viewing_context}
  {conversation_context}

  # 规则
  
  {sql_rules}           ← 从 template.yaml 提取: 只读、引号、LIMIT、标识符保持
  {chart_rules}         ← 从 template.yaml 提取: 图表类型选择 + 维度/指标规则
```

### 4.3 与现有 template.yaml 的关系

**不删除 `template.yaml`**，但使用方式改变：

| 当前 | 改造后 |
|------|--------|
| SQL 规则、示例、术语全量拼入每条 HumanMessage | 规则注入 System Prompt（~600 tokens） |
| SQL 训练示例嵌入 Prompt | Phase 2 后可迁移到 `load_skill` 工具（按需加载） |
| 术语嵌入 Prompt | 通过 `get_table_metadata` 的 field comment 自然获取 |

### 4.4 上下文注入（嵌入模式特有）

当 DataEase 仪表板加载 SQLBot 时，前端通过 postMessage 发送当前上下文：

```json
{
  "busi": "context",
  "dashboard": {"id": 123, "name": "销售分析"},
  "dataset": {"id": 5, "name": "销售数据集"},
  "filters": {"dateRange": "2026-06-01~2026-06-30"}
}
```

Agent 将此注入 System Prompt 的 `<context>` 块：

```
<context>
数据源类型: MySQL 8.0
当前时间: 2026-07-14 15:30:00
用户正在查看仪表板"销售分析"，数据集"销售数据集"
仪表板时间过滤: 2026-06-01 ~ 2026-06-30
</context>
```

### 4.5 追问时的上下文摘要

当前做法（消息列表堆积）：
```
[System] 规则 (~800 tokens)
[Human] Schema (~4000 tokens)
[AI] SQL (~200 tokens)
[Human] "用柱状图"
→ LLM 上下文已 5000+ tokens
```

改造后（结构化摘要）：
```
[System] 规则 + 上下文 (~800 tokens)
[System] <last-query record_id="r_001">SELECT p.name, SUM(...) FROM orders...</last-query>
        <last-chart chart_ref="chart_001" type="line"/>
[Human] "用柱状图"
→ LLM 上下文 ~1000 tokens
```

---

## 五、工具系统设计

### 5.1 工具清单（对标 Metabase）

参考 [Metabase 工具表](docs/metabase-ai-analysis.md#51-工具架构)（35 个工具，35 个 scopes），按 SQLBot 实际需求裁剪：

#### Phase 1 工具（8 个）——必须

| # | 工具 | 类别 | 对标 Metabase | 复用现有代码 |
|---|------|------|-------------|------------|
| 1 | `search_relevant_tables` | 探索 | `search`（简化版） | `calc_table_embedding()` |
| 2 | `get_table_metadata` | 探索 | `get_table_metadata` | 拆分 `get_table_schema()` → 单表版 |
| 3 | `get_field_values` | 探索 | `get_field_values` | **新增**：`SELECT DISTINCT field LIMIT 20` |
| 4 | `get_table_sample_data` | 探索 | — | 拆分 `get_tables_sample_data()` → 单表版 |
| 5 | `create_sql_query` | 创建 | `create_sql_query` | `generate_sql()` + `check_sql()` |
| 6 | `edit_sql_query` | 编辑 | `edit_sql_query` | **新增**：LLM 基于原 SQL + 修改描述生成新 SQL |
| 7 | `create_chart` | 创建 | `create_chart` | `generate_chart()` |
| 8 | `edit_chart` | 编辑 | `edit_chart` | **新增**：直接更新 chart JSON |

#### Phase 2 工具（4 个）——增强

| # | 工具 | 对标 Metabase | 说明 |
|---|------|-------------|------|
| 9 | `replace_sql_fragment` | `replace_sql_query` | 替换 SQL 片段（如表名/字段名替换），比 edit 更精确 |
| 10 | `ask_for_clarification` | `ask_for_sql_clarification` | 问题模糊时主动要求用户澄清（如"销售额是指含税还是不含税？"） |
| 11 | `analyze_query_result` | — | 分析查询结果，产出文字洞察（类似当前 /analysis 命令） |
| 12 | `load_skill` | `load_skill` | 按需加载 SQL 方言知识（如 PostgreSQL 日期函数、BigQuery 数组操作） |

#### 不纳入的工具（Metabase 有但 SQLBot 不适用）

| Metabase 工具 | 不纳入原因 |
|-------------|----------|
| `create_dashboard` / `create_dashboard_subscription` | DataEase 管理仪表板，SQLBot 不越界 |
| `navigate_user` | SQLBot 是嵌入式组件，无导航功能 |
| `search_content`（全平台搜索） | 超出 SQLBot 数据查询的职责范围 |
| `todo_write` / `todo_read` | 过度设计 |
| `static_viz` | Slack Bot 场景，SQLBot 不需要 |
| `document_*` 系列 | 文档生成，不是当前需求 |

### 5.2 工具粒度讨论：为什么需要 `get_field_values`

当前 SQLBot 把字段的 sample values 间接通过 M-Schema 中的 `examples` 传递（[datasource.py:250-251](backend/apps/datasource/crud/datasource.py#L250-L251)）：

```python
# 当前：Sample Data 中可能包含字段值，但不是结构化的
(country: varchar, 国家, examples:['亚洲','美洲','欧洲','非洲'])
```

但当 LLM 生成 `WHERE region = '华东'` 时，它不知道 `region` 字段的实际值是 `'华东'` 还是 `'华东区'` 还是 `'east_china'`。`get_field_values` 工具解决这个问题：

```
LLM: get_field_values(table="orders", field="region", limit=20)
Tool: {values: ["华东", "华南", "华北", "西南", "西北", "东北"]}
LLM: 现在可以生成 WHERE region = '华东'（精确匹配）
```

这是 Metabase 的 `get_field_values` 工具的直接映射，对 SQL 准确性提升显著。

```python
# agent/tools/query_tools.py

async def get_field_values(table_name: str, field_name: str, memory: AgentMemory,
                          limit: int = 20) -> dict:
    """
    获取字段的去重样本值。借鉴 Metabase get_field_values 工具。
    
    用途：LLM 生成 WHERE 子句前，需要知道字段的实际值格式。
    例如 country 字段存的是 "US" 还是 "United States"——
    猜错的代价是一个错误的查询。此工具消除猜测。
    """
    dialect = memory.datasource_type
    sql = _build_distinct_query(table_name, field_name, limit, dialect)
    
    try:
        result = _exec_raw_sql(sql, memory.datasource_id)
        values = [row[field_name] for row in result["data"] if row[field_name] is not None]
        return {
            "field": f"{table_name}.{field_name}",
            "distinct_count": len(values),
            "sample_values": values[:limit],
            "hint": "使用 sample_values 中的精确值来构建 WHERE 条件",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 5.4 工具对比：SQLBot vs Metabase

| Metabase 工具 (源码) | SQLBot 对应工具 | 借鉴的设计 |
|---------------------|---------------|-----------|
| `create-sql-query` ([create.clj](src/metabase/metabot/tools/sql/create.clj)) | `create_sql_query` | 验证→产生 ID→终端 |
| `edit-sql-query` ([edit.clj](src/metabase/metabot/tools/sql/edit.clj)) | `edit_sql_query` | **字符串替换模式**（old→new）+ 验证 |
| `validate-sql` ([validation.clj](src/metabase/metabot/tools/sql/validation.clj)) | 内嵌于 `create_sql_query` | sqlglot dialect mapping |
| `get_field_values` ([field_stats.clj](src/metabase/metabot/tools/field_stats.clj)) | `get_field_values` | DISTINCT LIMIT，消除 WHERE 猜测 |
| `read-resource-tool` ([resources.clj](src/metabase/metabot/tools/resources.clj)) | `get_table_metadata` | 单表结构按需获取 |
| `search-tool` ([search.clj](src/metabase/metabot/tools/search.clj)) | `search_relevant_tables` | embedding 语义搜索 |
| Profile system ([profiles.clj](src/metabase/metabot/agent/profiles.clj)) | AgentExecutor 配置参数 | terminal-tools + max_iterations |
| Memory ([memory.clj](src/metabase/metabot/agent/memory.clj)) | `AgentMemory` | 结构化 queries/charts + 错误时返回可用 ID |
| Discovery prompt ([discovery.selmer](resources/metabot/prompts/shared/prompt_snippets/discovery.selmer)) | System Prompt 发现策略段 | search→drill + 不绕圈 |
| SQL prompt ([sql-querying-only.selmer](resources/metabot/prompts/system/sql-querying-only.selmer)) | System Prompt 核心原则段 | Know Your Data First + 工具选择指南 |

```python
# agent/tools/sql_tools.py

async def create_sql_query(sql: str, memory: AgentMemory) -> dict:
    """
    创建 SQL 查询（终端工具）。
    成功 → 产生 record_id → terminal_triggered = True。
    失败 → 返回具体错误和修正建议。
    """
    # 1. 语法验证（复用 sqlglot）
    valid, error = _validate_sql(sql, memory.datasource_type)
    if not valid:
        memory.sql_retry_count += 1
        return {
            "success": False,
            "error": error,
            "retries_left": memory.max_sql_retries - memory.sql_retry_count,
            "suggestion": "请根据错误信息修正 SQL 后重新调用 create_sql_query"
        }
    
    # 2. 表名检查（引用的表是否已通过 get_table_metadata 获取）
    unknown = [t for t in _extract_tables(sql) if t not in memory.explored_tables]
    if unknown:
        return {
            "success": False,
            "error": f"表 {unknown} 的字段结构尚未获取",
            "action_required": f"请先对每张表调用 get_table_metadata(table_name=...) 获取字段结构"
        }
    
    # 3. 只读检查
    if not _is_readonly(sql):
        return {"success": False, "error": "仅支持 SELECT 查询"}
    
    # 4. 成功
    record_id = f"r_{_next_seq()}"
    memory.queries[record_id] = QueryRecord(
        record_id=record_id, sql=sql,
        tables_used=_extract_tables(sql), status="created"
    )
    memory.terminal_triggered = True
    return {
        "success": True,
        "record_id": record_id,
        "sql": sql,
        "tables_used": _extract_tables(sql),
    }


async def edit_sql_query(record_id: str, edits: list[dict], memory: AgentMemory) -> dict:
    """
    编辑已有 SQL（终端工具）—— 借鉴 Metabase edit.clj 的字符串替换模式。
    
    edits: [{"old_string": "...", "new_string": "...", "replace_all": false}, ...]
    工具内部逐个应用替换 → 验证新 SQL 有效性 → 更新。
    
    为什么用字符串替换而非 LLM 重新生成：
    - 确定性：替换不会意外改变 SQL 的其他部分
    - 可验证：替换后 SQL 不合法 → 精确报错（哪一步出了问题）
    - LLM 更擅长给出精确 diff 而非完整重写
    """
    if record_id not in memory.queries:
        return {
            "success": False,
            "error": f"查询 {record_id} 不存在",
            "available_queries": list(memory.queries.keys())  # 帮助 LLM 自修正
        }
    
    query = memory.queries[record_id]
    sql = query.sql
    
    for i, edit in enumerate(edits):
        old, new = edit["old_string"], edit["new_string"]
        replace_all = edit.get("replace_all", False)
        
        if old not in sql:
            return {
                "success": False,
                "error": f"第 {i+1} 步替换失败: '{old[:80]}' 在 SQL 中未找到。请检查 old_string 是否精确匹配。"
            }
        
        if not replace_all and sql.count(old) > 1:
            return {
                "success": False,
                "error": f"'{old[:80]}' 出现 {sql.count(old)} 次。设置 replace_all=true 替换全部，或给出更精确的 old_string 以唯一匹配。"
            }
        
        sql = sql.replace(old, new) if replace_all else sql.replace(old, new, 1)
    
    valid, error = _validate_sql(sql, memory.datasource_type)
    if not valid:
        return {"success": False, "error": f"编辑后的 SQL 语法有误: {error}"}
    
    query.sql_history.append({"version": len(query.sql_history) + 1,
                               "previous_sql": query.sql, "modified_at": _now()})
    query.sql = sql
    query.status = "edited"
    memory.terminal_triggered = True  # 终端工具：成功 → 终止
    return {"success": True, "record_id": record_id}
```

---

## 六、Agent 主循环（LangGraph 实现）

### 6.1 状态定义

```python
# agent/state.py
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
    """LangGraph 状态。messages 由 LangGraph 自动管理追加。"""
    messages: Annotated[list[BaseMessage], add_messages]
    memory: AgentMemory     # 结构化产出物，不透传给 LLM
    iteration: int          # 当前迭代次数
```

### 6.2 图构建

```python
# agent/graph.py
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from agent.state import AgentState
from agent.memory import AgentMemory
from agent.tools.registry import ToolRegistry

def build_agent_graph(llm, tools: list, system_prompt: str):
    """
    构建 SQLBot Agent 的 LangGraph 图。
    
    图结构:
      agent_node ←→ tools_node
      agent_node → END (终端工具触发 或 达到上限)
    """
    
    # 绑定工具到 LLM
    llm_with_tools = llm.bind_tools(tools)
    
    def agent_node(state: AgentState):
        """Agent 节点：调用 LLM，流式输出文本"""
        messages = state["messages"]
        # 首次调用时注入 system prompt
        if not any(m.type == "system" for m in messages):
            messages = [SystemMessage(content=system_prompt)] + list(messages)
        
        response = llm_with_tools.invoke(messages)
        state["iteration"] = state.get("iteration", 0) + 1
        
        # 流式输出处理在外部用 astream_events
        return {"messages": [response]}
    
    def should_continue(state: AgentState) -> str:
        """条件路由"""
        memory = state["memory"]
        
        # 终端工具已触发 → 结束循环
        if memory.terminal_triggered:
            return "end"
        
        # 达到最大迭代 → 结束循环
        if state["iteration"] >= memory.max_iterations:
            return "end"
        
        # 最后一条消息包含 tool_calls → 执行工具
        last_msg = state["messages"][-1]
        if hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
            return "tools"
        
        # 无 tool_calls → LLM 在对话而不是执行任务 → 结束
        return "end"
    
    # 构建图
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))
    workflow.set_entry_point("agent")
    workflow.add_conditional_edges("agent", should_continue, {
        "tools": "tools",
        "end": END,
    })
    workflow.add_edge("tools", "agent")
    
    return workflow.compile(checkpointer=MemorySaver())
```

### 6.3 流式执行

```python
# agent/executor.py
from agent.graph import build_agent_graph

class AgentExecutor:
    """Agent 执行器，封装 LangGraph 图 + SSE 输出"""
    
    def __init__(self, llm, memory: AgentMemory, system_prompt: str):
        tools = ToolRegistry.get_tools_for_llm()  # OpenAI function calling 格式
        self.graph = build_agent_graph(llm, tools, system_prompt)
        self.memory = memory
    
    async def run(self, user_message: str, thread_id: str):
        """
        执行 Agent 循环，yield SSE 事件。
        
        Args:
            user_message: 用户问题
            thread_id: chat_id 字符串，用于 LangGraph checkpoint 索引
        """
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = {
            "messages": [HumanMessage(content=user_message)],
            "memory": self.memory,
            "iteration": 0,
        }
        
        # 使用 astream_events 获取流式事件
        async for event in self.graph.astream_events(initial_state, config):
            kind = event["event"]
            
            if kind == "on_chat_model_stream":
                # LLM 文本流式输出
                chunk = event["data"]["chunk"]
                if chunk.content:
                    yield sse_text_delta(chunk.content)
            
            elif kind == "on_tool_start":
                # 工具开始执行
                yield sse_tool_start(event["name"], event["data"].get("input"))
            
            elif kind == "on_tool_end":
                # 工具执行完毕
                yield sse_tool_result(event["name"], event["data"].get("output"))
        
        # 后处理：执行 SQL + 生成图表
        latest = self.memory.get_latest_query()
        if latest and latest.status in ("created", "edited"):
            yield from self._execute_and_chart(latest)
        
        yield sse_finish()
```

### 6.4 文件结构

```
backend/apps/chat/
├── agent/                              # 新增
│   ├── __init__.py
│   ├── state.py                        # AgentState (LangGraph TypedDict)
│   ├── memory.py                       # AgentMemory + QueryRecord + ChartRecord
│   ├── graph.py                        # build_agent_graph() — LangGraph 图定义
│   ├── executor.py                     # AgentExecutor — SSE 流式适配
│   ├── prompt.py                       # System Prompt 构建（从 template.yaml 加载）
│   └── tools/
│       ├── __init__.py
│       ├── registry.py                 # ToolDef + ToolRegistry + OpenAI schema 转换
│       ├── schema_tools.py             # search_relevant_tables, get_table_metadata
│       ├── sql_tools.py                # create_sql_query, edit_sql_query
│       ├── chart_tools.py              # create_chart, edit_chart
│       └── query_tools.py              # get_field_values, get_table_sample_data
│   └── templates/
│       └── agent_system.yaml           # System Prompt 模板（新增）
```

**10 个新文件**，**3 个修改文件**：

```
修改:
  backend/apps/chat/api/chat.py              ← ?engine=agent 开关 + thread_id
  backend/apps/chat/task/llm.py              ← Agent 模式委托 AgentExecutor
  backend/apps/datasource/crud/datasource.py ← 拆分 get_table_schema → 单表版本
```

---

## 七、嵌入模式适配

### 7.1 嵌入模式的特殊性

Dataease 嵌入模式（高级应用 type=1）的关键差异：

1. **数据源来自 DataEase API** — `AssistantOutDs.get_ds_from_api()` 动态获取
2. **表范围由数据集确定** — 用户在前端选择数据集后传入 SQLBot
3. **字段带有业务名称** — `(列名:类型, 业务名称:别名)` 格式

这些不影响 Agent 架构。`get_table_metadata` 的底层实现根据 `type` 决定是查 SQLBot DB 还是 DataEase API。

### 7.2 上下文注入

Dataease 前端通过 postMessage 发送仪表板上下文（详见 4.4 节）。Agent 将此注入 System Prompt。

### 7.3 嵌入模式下的追问效率

最大收益场景——嵌入模式下的追问：

```
第 1 轮: "上月各产品线销售额" → 3 次迭代 → execute → chart
第 2 轮: "加上利润率" → edit_sql_query → execute（仅 SQL 执行耗时）
第 3 轮: "导出数据" → 数据已在 memory.queries["r_001"].data 中（零耗时）
```

---

## 八、实施路线图

### Phase 1: LangGraph Agent + 创建类工具（Week 1-3）

| 任务 | 内容 |
|------|------|
| P1.1 | `AgentMemory` + `QueryRecord` + `ChartRecord` |
| P1.2 | `ToolDef` + `ToolRegistry` + OpenAI schema 转换 |
| P1.3 | `search_relevant_tables`（复用 `calc_table_embedding`） |
| P1.4 | `get_table_metadata`（拆分 `get_table_schema` → 单表版） |
| P1.5 | `create_sql_query`（验证 + 重试 + 产生 record_id） |
| P1.6 | `create_chart`（复用 `generate_chart`） |
| P1.7 | System Prompt 模板（从 `template.yaml` 提取规则） |
| P1.8 | `build_agent_graph()` — LangGraph 图定义 |
| P1.9 | `AgentExecutor` — SSE 流式适配 |
| P1.10 | `?engine=agent` 开关 |

**验收**：独立模式 Agent 可完成"查询上月销售额最高的10个产品"。SQL 失败自动修正。

### Phase 2: 编辑类工具 + 追问（Week 4-5）

| 任务 | 内容 |
|------|------|
| P2.1 | `edit_chart`（接收 chart_ref + 修改内容） |
| P2.2 | `edit_sql_query`（LLM 基于原 SQL + 修改描述生成新 SQL） |
| P2.3 | `get_field_values`（SELECT DISTINCT ... LIMIT 20） |
| P2.4 | `get_table_sample_data`（单表 SELECT * LIMIT 3） |
| P2.5 | LangGraph checkpoint 跨轮次恢复（同一 chat_id） |
| P2.6 | 追问意图注入（`is_followup` + 上下文摘要） |

**验收**：追问"用柱状图"→ `edit_chart`，"只看华东区"→ `edit_sql_query`。

### Phase 3: 嵌入模式 + 打磨（Week 6-7）

| 任务 | 内容 |
|------|------|
| P3.1 | Assistant 模式接入 Agent（type=1 用 DataEase API） |
| P3.2 | 行权限 + 动态子查询适配 |
| P3.3 | DataEase 仪表板上下文注入 |
| P3.4 | `engine=agent` 设为默认 |
| P3.5 | 嵌入模式联调 |

### Phase 4: 增强工具（Week 8+）

| 任务 | 内容 |
|------|------|
| `replace_sql_fragment` | 精确片段替换 |
| `ask_for_clarification` | 模糊问题主动澄清 |
| `analyze_query_result` | 数据洞察 |
| `load_skill` | SQL 方言按需加载 |

---

## 九、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| LangGraph `MemorySaver` 在内存中膨胀 | 中 | Phase 2 切换 `SqliteSaver`；checkpoint 按 chat_id 过期清理 |
| LLM 不调用工具而是直接编造 SQL | 中 | System Prompt 明确"必须通过工具"；`create_sql_query` 检查表名是否已获取 |
| `edit_sql_query` 修改后 SQL 与原 SQL 逻辑不一致 | 中 | 保留 `sql_history` 支持回退；修改前后做 diff 校验 |
| Agent 模式延迟高于 Pipeline（多轮 LLM 调用） | 中 | 多数查询 2-3 轮完成；Phase 3 做 A/B 延迟对比 |
| 追问意图识别错误（该 create 却走了 edit） | 低 | System Prompt 中注入已有产出物摘要；LLM 自行判断 |
| `get_field_values` 在大表上性能差 | 低 | LIMIT 20 + DISTINCT；仅对枚举型字段（varchar/char）调用 |

---

## 附录：文件变更清单

### 新增（10 个文件）

```
backend/apps/chat/agent/__init__.py
backend/apps/chat/agent/state.py
backend/apps/chat/agent/memory.py
backend/apps/chat/agent/graph.py
backend/apps/chat/agent/executor.py
backend/apps/chat/agent/prompt.py
backend/apps/chat/agent/tools/__init__.py
backend/apps/chat/agent/tools/registry.py
backend/apps/chat/agent/tools/schema_tools.py
backend/apps/chat/agent/tools/sql_tools.py
backend/apps/chat/agent/tools/chart_tools.py
backend/apps/chat/agent/tools/query_tools.py
backend/apps/chat/agent/templates/agent_system.yaml
```

### 修改（3 个文件）

```
backend/apps/chat/api/chat.py              ← ?engine=agent + thread_id
backend/apps/chat/task/llm.py              ← 委托 AgentExecutor
backend/apps/datasource/crud/datasource.py ← 单表版 get_table_schema
```

---

> **文档版本**: v5.0
> **核心变更**:
> (1) **LangGraph**（已在 pyproject.toml 依赖中）作为 Agent 框架，`StateGraph` + `MemorySaver` 替代手写循环；
> (2) 工具从 7 个扩展为 Phase 1 8 个 + Phase 2 4 个，**`edit_sql_query` 改用字符串替换模式**（借鉴 [edit.clj](src/metabase/metabot/tools/sql/edit.clj)）；
> (3) **新增 3.4 节**：从 Metabase 源码中提取 5 个可直接落地的设计模式（终端工具语义、Memory 自修正、字符串替换编辑、发现策略、Profile 配置）；
> (4) **重写 Prompt 设计**（第四章）：借鉴 [sql-querying-only.selmer](resources/metabot/prompts/system/sql-querying-only.selmer) 的完整结构——Know Your Data First + 工具选择指南 + 澄清策略 + 概念驱动的跨语言发现。

## 附录 B：Metabase 源文件参考

| 文件 | 借鉴内容 |
|------|---------|
| [profiles.clj](src/metabase/metabot/agent/profiles.clj) | Profile 配置模式、terminal-tools、required-tool-call? |
| [memory.clj](src/metabase/metabot/agent/memory.clj) | 结构化状态管理、find-query 错误返回可用 ID |
| [create.clj](src/metabase/metabot/tools/sql/create.clj) | SQL 创建工具：验证→产生 ID |
| [edit.clj](src/metabase/metabot/tools/sql/edit.clj) | **字符串替换编辑**模式 |
| [validation.clj](src/metabase/metabot/tools/sql/validation.clj) | sqlglot dialect mapping + transpile 验证 |
| [discovery.selmer](resources/metabot/prompts/shared/prompt_snippets/discovery.selmer) | 发现策略：search→drill + 不绕圈 |
| [sql-querying-only.selmer](resources/metabot/prompts/system/sql-querying-only.selmer) | Know Your Data First + 工具选择指南 + 澄清策略 |
| [skills.selmer](resources/metabot/prompts/shared/skills.selmer) | Skills 目录格式 |
| [sql-generation-cross-language-discovery.md](docs/开发文档/sql-generation-cross-language-discovery.md) | 概念驱动的跨语言发现策略 |
