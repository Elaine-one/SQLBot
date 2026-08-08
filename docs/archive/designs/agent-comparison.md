# SQLBot Agent 升级：改造前后差异对比

> 编写日期: 2026-07-14
> 基于: SQLBot-main 实际代码路径分析（llm.py / assistant.py / datasource.py / chat.py）
> 状态: 仅对比，不做推断。所有"改造前"行为均来自现有代码，"改造后"行为均来自新增 agent/ 代码。

---

## 一、SQLBot 独立模式（直接输入，选择数据源）

**入口**: `POST /api/v1/chat/question`（`origin=0`）
**数据源来源**: SQLBot 自身的 `core_datasource` 表（[llm.py:136-140](backend/apps/chat/task/llm.py#L136-L140)）
**改造后入口**: 同一端点，添加 `?engine=agent` 参数（[chat.py:374](backend/apps/chat/api/chat.py#L374)）

| 环节 | 改造前（Pipeline） | 改造后（Agent） |
|------|-------------------|----------------|
| **整体架构** | 线性 Pipeline，约 300 行 `run_task()` 串行执行 | ReAct Agent Loop，LangGraph `StateGraph` 驱动 |
| **初始 Prompt 大小** | ~5000-10000 tokens（System + Rules + 全部 10 张表的 Schema + Sample Data + 术语 + 示例 + 历史） | ~800 tokens（System + Rules），Schema 不在 Prompt 中 |
| **表发现** | `calc_table_embedding()` → embedding 排序 → top N，**所有表的完整字段**一次嵌入 Prompt | LLM 调用 `search_relevant_tables` 工具 → 仅返回表名+描述 → LLM 选择感兴趣的表 → 调用 `get_table_metadata` 按需获取字段 |
| **SQL 生成** | LLM 单次 stream → 期望返回 JSON `{success, sql, tables, chart-type, brief}` | LLM 调用 `create_sql_query(sql)` 工具 → 工具内验证语法+只读+表名 → 返回 `record_id` |
| **SQL 验证失败** | 直接抛 error SSE → 用户手动 `/regenerate` | 工具返回 `{success: false, error: "..."}` → LLM 修正 → 重新调用（最多 3 次） |
| **Schema 缓存** | 无。每轮独立，即使连续追问也重新构建全部 Schema | `memory.explored_tables` 跨轮次缓存。追问时已缓存的表直接返回 `{cached: true}` |
| **行权限过滤** | `generate_filter()` → LLM 注入 WHERE 条件（[llm.py:884](backend/apps/chat/task/llm.py#L884)） | `apply_permissions()` 在 Agent 后处理中执行（[executor.py](backend/apps/chat/agent/executor.py)） |
| **图表生成** | LLM 单次 stream → 图表 JSON | `create_chart(record_id, chart_config)` 工具 |
| **追问"用柱状图"** | 重新执行完整 Pipeline（包括重新生成 SQL、重新执行） | `edit_chart(chart_ref, {type: "column"})` → **不重新生成 SQL，不重新执行** |
| **追问"只看华东区"** | 重新执行完整 Pipeline | `edit_sql_query(record_id, edits)` → 字符串替换 → 仅重新执行 SQL |
| **SQL 编辑方式** | 不支持。每次全量重新生成 | `edit_sql_query`：字符串替换（old_string→new_string），验证通过则更新。对标 Metabase [edit.clj](src/metabase/metabot/tools/sql/edit.clj) |
| **字段值查询** | 不可用。WHERE 条件中的值靠 LLM 猜测 | `get_field_values(table, field)` → `SELECT DISTINCT ... LIMIT 20` → 精确值 |
| **方言知识** | 全部嵌入 Prompt（`template.yaml` + `sql_examples/*.yaml`） | `load_skill("sql-postgresql")` → 按需加载，不占初始 Prompt |
| **数据洞察** | 需手动 `/analysis` 命令 | `analyze_query_result(record_id)` 工具 |
| **模糊问题** | 直接猜 → 可能猜错 | `ask_for_clarification("销售额含税还是不含税？")` → 用户澄清后再生成 |
| **SSE 格式** | `data:{"type":"...","content":"..."}` | **相同格式** — 前端零改动 |

---

## 二、基础应用（type=0）嵌入 DataEase

**入口**: `POST /api/v1/chat/assistant/start`（`origin=2`）
**数据源来源**: SQLBot 的 `core_datasource` 表，按工作空间过滤 + 公开/私有可见性（[assistant.py:39-51](backend/apps/system/crud/assistant.py#L39-L51)）
**前端创建方式**: 助理管理页 → 点击"创建应用" → 选择 **基础应用** → 配工作空间 + 选公开/私有数据源

| 环节 | 改造前 | 改造后 |
|------|--------|--------|
| **数据源获取** | `get_assistant_ds()` → `CoreDatasource`（SQLBot 库） | 同左。`memory.out_ds_instance = None` → 走 CoreDatasource 路径 |
| **表发现** | DsCard 选择公开/私有数据源 → 全部表字段一次嵌入 Prompt | 同独立模式：`search_relevant_tables` + `get_table_metadata` 按需获取 |
| **Schema 嵌入** | 全部入选表的完整字段 | 按需。Agent 迭代中逐表获取并缓存 |
| **Sample Data** | `get_tables_sample_data()` 对所有表执行 `SELECT * LIMIT 3`（[llm.py:397-402](backend/apps/chat/task/llm.py#L397-L402)） | `get_table_sample_data` 工具按需获取（指定单表） |
| **行权限** | `generate_filter()`（[llm.py:1281](backend/apps/chat/task/llm.py#L1281)） | `_apply_core_permissions()` 在 Agent SQL 生成后执行 |
| **追问处理** | 不支持。每次都是新对话 | 支持。`is_followup=True` → 注入已有 query/chart 摘要 → `edit_*` 工具优先 |
| **跨轮次状态** | 无。每轮独立构建 Prompt | 共享 `MemorySaver` + `thread_id=chat_id` → LangGraph 自动恢复 |

---

## 三、高级应用（type=1）嵌入 DataEase

**入口**: `POST /api/v1/chat/assistant/start`（`origin=2`）
**数据源来源**: DataEase `/sqlbot/dataset` API（[assistant.py:123-157](backend/apps/system/crud/assistant.py#L123-L157)），通过配置的 endpoint + 接口凭证动态获取
**前端创建方式**: 助理管理页 → 点击"创建应用" → 选择 **高级应用** → 配接口 URL + 超时 + AES 加密 + 接口凭证

| 环节 | 改造前 | 改造后 |
|------|--------|--------|
| **数据源获取** | `AssistantOutDsFactory.get_instance()` → `get_ds_from_api()` → HTTP GET DataEase `/sqlbot/datasource` | 同左。`LLMService.create()` 不变，`memory.out_ds_instance != None` |
| **ds 对象类型** | `AssistantOutDsSchema`（`host/port/user/password/dataBase/tables[]`） | 同左 |
| **表发现** | `get_db_schema()` 遍历 `ds.tables`，**全部表全部字段**拼入 M-Schema 字符串（[assistant.py:183-229](backend/apps/system/crud/assistant.py#L183-L229)） | `search_relevant_tables` → 返回**全部表名+描述**（不排序、不截断）。数据集通常 1-10 张表，LLM 用自身跨语言语义理解挑选候选表 → `get_table_metadata` 按需获取字段，验证列名匹配。**对标 Metabase 概念驱动发现**（LLM 做语义匹配，不用 embedding/关键词） |
| **字段名处理** | `(field_name:type, 业务名称:field_comment)` 格式嵌入 Prompt。LLM 需记住"业务名称不能用于 SQL" | `_build_field_dict_assistant()` → 返回 `{name, type, comment, business_name}`。`business_name` 显式标注，LLM 清楚区分 |
| **Embedding** | **注释掉了**（[assistant.py:222-223](backend/apps/system/crud/assistant.py#L222-L223)）。所有表直接全部返回 | 同样不使用 embedding。表已预设，无需排序 |
| **Sample Data** | **不获取**（[llm.py:397-398](backend/apps/chat/task/llm.py#L397-L398)：`if not self.out_ds_instance` 条件跳过） | `get_table_sample_data` → `exec_sql(ds, "SELECT * FROM table LIMIT 3")` 按需获取。`exec_sql()` 原生支持 `AssistantOutDsSchema`（[db.py:585](backend/apps/db/db.py#L585)） |
| **动态子查询** | `generate_assistant_dynamic_sql()` → LLM 替换表名为子查询（Union/Join 数据集场景）（[llm.py:820](backend/apps/chat/task/llm.py#L820)） | `_apply_assistant_permissions()` 后处理中执行（[permission_tools.py](backend/apps/chat/agent/tools/permission_tools.py)） |
| **行权限规则** | `generate_assistant_filter()` → LLM 注入 DataEase 行规则 WHERE（[llm.py:891](backend/apps/chat/task/llm.py#L891)） | 同上，后处理统一执行 |
| **追问处理** | 不支持 | 支持。与独立模式相同 |
| **上下文注入** | 无 | 支持。待前端发送 `busi=context` postMessage → Agent System Prompt 注入仪表板/数据集上下文 |

---

## 四、改造前后关键指标对比

| 指标 | 独立模式（前） | 独立模式（后） | 基础应用（前） | 基础应用（后） | 高级应用（前） | 高级应用（后） |
|------|-------------|-------------|-------------|-------------|-------------|-------------|
| 初始 Prompt | ~5000-10000 tokens | ~800 tokens | ~5000-10000 tokens | ~800 tokens | ~1500-4000 tokens（取决于数据集表数） | ~800 tokens |
| SQL 失败处理 | 用户手动 /regenerate | 自动修正，最多 3 次 | 同左 | 同左 | 同左 | 同左 |
| 追问（改图表） | 重新生成 SQL + 执行 | 仅更新图表配置 | 不支持（每次新对话） | edit_chart | 不支持 | edit_chart |
| 追问（改 SQL） | 重新生成 SQL + 执行 | 字符串替换 + 仅执行 | 不支持 | edit_sql_query | 不支持 | edit_sql_query |
| Schema 获取 | 全部表嵌入 | 按需单表获取 | 全部表嵌入 | 按需单表获取 | 全部表嵌入 | 按需单表获取 |
| Sample Data | 全部表一次获取 | 按需单表获取 | 全部表一次获取 | 按需单表获取 | 不获取 | 按需按需获取 |
| Embedding | ✅ 排序 top N | ✅ 工具内排序 | ✅ 排序 top N | ✅ 工具内排序 | ❌ 注释掉 | ❌ 不需要（表预设） |
| 字段值精确查询 | ❌ | ✅ get_field_values | ❌ | ✅ get_field_values | ❌ | ✅ get_field_values |
| 方言按需加载 | ❌ 全量嵌入 | ✅ load_skill | ❌ | ✅ load_skill | ❌ | ✅ load_skill |
| 数据洞察 | /analysis 命令 | analyze_query_result | /analysis 命令 | analyze_query_result | /analysis 命令 | analyze_query_result |
| 模糊问题澄清 | ❌ 直接猜 | ✅ ask_for_clarification | ❌ | ✅ | ❌ | ✅ |
| 跨轮次状态 | 无 | MemorySaver checkpoint | 无 | MemorySaver checkpoint | 无 | MemorySaver checkpoint |
| 行权限 | generate_filter (LLM) | 后处理 apply_permissions | generate_filter | 后处理 | generate_assistant_filter | 后处理 |
| 动态子查询 | - | - | - | - | generate_assistant_dynamic_sql | 后处理 |
| 前端改动 | - | 零改动 | - | 零改动 | - | 零改动 |

---

> **结论**: 三种模式统一使用同一套 Agent 引擎（LangGraph + 13 工具）。差异仅在数据源获取方式——独立模式和基础应用走 CoreDatasource，高级应用走 AssistantOutDsSchema——由 `memory.out_ds_instance` 自动判别，Agent 主循环不变。
