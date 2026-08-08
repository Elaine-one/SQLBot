# 案例：Agent 工具职责混乱导致内部查询泄露到前端

> **日期**：2026-07-24 | **标签**：工具设计、职责分离、Bug修复

---

## 1. 问题现象

- Agent 在内部探索阶段（查表结构、验证字段值、探索数据分布）时，工具调用记录和中间图表直接展示到了前端
- 用户看到"半成品"图表或者探索步骤，打断了正常的问答体验
- Agent 偶尔会用 `create_sql_query` 做内部数据验证，导致 terminal 提前触发、对话提前终止

## 2. 诊断过程

### 2.1 追溯图表生成链路

```
create_sql_query (terminal)
  → memory.terminal_triggered = True
  → graph.py 路由检测 → return "end"
  → executor.py _post_process()
    → _execute_and_chart() → 强制执行 SQL → _generate_chart()
    → emit("chart") → 前端渲染图表
```

**发现**：只要 agent 调用了 `create_sql_query`，无论出于什么目的（内部验证 or 最终答案），post-process 都会无条件执行+出图。

### 2.2 追溯前端推送链路

```
executor.py _run_agent()
  → 所有 tool-call / tool-result 事件无过滤推送 SSE
  → 前端 ChartAnswer.vue 全部接收并存入 tool_calls_log
  → ExecutionDetails.vue 抽屉展示全部工具调用
```

**发现**：16 个工具的调用记录全部推到前端，没有区分"用户需要看到"和"agent 内部需要"。

### 2.3 搜索所有工具调用点

共 16 个注册工具。逐一排查发现三个维度混在一起：

| 维度 | 含义 | 控制位置 | 混乱表现 |
|------|------|---------|---------|
| `terminal` | 成功后终止循环 | `ToolDef.terminal` + `graph.py` | 所有"用户产出"恰好是 terminal，但非充要条件 |
| (缺失) | 是否展示给前端 | 无 | 全部推送，无过滤 |
| `AgentProfile.terminal_tools` | 名义上的终端列表 | `engine.py` | **从未被任何代码读取，纯死代码** |

### 2.4 系统提示词审查

```
❌ 问题：提示词只说 create_sql_query 是"终端工具"
   没说调用后果是"系统会自动执行 SQL 并生成图表展示给用户"
   Agent 不知道后果 = Agent 无法正确决策
```

## 3. 根因

三层职责混乱：

```
工具层：缺一个"非终端的自定义 SQL 工具"
  → Agent 想用 SQL 内部验证 → 只能调 create_sql_query
  → terminal → chart → 前端中断

控制层：terminal ⊘ show_to_user 混为一谈
  → terminal 只管"循环停不停"
  → 但没有机制管"结果要不要给用户看"
  → post-process 强制执行+出图，无可跳过机制

提示词层：Agent 不知道自己调用的后果
  → 系统提示只说工具功能，不说系统后续行为
```

## 4. 修复方案

### 4.1 新增 `preview_sql` 工具

Agent 内部验证数据的专用 SQL 工具：

```python
preview_sql(sql, max_rows=20)
  terminal=False       # 调完继续探索
  show_to_user=False   # 不推前端
  # 不创建 QueryRecord → post-process 不会处理它
```

### 4.2 `ToolDef` 加 `show_to_user` 字段

```python
@dataclass
class ToolDef:
    terminal: bool = False       # 终止循环？
    show_to_user: bool = False   # 推送前端？
```

分类结果：

| 类型 | 工具数 | 工具 |
|------|-------|------|
| 内部 (`show_to_user=False`) | 11 | `search_relevant_tables`, `get_table_metadata`, `get_table_sample_data`, `get_field_values`, `load_skill`, `execute_sql_query`, `analyze_query_result`, `get_data_summary`, `get_data_preview`, `search_web`, `preview_sql` |
| 用户可见 (`show_to_user=True`) | 6 | `create_sql_query`, `edit_sql_query`, `replace_sql_fragment`, `create_chart`, `edit_chart`, `ask_for_clarification` |

### 4.3 executor.py SSE 过滤

```python
# 改动前：全部推送
self._emit("tool-call", tool_name=tc_name, args=tc_args)

# 改动后：按 show_to_user 过滤
if ToolRegistry.is_user_visible(tc_name):
    self._emit("tool-call", tool_name=tc_name, args=tc_args)
# tool-result 同样过滤
```

### 4.4 修复 chart-only 路径跨轮次泄漏

`memory.charts` 包含所有历史图表。新增运行时集合只追踪当前轮：

```python
_charts_this_turn: set[str]  # 不持久化，每轮清空

# chart-only 路径改为只推送当前轮图表
if self.memory._charts_this_turn:
    latest = self.memory.get_latest_chart_this_turn()
```

### 4.5 删除死代码 `AgentProfile.terminal_tools`

终端列表的唯一真相源是 `ToolDef.terminal`（在 `register_all.py`）。`AgentProfile.terminal_tools` 从未被读取，已删除。

### 4.6 系统提示词添加"后果说明"

```
## ⚠️ 关键规则

- preview_sql vs create_sql_query：前者是内部验证，后者是最终答案。
  如果你想"我先查一下看看数据长什么样"→ 用 preview_sql。
  只有当你确信这就是用户要的最终结果时 → 用 create_sql_query。

- create_sql_query 的后果：一旦调用成功，系统立即执行 SQL、生成图表、展示给用户。
  此时 agent 循环终止，你无法再做更多探索或修正。
```

## 5. 改动文件

| 文件 | 改动类型 | 关键变更 |
|------|---------|---------|
| `apps/chat/agent/tools/registry.py` | 数据模型 | `ToolDef` +`show_to_user`；`ToolRegistry` +`is_user_visible()`, `get_terminal_tool_names()` |
| `apps/chat/agent/tools/register_all.py` | 工具注册 | 17 个工具标注 `show_to_user`；+`preview_sql` 注册 |
| `apps/chat/agent/tools/advanced_tools.py` | 新工具 | `preview_sql` 实现（校验→只读→执行，最多 20 行） |
| `apps/chat/agent/memory.py` | 状态追踪 | +`_charts_this_turn`；+`mark_chart_this_turn()`；+`get_latest_chart_this_turn()` |
| `apps/chat/agent/graph.py` | 提示词 | 工具表拆分为内部/用户可见；+`⚠️ 关键规则`段落 |
| `apps/chat/agent/engine.py` | 清理 | QA profile +`preview_sql`；-`terminal_tools`（死代码） |
| `apps/chat/agent/executor.py` | 执行器 | SSE 过滤 `is_user_visible()`；chart-only 用 `_charts_this_turn`；删除跨记录数据污染 |

## 6. 通用经验

1. **Agent 工具需要两轴分类**：`terminal`（控制循环）和 `show_to_user`（控制前端展示）是正交维度，不要混成一个字段
2. **内部探索和最终产出用不同工具**：防止 Agent 模糊调用时误触产出物
3. **系统提示词要声明"后果"，不只是"功能"**：Agent 不知道代码逻辑，必须显式告诉它调用后系统会做什么
4. **跨轮次状态区分"持久化集合"和"运行时集合"**：前者从 DB 恢复，后者每轮清零
5. **推送前过滤，而非在每个工具内部判断**：集中在 executor 一处过滤，工具实现保持纯粹
6. **死代码要删**：`AgentProfile.terminal_tools` 存在但从未被读——保留它制造了"有两个真相源"的假象

## 7. 相关文档

- [Agent 系统维护手册](../../development/agent-maintenance.md) — 工具注册流程、SSE 事件类型、跨轮次内存
- [工具注册代码](../../../backend/apps/chat/agent/tools/register_all.py)
- [ToolRegistry](../../../backend/apps/chat/agent/tools/registry.py)
- [AgentExecutor](../../../backend/apps/chat/agent/executor.py)
