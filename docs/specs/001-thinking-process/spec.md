# 001：Thinking Process 配置、流式与恢复一致性

> 状态：Proposed
> 创建日期：2026-08-08
> 适用范围：Thinking 配置、Agent SSE 事件、QA/分析/预测前端消费与持久化恢复。
> 相关历史资料：[Thinking 配置统一计划](../../archive/plans/thinking-config-unify.md)、[SSE 修复计划](../../archive/plans/streaming-sse-fix.md)。二者仅供追溯，不是实施依据。

## 1. 背景与问题陈述

系统将“是否产生模型推理内容”与“是否向前端展示/发送 reasoning 事件”分为两个概念：

- `enable_thinking`：请求模型输出原生推理内容的运行时参数。
- `chat.expand_thinking_block`：系统参数，控制 Agent 是否发送 reasoning SSE，以及前端默认展开状态。

迁移 `073_seed_expand_thinking_block.py` 已为 `chat.expand_thinking_block` 写入默认 `true`，系统参数页也已有对应开关。但现有代码尚未形成完整、可验证的端到端行为：不同模型、三种 Agent Profile、SSE 分段、刷新恢复和关闭开关仍可能表现不一致。

## 2. 已确认的现状

| 项目 | 结论 | 证据 |
|---|---|---|
| 配置默认值 | 已实现 | 迁移 `073_seed_expand_thinking_block.py` 写入 `sys_arg` 默认值 |
| 配置读取与传播 | 已实现 | `backend/apps/chat/task/llm.py`、`agent/adapter.py`、`agent/memory.py` |
| 模型推理参数 | 已实现 | `agent/executor.py` 设置 `enable_thinking`；`apps/ai_model/openai/llm.py` 注入请求参数 |
| reasoning SSE 门控 | 已实现 | `agent/executor.py` 依据 `memory.expand_thinking_block` 决定是否发送 |
| 管理端开关 | 已实现 | `frontend/src/views/system/parameter/index.vue`、`stores/chatConfig.ts` |
| 非推理模型自动降级 | 未确认，当前未发现完整实现 | `openai/llm.py` 只有参数注入，未发现 400 降级重试链路 |
| 分析/预测 SSE 解析 | 存在风险 | `AnalysisAnswer.vue`、`PredictAnswer.vue` 仍使用正则切分 SSE；与 `ChartAnswer.vue` 的分段方式不一致 |
| 刷新后 reasoning 恢复 | 存在风险 | `BaseAnswer.vue` 对 SQL/分析/预测结果的 reasoning 字段读取需按持久化 JSON 实际结构验证 |

## 3. 目标

1. 在支持原生 thinking 的模型上，QA、分析和预测均能稳定展示对应 reasoning 内容。
2. 在不支持 thinking 参数或 reasoning 内容的模型上，请求不因该参数失败；界面仍能提供符合产品约定的执行过程反馈。
3. SSE 分段到达、网络分块和页面刷新后，reasoning 与正文不会重复、丢失、错位或显示原始 JSON。
4. 关闭 `chat.expand_thinking_block` 后，新请求不向前端发送 reasoning SSE；重新开启后恢复正常行为。
5. 配置、后端事件、持久化字段和前端渲染的职责边界有测试和文档证据。

## 4. 非目标

- 不重构整个 Agent 图、Profile 模型或消息协议。
- 不改变模型供应商的公开 API 协议，除非为兼容不支持 thinking 的模型所必需。
- 不将模型的私有链式推理作为必须永久保存或完全展示的产品承诺。
- 不在本 Spec 中处理 G2 SSR、MCP 启动或其他独立开发环境问题。

## 5. 范围与受影响模块

| 层级 | 可能涉及的位置 |
|---|---|
| 配置与迁移 | `backend/alembic/versions/073_seed_expand_thinking_block.py`、`apps/chat/task/llm.py` |
| Agent 事件 | `apps/chat/agent/adapter.py`、`memory.py`、`executor.py` |
| 模型适配 | `apps/ai_model/openai/llm.py` 及模型工厂 |
| 前端流式消费 | `frontend/src/views/chat/answer/AnalysisAnswer.vue`、`PredictAnswer.vue`、`ChartAnswer.vue`、`BaseAnswer.vue` |
| 前端设置 | `frontend/src/stores/chatConfig.ts`、`frontend/src/views/system/parameter/index.vue` |
| 持久化与测试 | ChatRecord 相关 CRUD、后端测试与前端可重复验证步骤 |

## 6. 实施前必须完成的核查

以下内容尚未批准为具体实现方案，必须先复现并记录结果：

1. 选择一款支持原生 thinking 的模型和一款不支持该参数的模型，分别走 QA、分析、预测。
2. 确认不支持模型收到 thinking 参数时的响应；判断是否需要 400 降级重试。
3. 人为拆分 SSE 数据块，验证 AnalysisAnswer 与 PredictAnswer 的解析不会吞掉、合并或重复事件。
4. 在三种 Profile 完成后刷新页面，核对 ChatRecord 中保存的数据结构与 BaseAnswer 的恢复结果。
5. 在 UI 中关闭/开启 `chat.expand_thinking_block`，验证新请求实际发出的 SSE 事件集合。

## 7. 候选方案与决策门槛

- 若确认模型不支持 thinking 参数会导致请求失败，则在模型适配层增加可观测、一次性的兼容降级；不得静默无限重试。
- 若确认前端解析不一致，则统一采用按 SSE 空行边界保留残片的解析策略，并为分块输入补测试。
- 若确认持久化 JSON 与恢复字段不一致，则以 ChatRecord 实际 schema 为唯一来源，修正读取逻辑并为刷新恢复补测试。
- 若关闭开关仍发送 reasoning，优先修正后端事件门控；前端只负责展示状态，不能作为权限/事件过滤替代。

具体方案必须在核查结果明确后补充，并将状态改为 `Accepted` 后才能实施。

## 8. 验收标准

| 场景 | 预期结果 |
|---|---|
| QA + 原生 thinking 模型 | reasoning 流式展示，正文不重复进入 reasoning 区 |
| 分析 + 原生 thinking 模型 | reasoning 与分析正文分别显示，刷新后语义一致 |
| 预测 + 原生 thinking 模型 | reasoning 与预测正文分别显示，刷新后语义一致 |
| 不支持 thinking 的模型 | 请求可完成；兼容路径有日志或可观测证据，不出现未处理 400 |
| 任意 Profile + 关闭开关 | 新请求不输出 reasoning SSE，前端不展示旧轮次内容 |
| 任意 Profile + 网络分块 | SSE 事件不丢失、不重复、不因 JSON/换行分块解析失败 |

## 9. 风险、回滚与验证

- 风险：模型供应商对 thinking 参数的兼容性不同；SSE 事件字段变更会同时影响三类回答组件。
- 回滚：配置迁移的回滚必须谨慎处理用户已修改的 `sys_arg` 值；代码修复应保持与既有 ChatRecord 的兼容读取。
- 验证：补充后端事件测试、前端分块解析测试或可重复脚本，并执行上述六类手工验收场景。
