# SQLBot 旧管道清理 —— 踩坑记录与解决方案

> 日期: 2026-07-24
> 范围: 旧 Pipeline 清理 → Agent 功能补齐 → 前端后端集成修复 → 部署

---

## 1. 双引擎架构清理

### 问题

SQLBot 同时存在两套引擎：旧 Pipeline（`LLMService.run_task`）和新 Agent（`AgentExecutor.run`）。旧代码约 1900 行，涉及 `llm.py`、`chat_model.py`、6 个模板目录。

### 踩坑

**不能简单地 grep 删除。** 旧 Pipeline 的方法和 Agent 共享了 `LLMService` 类。`LLMService.create()` 是 Agent 启动的入口——删除它会让 Agent 完全失效。

### 正确做法

1. 先画引用图：谁调用谁
2. 保留 `LLMService.create()` + `__init__()` + `init_record()`
3. 删除所有 Pipeline 专属方法（`run_task`、`generate_*`、`execute_sql` 等）
4. 模板目录不能全删——`generate_analysis` 和 `generate_chart` 被 Agent 工具直接引用

### 关键教训

```
LLMService 类:
  ✅ 保留: create(), __init__(), init_record(), get_record(), set_record()
  ❌ 删除: run_task_*(), generate_*(), execute_sql(), init_messages(), ...
  
模板目录:
  ✅ 保留: generate_analysis/ (agent: analyze_query_result 工具)
          generate_chart/    (agent: _generate_chart())
  ❌ 删除: 其他 6 个目录
```

---

## 2. 工作区参数断层 — Agent 忽略前端配置

### 问题

前端"参数配置"页面有 5 个设置，但 Agent 对其中 3 个使用了硬编码值：

| 参数 | DB 中的值 | Agent 使用的值 |
|------|----------|-------------|
| `chat.sqlbot_name` | "我的助手" | `"SQLBot"` 硬编码 |
| `chat.limit_rows` | false | `True` 硬编码 |
| `chat.context_record_count` | 10 | `3` 硬编码 |

### 根因

`LLMService.create()` 从 DB 正确读取了参数，但**从未将它们传递给 AgentMemory**。Agent 有自己的默认值。

### 修复

```python
# adapter.py: init_agent_memory()
memory = AgentMemory(
    sqlbot_name = llm_service.chat_question.sqlbot_name,      # 来自 DB
    enable_sql_row_limit = llm_service.enable_sql_row_limit,  # 来自 DB
    context_record_count = llm_service.base_message_round_count_limit, # 来自 DB
    expand_thinking_block = llm_service.expand_thinking_block, # 来自 DB（新增）
)
```

### 关键教训

**数据流断层**是最难发现的 bug。DB → `LLMService.create()` 这段是对的。`LLMService` → `AgentMemory` 这段是断的。每个新参数都需要手动接线。检查方法：在 `init_agent_memory()` 中加一条 `_log.info` 打印所有参数。

---

## 3. 图表工具双重 Bug — 编辑后的图表不渲染

### 问题

用户说"改成扇形图"，Agent 调用了 `edit_chart` 或 `create_chart`，工具返回 `success=True`，但**前端没有渲染图表**。

### 根因（两个独立 Bug）

**Bug 1：`edit_chart` 和 `create_chart` 都没有调用 `mark_chart_this_turn()`**

```python
# _post_process 中的判断条件:
if self.memory.terminal_triggered and self.memory._charts_this_turn:
    # 发射图表 ← 这行永远不会执行！
    # _charts_this_turn 只在 create_chart 中设置，但 create_chart 是非终端工具
    # edit_chart 是终端工具，但不设置 _charts_this_turn
```

**Bug 2：跨轮次数据丢失**

`AgentMemory.to_dict()` 有意不持久化 `query.data`（注释："保持 JSON 列较小"）。
下一轮恢复时 `query.data = None`，导致 `save_sql_exec_data` 被跳过，
前端 `getChatData()` 返回空。

### 修复

```python
# Bug 1 修复：create_chart + edit_chart 都加上
memory.mark_chart_this_turn(chart_ref)

# Bug 1 修复：移除 terminal_triggered 条件（create_chart 是非终端工具）
if self.memory._charts_this_turn:  # 不再需要 && terminal_triggered
    ...

# Bug 2 修复：从 DB 恢复数据
if not query.data:
    SELECT data FROM chat_record
    WHERE chat_id = ? AND data IS NOT NULL
    ORDER BY create_time DESC LIMIT 1
    → query.data = loaded_data
```

### 关键教训

1. **终端工具**（terminal=True）成功时终止循环，**非终端工具**（create_chart）不会。后处理不能只依赖 terminal_triggered。
2. `AgentMemory` 为了性能刻意不持久化大数据——但图表重渲染时必须有数据。解决方案是**从 ChatRecord.data 列回读**。
3. 检查方法：看 `_post_process` 的日志，搜索 "chart-only" 关键字。

---

## 4. System Prompt 引导错误 — edit_chart vs create_chart

### 问题

System prompt 说："修改图表类型 → `edit_chart`"。但对于**跨类别切换**（柱状图→饼图），edit_chart 只改 `type` 字段，不重构轴结构。饼图需要 category 在 `series` 轴上，笛卡尔图在 `x` 轴上。

### 修复

```yaml
# graph.py system prompt — 改为按类别区别对待:
1. 同类切换（柱↔条↔线） → edit_chart({"type": "..."})
2. 切换到饼图             → create_chart（轴结构完全不同）
3. 切换到表格             → create_chart
4. 仅改样式（加标题等）    → edit_chart({"settings": {...}})
```

### 关键教训

**简单编辑只适用于相同结构的图表。** 饼图和笛卡尔图的轴结构不同。如果 LLM 被引导去编辑而不是重建，会导致图表看起来"成功了"（JSON 有效）但实际上前端渲染得很差。

---

## 5. 日志路径 — 相对路径 vs Docker CWD

### 问题

`LOG_DIR = "logs"` 是相对路径。Docker 内 CWD 是 `/opt/sqlbot/app/` → 日志写入 `/opt/sqlbot/app/logs/`。但本地运行时 CWD 可能不同 → 日志写入错误位置或根本找不到。

### 修复

```python
# 改为基于文件位置的绝对路径
log_dir = Path(__file__).resolve().parent.parent.parent / settings.LOG_DIR
# → Docker:  /opt/sqlbot/app/logs  → volume: ./data/sqlbot/logs
# → 本地:    C:\...\backend\logs   → 始终正确
```

### 关键教训

**绝不使用相对路径做日志目录。** Docker 容器内和本地开发的 CWD 不同，且 Docker volume 映射隐藏了真实路径。修复后 `.resolve()` 保证无论从哪里启动都能找到正确的目录。

---

## 6. 死配置：PARSE_REASONING_BLOCK_ENABLED

### 问题

`config.py` 中定义了 `PARSE_REASONING_BLOCK_ENABLED`、`DEFAULT_REASONING_CONTENT_START/END`，但**从未被任何代码读取**。这是旧 Pipeline 解析 DeepSeek `<think>` 标签的遗留配置。

### 根因

现代 LLM 提供者（DeepSeek、Qwen）在 OpenAI 兼容响应中通过 `additional_kwargs["reasoning_content"]` 原生提供推理内容。不需要服务器端 XML 解析。

### 处理

Agent 在 `executor.py:278` 直接读取 `msg.additional_kwargs.get("reasoning_content")` 并作为 `reasoning` SSE 事件发送。旧配置是死代码，可以安全删除。

---

## 7. 前端"伪按钮" — expand_thinking_block

### 问题

前端参数页面有 5 个开关，但 `chat.expand_thinking_block` 是**完全死代码**：
- 后端：Agent 硬编码 `enable_thinking=True`，不读这个参数
- 前端：`chatConfig.getExpandThinkingBlock` 存在但**没有任何组件使用它**

### 修复

```python
# 后端：门控推理 SSE 发射
# executor.py
_emit_reasoning = getattr(self.memory, "expand_thinking_block", True)
if reasoning and reasoning.strip():
    if _emit_reasoning:
        self._emit("reasoning", content=reasoning)
```

```vue
// 前端：BaseAnswer.vue — 使用 store 值做面板默认状态
const expandThinking = chatConfig.getExpandThinkingBlock
const show = ref<boolean>(expandThinking)  // 初始值由配置决定
```

### 关键教训

**前后端都要查。** 一个参数可能出现在前端表单、Pinia store、后端 config、AgentMemory 四个地方。断掉任何一个环节都会导致"切换开关但没效果"。验证方法：改参数 → 问一个问题 → 看 `info.log` 中是否有 `expand_thinking_block=...` 日志。

---

## 8. 术语 / SQL 示例 — 数据流打通

### 问题

前端"术语配置"和"SQL 示例库"的 CRUD 页面和 API 都正常，但 Agent **从未加载这些数据**。旧 Pipeline 的 `filter_terminology_template()` 和 `filter_training_template()` 在 Agent 中没有对应实现。

### 修复

新增 `adapter.py:_load_prompt_context()` 函数，在每次提问时：
1. 调用 `get_terminology_template(session, question, oid, datasource_id)` → ILIKE + pgvector 搜索
2. 调用 `get_training_template(session, question, oid, datasource_id)` → 同上
3. 结果写入 `memory.terminology_text` / `memory.training_examples_text`
4. `graph.py` 注入为 `## 业务术语定义` / `## SQL训练示例` 到 system prompt

### 关键教训

**CRUD 完好 ≠ Agent 在使用。** 旧 Pipeline 的 `filter_*` 方法是独立的调用点，Agent 架构没有自动继承它们。每个旧 Pipeline 的辅助功能都需要在 Agent 中显式重新实现。

---

## 9. 部署：修改后的代码不能直接用官方镜像

### 问题

`docker-compose.yaml` 使用 `image: dataease/sqlbot` 官方镜像。修改代码后必须本地构建，不能用官方镜像。

### 修复

```yaml
# docker-compose.yaml 改为本地构建:
services:
  sqlbot:
    build:
      context: .
      dockerfile: Dockerfile
    image: sqlbot:dev   # 本地构建镜像
```

### 部署包创建

只复制必要文件，排除 `.venv`（371MB）、`node_modules`、`docs`、`tests`、`logs`：

```
SQLBot部署包/  (38 MB / 928 files)
├── docker-compose.yaml
├── .dockerignore
├── Dockerfile / Dockerfile-base / start.sh
├── backend/     (不含 .venv)
├── frontend/    (不含 node_modules)
└── g2-ssr/
```

### 关键教训

**`.dockerignore` 决定构建速度。** 没有它，Docker 会把整个 `.venv` 和 `node_modules` 复制到构建上下文，每次构建浪费数 GB。

---

## 总结：检查清单

| 场景 | 检查方法 |
|------|---------|
| 删除旧代码 | `grep` 所有引用 → 确认无调用方 → 再删除 |
| 前端设置是否生效 | 改设置 → 提问 → 查 `info.log` → 找 `_log.info(f"[Agent] ... setting=...")` |
| 图表不渲染 | 查日志 `chart-only emit` → 如果缺失，检查 `mark_chart_this_turn` |
| 跨轮次数据丢失 | 查日志 `query_data=N data_rows=0` → DB 回读 |
| 日志没变化 | `Path(__file__).resolve()` 验证绝对路径 → 检查 Docker volume 映射 |
| 参数默认值不一致 | 追完整链路：config.py → LLMService → AgentMemory → 实际使用点 |
