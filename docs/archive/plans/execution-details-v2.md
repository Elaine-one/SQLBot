# 执行详情优化方案 B：深执行详情，浅思考中

> 编写日期: 2026-07-16
> 状态: 待实施

---

## 一、对比

| | 思考中（现状不变） | 执行详情（改造后） |
|---|---|---|
| 内容 | 工具名 + 一行摘要 | 工具名 + 耗时 + 展开看完整入参/出参 |
| 数据源 | `tool_calls_log`（SSE 实时） | `execution_log.tools`（含完整 args + result） |
| 存储 | 前端内存（不持久化） | DB `execution_log` JSON 列（持久化） |
| 时机 | 执行中流式可见 | 执行完成后点开 Drawer |
| 交互 | 折叠/展开 | 每条可展开查看 JSON |

---

## 二、数据流

```
executor._run_agent
  ├─ tool_call: 记录 tool_name + args → 发射 tool-call SSE
  ├─ tool_result: 记录 result + 耗时 → 发射 tool-result SSE
  │
  ├─ 累积到 self._tool_logs:
  │   [
  │     {name, args: {...}, result: {...}, elapsed_ms, summary},
  │     ...
  │   ]
  │
  ├─ _build_execution_log():
  │   {
  │     iterations, duration_ms,
  │     tokens: {prompt, completion, total},
  │     tools: [上述含 args/result 的完整列表]
  │   }
  │
  ├─ _save_execution_log() → DB
  ├─ emit execution-stats SSE → 前端实时拿到
  │
  └─ 前端 ExecutionDetails 读取 execution_log.tools:
      列表: 工具名 + 耗时 + 成功/失败状态
      展开: <pre>args JSON</pre> + <pre>result JSON</pre>
```

---

## 三、文件变更

### 3.1 `executor.py` — tool log 加完整 args/result

```python
# _run_agent 中 ToolMessage 处理处

self._tool_logs.append({
    "name": msg.name or "",
    "args": tc_args,          # ← 新增：工具调用入参
    "result": result,          # ← 新增：工具返回的完整 dict
    "elapsed_ms": elapsed_ms,
    "summary": summary,
})
```

`tc_args` 来自 AIMessage 的 tool_calls，已在 `self._emit("tool-call", ...)` 时获取。

### 3.2 `ExecutionDetails.vue` — 改渲染逻辑

新增组件 `LogAgentTool.vue`，替代现有的 `LogWithAi`：

```
每条工具日志:
  ┌─────────────────────────────────────────┐
  │ ▶ get_data_summary    120ms  ✓ 成功     │  ← 点击展开
  ├─────────────────────────────────────────┤
  │ 入参:                                    │
  │ {"record_id": "r_base_240"}              │
  │                                          │
  │ 返回:                                    │
  │ {"success": true, "field_count": 6, ...} │
  └─────────────────────────────────────────┘
```

### 3.3 `ChatLogHistoryItem` 类型扩展（`chat.ts`）

```typescript
// ChatLogHistoryItem 新增字段
item?: any           // 原始工具日志对象，供 AgentToolLog 使用
```

---

## 四、和现有组件的关系

| 组件 | 处理方式 |
|------|---------|
| `LogTerm` / `LogSQLSample` / `LogCustomPrompt` 等 | 保留，旧管线记录继续用 |
| `LogWithAi` | 保留，旧管线记录 fallback |
| `LogAgentTool` | **新增**，Agent 记录专用 |
| `ExecutionDetails.vue` | 改 `getLogList` 中 `operate_key` 为 `AGENT_TOOL`，新增 `LogAgentTool` 分支 |
| `BaseAnswer.vue` | 不动，思考中继续读 `tool_calls_log` |

模板渲染逻辑：

```html
<!-- ExecutionDetails.vue -->
<LogTerm v-if="ele.operate_key === 'FILTER_TERMS'" :item="ele" />
...
<LogAgentTool v-else-if="ele.operate_key === 'AGENT_TOOL'" :item="ele" />
<LogWithAi v-else :item="ele" />
```

---

## 五、Token 到 DB 的链路确认

当前已经完整：

```
_capture_token_usage(msg) → _token_usage 累积
  → _build_execution_log() → tokens: {prompt, completion, total}
    → _save_execution_log() → UPDATE chat_record SET execution_log = ...
      → API SELECT ChatRecord.execution_log
        → 前端 toChatRecord → record.execution_log
          → index.vue :total-tokens="execution_log.tokens.total"
            → ChatTokenTime 展示
```

---

## 六、不做的

- 不碰思考中（BaseAnswer / tool_calls_log），保持现状
- 不删旧管线组件（LogTerm 等），兼容旧记录
- 不加 per-iteration LLM 详情（prompt/response 原文），避免执行日志过大
