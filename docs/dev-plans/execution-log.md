# 执行详情 + Token 统计 + 思考中 统一方案（方案 A）

> 编写日期: 2026-07-16
> 状态: 待实施
> 设计原则: 新增一个 JSON 字段，不走旧表，不污染 chat_log

---

## 一、问题总览

| 问题 | 根因 | 影响 |
|------|------|------|
| `analyze_query_result` LLM 不可用 | 工具签名的 `llm` 参数 Agent 调用时没传 | 分析/预测核心工具废了 |
| `get_data_summary` 偶发崩溃 | 函数内裸异常，`str(exc) == "0"` | 脏数据时分析流程中断 |
| 执行详情永远是空的 | Agent 不写 `chat_log` 表 | `ExecutionDetails.vue` 无数据 |
| Token 消耗无统计 | Agent 循环中没捕获 | 用户看不到模型调用成本 |
| 思考中工具进度不完整 | 分析/预测缺少 `tool_calls_log` 初始化 | 思考块空白，已修复 |

---

## 二、方案设计

**核心思路**：Agent 执行完后，写一段结构化的 `execution_log` JSON 到 ChatRecord 新增字段。思考中、执行详情、Token 消耗从同一个 JSON 源取数据。不碰 `chat_log` 表。

### 2.1 数据流

```
Agent 执行
  │
  ├─ 每轮迭代: 记录 tool_call → tool_result（时间戳、耗时、摘要）
  ├─ 每轮 LLM 调用: 捕获 token_usage（从 response.response_metadata）
  │
  ├─ _post_process 完成
  │
  ├─ 构建 execution_log JSON:
  │   {
  │     "iterations": 6,          ← self.iter_count
  │     "duration_ms": 4500,      ← start_time → end_time
  │     "has_chart": true,        ← 是否生成了图表
  │     "tools": [                ← 从内部计时累积
  │       {"name": "get_data_summary", "elapsed_ms": 120, "summary": "数据摘要: 5 字段, 120 行"},
  │       {"name": "get_data_preview", "elapsed_ms": 50,  "summary": "数据预览: 20/120 行"},
  │       {"name": "analyze_query_result", "elapsed_ms": 2300, "summary": "数据分析完成"},
  │       {"name": "create_chart", "elapsed_ms": 80,  "summary": "图表已创建"}
  │     ],
  │     "token_usage": {          ← 累积多次 LLM 调用
  │       "prompt_tokens": 2500,
  │       "completion_tokens": 800,
  │       "total_tokens": 3300
  │     }
  │   }
  │
  ├─ _finalize_record_and_chat() → 写入 ChatRecord.execution_log
  │
  ├─ 前端:
  │   思考中: BaseAnswer.vue 读 tool_calls_log（实时 SSE，不依赖 execution_log）
  │   执行详情: ExecutionDetails.vue 改读 ChatRecord.execution_log
  │   Token 显示: 从 execution_log.token_usage 取
  │
  └─ 旧表 chat_log: 完全不碰
```

### 2.2 ChatRecord 新增字段

```python
# chat_model.py: ChatRecord — 在 chart_answer 后面加一行

execution_log: Optional[dict] = Field(sa_column=Column(JSON, nullable=True))

# ChatRecordResult 也要加:
execution_log: Optional[dict] = None
```

`JSON` 类型兼容 PostgreSQL JSON/JSONB 和 MySQL JSON。不需要 migraiton 脚本（SQLModel `create_all` 或手动 `ALTER TABLE ADD COLUMN`）。

### 2.3 executor.py 改动

三处改动：

#### 2.3.1 构造函数 — 初始化计时和 Token 累积

```python
def __init__(self, llm, memory, queue, profile=None):
    ...
    self._start_time: float = 0.0           # time.monotonic()
    self._tool_logs: list[dict] = []         # [{name, elapsed_ms, summary}]
    self._token_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
```

#### 2.3.2 _run_agent — 计时 + token 捕获 + tool 计时

```python
async def _run_agent(self) -> None:
    self._start_time = time.monotonic()
    self._tool_logs = []
    self._token_usage = {"prompt_tokens": 0, "completion_tokens": 0}
    ...
    async for event in self.graph.astream(initial_state, config):
        for _node_name, node_output in event.items():
            messages = node_output.get("messages", [])
            for msg in messages:
                if isinstance(msg, AIMessage):
                    ...
                    # ── 捕获 Token 用量 ──
                    if hasattr(msg, "response_metadata"):
                        usage = msg.response_metadata.get("token_usage", {})
                        if usage:
                            self._token_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
                            self._token_usage["completion_tokens"] += usage.get("completion_tokens", 0)
                    ...
                    # ── 工具调用开始计时 ──
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            tc_name = tc.get("name", "")
                            self._emit("tool-call", tool_name=tc_name, args=tc_args)
                            # 记录工具调用开始时间（使用幂等 key 防重复）
                            self._tool_start_time = time.monotonic()

                elif isinstance(msg, ToolMessage):
                    # ── 工具调用结束，记录耗时 + 摘要 ──
                    elapsed = time.monotonic() - getattr(self, '_tool_start_time', time.monotonic())
                    try:
                        result = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                    except json.JSONDecodeError:
                        result = {"raw": str(msg.content)}
                    summary = _tool_result_summary(msg.name, result)
                    self._tool_logs.append({
                        "name": msg.name or "",
                        "elapsed_ms": round(elapsed * 1000),
                        "summary": summary,
                    })
                    self._emit("tool-result", tool_name=msg.name or "", ...)
```

#### 2.3.3 _finalize_record_and_chat — 写入 execution_log

```python
def _finalize_record_and_chat(self) -> None:
    ...
    # ── 写入 execution_log ──
    duration_ms = round((time.monotonic() - self._start_time) * 1000) if self._start_time else 0
    execution_log = {
        "iterations": self.iter_count,
        "duration_ms": duration_ms,
        "tokens": {
            "prompt": self._token_usage.get("prompt_tokens", 0),
            "completion": self._token_usage.get("completion_tokens", 0),
            "total": (self._token_usage.get("prompt_tokens", 0) +
                      self._token_usage.get("completion_tokens", 0)),
        },
        "tools": self._tool_logs,
        "has_chart": len(self.memory.charts) > 0,
    }
    # 写入 DB
    try:
        from sqlalchemy import update
        stmt = update(ChatRecord).where(ChatRecord.id == record.id).values(
            execution_log=execution_log
        )
        session.execute(stmt)
        session.commit()
    except Exception as exc:
        _log.info(f"[Agent] save execution_log failed: {exc}")

    finish_record(session=session, record_id=record.id)
```

### 2.4 前端 ExecutionDetails.vue 改动

```vue
// 改前: 读 chat_log 表
function getLogList(recordId: any) {
    chatApi.get_chart_log_history(recordId).then((res) => {
        logHistory.value = chatApi.toChatLogHistory(res)
    })
}

// 改后: 读 ChatRecord.execution_log
function getLogList(recordId: any) {
    chatApi.get_chart_data(recordId).then((res) => {
        // res.execution_log → 渲染 Agent 执行详情
        logHistory.value = parseExecutionLog(res.execution_log)
    })
}
```

展示内容改造：

| 旧步骤名 | 新展示 |
|---------|--------|
| GENERATE_SQL | **Agent 对话 (N 轮)** |
| GENERATE_CHART | 每一轮工具调用: 名称 + 耗时 + 摘要 |
| ANALYSIS | Token 消耗: prompt / completion / total |

### 2.5 前端 ChatBlock / 思考块改动（已完成）

前端 `BaseAnswer.vue` 已读 `record.tool_calls_log` 展示思考中。分析/预测的 `tool_calls_log` 初始化 + handler 已修好，实时 SSE 填充。

---

## 三、附带修复

### BUG 1: `analyze_query_result` LLM 注入

```python
# dispatch() 中
memory._llm = llm_service.llm

# advanced_tools.py: analyze_query_result
async def analyze_query_result(record_id, memory, llm=None):
    if llm is None:
        llm = getattr(memory, '_llm', None)
    if llm is None:
        return {"success": False, "error": "LLM not available"}
    ...
```

### BUG 2: `get_data_summary` 异常兜底

```python
async def get_data_summary(record_id, memory):
    try:
        ...  # 现有逻辑
    except Exception as exc:
        return {"success": False, "error": f"统计失败: {str(exc)[:200]}"}
```

---

## 四、LLM API 适配 — token_usage 获取路径

不同 LLM 提供商返回 token_usage 的位置不同。需要兼容：

```python
# LangChain AIMessage.response_metadata 中提取
def _extract_token_usage(msg) -> dict:
    meta = getattr(msg, "response_metadata", {}) or {}
    usage = meta.get("token_usage", {}) or meta.get("usage", {}) or {}
    if not usage:
        # DeepSeek / OpenAI 格式: {"completion_tokens": N, "prompt_tokens": M, "total_tokens": T}
        if "completion_tokens" in meta:
            usage = {
                "prompt_tokens": meta.get("prompt_tokens", 0),
                "completion_tokens": meta.get("completion_tokens", 0),
                "total_tokens": meta.get("total_tokens", 0),
            }
    return usage
```

---

## 五、文件变更清单

| 文件 | 操作 | 内容 |
|------|------|------|
| `chat_model.py` | 改 | ChatRecord + `execution_log` 字段 |
| `executor.py` | 改 | 计时 + token 捕获 + `_finalize_record_and_chat` 写入 + memory._llm 注入 |
| `advanced_tools.py` | 改 | `analyze_query_result` 从 memory._llm 取 LLM |
| `advanced_tools.py` | 改 | `get_data_summary` 加 try/except |
| `engine.py` | 改 | dispatch 中注入 `memory._llm` |
| `ExecutionDetails.vue` | 改 | 读 `execution_log` 替代 `chat_log` |
| `BaseContent.vue` 等 | 改/新增 | 新增 Agent 执行详情的渲染组件（或复用工具日志展示） |

---

## 六、和旧代码的关系

| 旧组件 | 处理方式 |
|--------|---------|
| `chat_log` 表 | 不删，旧记录仍从旧表读。新 Agent 产生的记录不写此表 |
| `get_chart_log_history` API | 保留，兼容旧记录 |
| `ExecutionDetails.vue` 旧渲染组件 | 保留 `LogTerm`、`LogSQLSample` 等，旧记录继续用。新记录走 `LogAgent` 新组件 |
| 旧管线代码 | 已标记 DEPRECATED，不影响 Agent 路径 |

---

> **和阶段1/1.5的关系**: 阶段1 完成迁移，阶段1.5 完成优化，本文档是阶段1.5的补充——解决遗留的"执行详情空"和"Token 不可见"问题。
