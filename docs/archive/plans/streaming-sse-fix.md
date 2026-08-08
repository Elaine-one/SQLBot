# 前后端流式协作问题排查修复计划

## 一、问题总结（用户描述）

1. **QA 及两个子智能体（数据分析/数据预测）不显示思考过程流式**
2. **子智能体触发后显示"思考中"，但无输出；正文完毕后思考内容与正文完全一致**
3. **刷新页面后，思考中内容又发生变化**

***

## 二、Repo 调研结论（根因分析）

经过对后端 Agent 流式管线、前端 SSE 解析、ChatRecord 存储模型的全面查验，已识别出 **5 类根因**，它们相互叠加导致了上述现象。

### 根因 1：AnalysisAnswer / PredictAnswer 中 SSE 解析正则有 Bug（CRITICAL）

**位置：**

* [AnalysisAnswer.vue#L149-L156](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/AnalysisAnswer.vue#L149-L156)

* [PredictAnswer.vue#L157-L165](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/PredictAnswer.vue#L157-L165)

**当前问题代码：**

```typescript
// AnalysisAnswer.vue —— 错误的正则方式
const split = tempResult.match(/data:.*}\n\n/g)
if (split) {
  chunk = split.join('')
  tempResult = tempResult.replace(chunk, '')
} else {
  continue
}
```

**问题：**

* 正则 `.*` 不匹配换行符 → 嵌套 JSON（含换行的 chart config / reasoning\_content）永远匹配不到，事件被延迟或丢失

* `.*}` 贪婪假设 payload 以 `}` 结束，但 `\n\n` 前可能有空白字符

* `.match()` → `.replace()` 方式会误删尚未完整接收的 chunk 边界

* **症状映射：** 思考过程事件（reasoning / tool-call）在流式期间丢失或直到全部接收完毕才一次性吐出 → 对应"思考中没有输出，正文完毕思考才出现"

**对比正确实现：** [ChartAnswer.vue#L153-L156](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/ChartAnswer.vue#L153-L156)

```typescript
// ChartAnswer.vue —— 正确的 \n\n 分割方式
const parts = tempResult.split('\n\n')
tempResult = parts.pop() || ''
```

***

### 根因 2：AnalysisAnswer / PredictAnswer 中错误地把 text-delta 追加到思考区（HIGH）

**位置：**

* [AnalysisAnswer.vue#L204-L206](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/AnalysisAnswer.vue#L204-L206)

* [PredictAnswer.vue#L212-L214](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/PredictAnswer.vue#L212-L214)

**当前问题代码：**

```typescript
// 非推理模型用 text-delta 作为思考内容
if (!hasNativeReasoning && data.content) {
  streamState.analysis_thinking = (streamState.analysis_thinking || '') + data.content
}
```

**问题：**

* `hasNativeReasoning` 只有在**首个** AIMessage 带有 `additional_kwargs.reasoning_content`（DeepSeek R1 等）时才被置为 true

* 但后端的 `AgentExecutor._run_agent` 在 [executor.py#L282-L297](file:///d:/Code/SQLBot/backend/apps/chat/agent/executor.py#L282-L297) 已经通过 `has_tools` 分支，把"工具调用前的叙事性文字"统一 emit 为 `reasoning` 事件 → 因此**即使模型没有原生 thinking，后端仍然有 reasoning 事件**

* 这个 fallback 导致 `text-delta`（最终答案）**同时写入了答案区和思考区**

* **症状映射：** 思考中内容 = 正文内容，一模一样 → 对应用户描述"思考中显示和正文一模一样"

***

### 根因 3：BaseAnswer.vue 中思考内容的字段查找与后端 JSON 结构不匹配（HIGH）

**位置：** [BaseAnswer.vue#L50-L67](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/BaseAnswer.vue#L50-L67)

**当前查找逻辑：**

```typescript
if (rn.value.includes('sql_answer')) {
  r = _read('sql_reasoning_content')   // ← DB 中不存在这个列！
}
if (rn.value.includes('analysis_thinking')) {
  a = _read('analysis_thinking')       // ← DB 中不存在这个列！
}
if (rn.value.includes('predict')) {
  p = _read('predict')                 // ← 这是整个 JSON {content, reasoning_content}，不是 reasoning！
}
```

**后端实际存储（executor.py save\_\*）：**

* QA：`ChatRecord.sql_answer` = JSON 字符串 `{"content":"...","reasoning_content":"..."}`

* 分析：`ChatRecord.analysis` = JSON 字符串 `{"content":"...","reasoning_content":"..."}`

* 预测：`ChatRecord.predict` = JSON 字符串 `{"content":"...","reasoning_content":"..."}`

**DB 实际列（chat\_model.py ChatRecord 表模型）：**

* 只有 `sql_answer` / `analysis` / `predict` 三个 Text 列 → `sql_reasoning_content` / `analysis_thinking` / `predict_reasoning_content` **只存在于前端 ChatRecord TS 类与 ChatRecordResult Pydantic 模型里，从未落库**

**结果：**

* 页面刷新后（从 DB 重新加载），`analysis_thinking` / `sql_reasoning_content` 读取值始终为 `''`

* Predict 则读到整个 `predict` JSON 字符串作为思考内容（不是解析后的 reasoning\_content）

* **症状映射：** 刷新后思考内容改变 / 空 → 对应用户描述"刷新，思考中内容又改变了"

**另外，QA 的 replyText：** [BaseAnswer.vue#L75-L77](file:///d:/Code/SQLBot/frontend/src/views/chat/answer/BaseAnswer.vue#L75-L77)

```typescript
const replyText = computed<string>(() => {
  return props.message?.record?.sql_answer || ''   // ← 直接返回原始 JSON！没解析！
})
```

→ QA 刷新后回复区会显示 `{"content":"...","reasoning_content":"..."}` 原始 JSON，需要解析后只取 `content`。

***

### 根因 4：后端 enable\_thinking 与 reasoning 路由边界可能需要复核（MEDIUM）

**位置：** [executor.py#L238-L241](file:///d:/Code/SQLBot/backend/apps/chat/agent/executor.py#L238-L241)

```python
object.__setattr__(self.llm, "enable_thinking", True)
```

这个会给 **所有** profile（QA / analysis / predict）统一开启 `thinking: {type: "enabled"}`。在 QA profile 中，Agent 每一轮都调工具（`msg.tool_calls` 非空），所以 content 为空 + reasoning 走 exploration narrative，这一块是对的。

但是对于一些**不支持 thinking 参数**的模型（Ollama / 非 DeepSeek-R1 的普通模型），传入 `extra_body: thinking` 可能导致 400 错误或静默忽略，需要加 fallback：

* 捕获"不支持 thinking 参数"错误，重试时去掉该参数

* 或者按模型类型（从 AiModelDetail 判断是否 R1 系列）选择开启

***

### 根因 5：BaseChatOpenAI `additional_kwargs.reasoning_content` 聚合一致性（LOW-MEDIUM）

**位置：** [openai/llm.py#L31-L35](file:///d:/Code/SQLBot/backend/apps/ai_model/openai/llm.py#L31-L35)

这段兼容了 `reasoning_content` 和 `reasoning` 两个字段，逻辑是对的。但流式聚合过程中，如果 provider 返回 **reasoning 在 content 之后的 chunk**，LangGraph astream 的聚合顺序可能错乱 → 导致 `_reasoning_output` 末尾重复拼接了 `content` 的尾部。建议在 executor 保存前做一次去重/裁剪（不过这是锦上添花，不是主因）。

***

## 三、需要修改的文件/模块

| 模块                          | 文件                                                                                        | 修改类型                                                                                                  |
| --------------------------- | ----------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 前端 SSE 解析                   | `frontend/src/views/chat/answer/AnalysisAnswer.vue`                                       | 替换正则解析为 `\n\n` 分割（对齐 ChartAnswer）                                                                     |
| 前端 SSE 解析                   | `frontend/src/views/chat/answer/PredictAnswer.vue`                                        | 同上                                                                                                    |
| 前端错误 fallback               | `frontend/src/views/chat/answer/AnalysisAnswer.vue`                                       | 删除"text-delta 拷贝到 thinking"的 fallback，并改为**只以 reasoning / tool-call 序列**为思考                           |
| 前端错误 fallback               | `frontend/src/views/chat/answer/PredictAnswer.vue`                                        | 同上                                                                                                    |
| 前端思考字段映射                    | `frontend/src/views/chat/answer/BaseAnswer.vue`                                           | DB 恢复时从 `sql_answer` / `analysis` / `predict` JSON 中**分别解析** `reasoning_content`；同时 replyText 解析 JSON |
| 前端 QA 回复 JSON 解析            | `frontend/src/views/chat/answer/BaseAnswer.vue` 或新增                                       | `replyText` 需解析 `sql_answer.content`                                                                  |
| 前端 QA 的 analysis 回复 JSON 解析 | `frontend/src/views/chat/answer/AnalysisAnswer.vue` →  `analysisContent` 已有 JSON 解析逻辑（保持） | 但思考区需同步读取 `analysis.reasoning_content`                                                                |
| 前端 QA 的 predict 回复 JSON 解析  | `frontend/src/views/chat/answer/PredictAnswer.vue` → `predictContent` 已有 JSON 解析逻辑（保持）    | 思考区需独立读取 `predict.reasoning_content`                                                                  |
| 后端 thinking 参数兼容            | `backend/apps/ai_model/openai/llm.py` (`BaseChatOpenAI._get_request_payload`)             | thinking 参数发送时捕获异常，可重试 fallback；或按模型白名单开启                                                             |
| （可选）DB 迁移                   | `backend/alembic/versions/` 新迁移                                                           | 如要把 reasoning\_content 独立列存，需 DDL；但当前计划优先采用 JSON 内解析，不加迁移                                             |

***

## 四、修改步骤（按依赖顺序）

### Phase 1：修复前端 SSE 解析 Bug（最紧急）

**目标：** 流式期间 reasoning / tool-call 事件不再丢。

1. **AnalysisAnswer.vue**：

   * 删掉 `tempResult.match(/data:.*}\n\n/g)` 正则分支

   * 替换为 ChartAnswer 同款：`parts = tempResult.split('\n\n')` → `tempResult = parts.pop() || ''` → 对每个 `part` 做 `part.startsWith('data:{')` 判断 → `JSONBig.parse(part.slice(5))`

2. **PredictAnswer.vue**：

   * 完全同上替换

### Phase 2：移除 text-delta → thinking 错误 fallback

**目标：** 思考区 ≠ 答案区，不再"一模一样"

1. **AnalysisAnswer.vue**：

   * 删除 `case 'text-delta':` 下的 fallback 分支（`if (!hasNativeReasoning...)`）

   * `hasNativeReasoning` 变量保留，但不再作为 fallback 开关；只要 `data.type === 'reasoning'` 就追加到思考

2. **PredictAnswer.vue**：

   * 同上删除对应 fallback

### Phase 3：修复 BaseAnswer 字段映射 & JSON 解析

**目标：** 刷新后思考内容 / 回复内容 正确恢复（不再变空 / 变 JSON）

1. **BaseAnswer.vue** 新增 3 个 computed helper：

   * `_parseAnswerJson(raw)` → 统一返回 `{content, reasoning_content}`

   * 分别处理 3 个字段：

     * QA：`sql_answer` → 解析后 `content` 给 replyText，`reasoning_content` 给 `sql_reasoning_content` 查找

     * 分析：`analysis` → 解析后 `reasoning_content` 给 `analysis_thinking` 查找

     * 预测：`predict` → 解析后 `reasoning_content` 给 predict 思考查找（**注意目前 predict 的 reasoning-name='predict' 会拿整个 JSON，必须改掉**）

2. **关键变更：BaseAnswer.vue reasoningContent 逻辑**：

   ```typescript
   // 伪代码
   if (rn.value.includes('sql_answer')) {
     const parsed = parseSqlAnswer(_record)
     if (parsed.reasoning_content) result.push(parsed.reasoning_content)
     // 同时保留 streamState.sql_reasoning_content（流式实时的）
     const live = _read('sql_reasoning_content')
     if (live && live !== parsed.reasoning_content) result.push(live)
   }
   if (rn.value.includes('analysis_thinking')) {
     const parsed = parseAnalysis(_record)
     const live = _read('analysis_thinking')
     result.push(live || parsed.reasoning_content || '')
   }
   if (rn.value.includes('predict')) {
     const parsed = parsePredict(_record)
     const live = _read('predict')  // 这里实际 streamState.predict 存的是 thinking
     result.push(live || parsed.reasoning_content || '')
   }
   ```

   （需要在 `_read` 之上加一层：streamingState 优先，否则 JSON 内部字段）

3. **BaseAnswer.vue replyText**：

   * QA 情形：`parseSqlAnswer(_record).content`  fallback 原字符串

### Phase 4：Predict/Answer 的 reasoning-name 一致性

* **PredictAnswer.vue** 传的 `reasoning-name="['predict']"`，这个语义很模糊（因为 BaseAnswer 会读 `record.predict`，它是 answer+reasoning 混合 JSON）。建议引入一个新的 `streamState.predict_reasoning`，并且在 BaseAnswer 里读 `predict_reasoning` / `predict.content.reasoning`，避免歧义。

* 同步：AnalysisAnswer 已用 `analysis_thinking` 保持不变；ChartAnswer 用 `sql_reasoning_content` 保持不变。

### Phase 5：后端 LLM thinking 参数兼容（可选但建议）

1. **BaseChatOpenAI.\_get\_request\_payload**：

   * 按 `AiModelDetail.base_model` 判断是否是 DeepSeek-R1 / 其他已知支持 thinking 的白名单

   * 或在 `_stream` 里捕获 provider 返回 400 的情况，在生成日志里标记后静默降级

***

## 五、潜在依赖与注意事项

1. **版本兼容**：已存在 DB 中旧格式的 `sql_answer` / `analysis` / `predict`（纯文本非 JSON）需要 fallback。解析失败时**必须返回 raw 字符串**，不能崩。

2. **Vue 响应式**：`streamState` 是 `reactive<Record<string, any>>`，新增字段时 Vue 3 自动追踪，但 `record.xxx` 字段是 `ChatRecord` class 实例属性，对**不存在**的 `analysis_thinking` 赋值可能在某些 TS 版本被 proxy 忽略 → 始终优先写 `streamState.*` 而不是 `record.*`。

3. **AbortController**：AnalysisAnswer / PredictAnswer 的 `stop()` 会 abort，SSE 解析重写后需要验证 `reader.read()` 抛错时 finally 分支仍然正确 `_loading.value = false`。

4. **后端** **`expand_thinking_block`**：[adapter.py#L53](file:///d:/Code/SQLBot/backend/apps/chat/agent/adapter.py#L53) 和 [executor.py#L282](file:///d:/Code/SQLBot/backend/apps/chat/agent/executor.py#L282) 用 `memory.expand_thinking_block` 控制是否 emit reasoning SSE。若前端传了 false，思考区会被后端天然静默；Phase 3 里不要假设 DB 总有 reasoning。

***

## 六、风险与处理

| 风险                                                                       | 影响          | 处理方式                                                                                                                           |
| ------------------------------------------------------------------------ | ----------- | ------------------------------------------------------------------------------------------------------------------------------ |
| SSE 解析重写后在某些浏览器（老版 Edge/Safari）上 chunk 分割行为不同                            | 解析死循环/丢事件   | 测试时用 `tempResult.length > 1MB` 设保险上限截断，避免无限 buffer                                                                             |
| 旧记录纯文本 sql\_answer（非 JSON）解析抛错                                           | 回复区空白       | JSON.parse 一律 try/catch，失败回退 raw text                                                                                          |
| PredictAnswer / AnalysisAnswer 去掉 fallback 后，对完全不支持 reasoning 的模型思考区一片空白 | 用户困惑"思考区空了" | 此时后端会 emit `tool-call` / `tool-result`，考虑在 BaseAnswer 中 **把 tool-call 序列作为最小思考展示**（用户截图中确实有 "思考过程" 折叠里的文字是工具叙述，不是模型 reasoning） |
| QA sql\_answer 改成 JSON 解析后，原来 ChartAnswer 流式期间直接写字段会与恢复逻辑不一致             | 流式时闪烁 / 双写  | 流式期间 streamState 优先；**only 当** **`!isTyping && record.finish`** 时走 DB JSON 解析路径（BaseAnswer.\_read 里可以做这个分支）                    |

***

## 七、验证清单（执行后必须逐一验证）

1. **QA 流式**：

   * 选一个 QA 问题，观察"思考中"按钮出现时是否能实时看到 reasoning（如"查找相关表"、"获取表结构"等工具叙述 + 模型原生 thinking）

   * 流式完毕后思考内容和正文内容应当**不同**

   * 刷新页面后思考内容应当与刷新前一致（不空白、不变成 JSON、不发生跳变）

2. **数据分析子智能体**：

   * 点击"数据分析"按钮后，观察思考按钮实时出现 + reasoning（数据摘要、预览等工具说明）流式出现

   * 正文（分析报告）出现顺序应当在思考之后

   * 思考区 ≠ 正文区（不再一模一样）

   * 刷新页面思考内容保持不变

3. **数据预测子智能体**：

   * 同上，额外验证 `search_web` 工具调用在思考区是否可见

   * 刷新页面预测内容 / 思考内容均正确恢复

4. **错误/中断**：

   * 流式中途点停止，再刷新不崩

   * 模型 500 / 超时，SSE `error` 事件能走通 ErrorInfo

5. **老旧记录兼容**：

   * 找一条升级前产生的 ChatRecord（sql\_answer 是纯文本非 JSON），刷新后回复区正常显示、思考区优雅退化（空或显示无）

***

## 八、执行顺序建议

1. **Phase 1 → Phase 2 一起做**（都是前端流式解析/显示问题，改完立即可以肉眼看到改善）
2. **Phase 3 + Phase 4**（BaseAnswer 字段映射 & JSON 解析）
3. **Phase 5**（后端兼容）根据测试情况决定是否需要，如果当前模型都能正常返回 thinking 可以跳过

