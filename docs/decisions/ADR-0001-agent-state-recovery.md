# ADR-0001：数据库恢复跨轮次 Agent 状态

> 状态：Decision  
> 决策日期：2026-08-08  
> 适用范围：聊天跨轮次上下文与工具消息隔离。

## 上下文

Agent 需要跨轮次恢复可用上下文，但完整工具调用和查询数据不应无差别进入下一轮模型上下文。

## 决策

不使用共享 LangGraph Checkpointer。适配层从 `Chat.memory_state` 和近期 `ChatRecord` 受控恢复上下文；完整查询数据仍由 `ChatRecord.data` 保存。

## 后果

- 可以按用户、工作空间和数据源边界恢复上下文。
- 避免内部 tool message 与大结果集被自动带入下一轮。
- 恢复逻辑需要随持久化模型变更同步测试。

## 证据

`backend/apps/chat/agent/adapter.py`、`graph.py`、`memory.py` 与 [状态边界文档](../architecture/state-and-storage.md)。
