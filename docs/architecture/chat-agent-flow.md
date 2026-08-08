# SQLBot 当前问答执行流程

> 状态：Current  
> 最后验证：2026-08-08  
> 适用范围：主问答、分析和预测请求的后端执行链路。  
> 事实来源：`backend/apps/chat/api/chat.py`、`backend/apps/chat/agent/`。

本文描述本仓库当前代码，而不是上游 SQLBot 的历史 Pipeline。产品流程可以概括为：用户提问 → Agent 探索元数据 → 生成并执行 SQL → 保存结果 → 生成图表/文本 → 通过 SSE 返回前端。

## 1. 服务入口

`backend/main.py` 创建两个 FastAPI 应用：

- `app`：主 API，挂载 `apps.api.api_router`，默认由 `uvicorn main:app` 运行在 8000。
- `mcp_app`：挂载图片静态目录，并通过 `FastApiMCP` 将指定 API 操作暴露给 MCP 客户端，部署端口为 8001。

Docker 开发模式由根目录 `docker-compose.yml` 管理：前端 Vite 使用 5173，内置 PostgreSQL 使用 5432；生产镜像和离线包由 `installer/` 管理。

## 2. 主问答链路

### 2.1 创建对话和记录

前端先调用 `backend/apps/chat/api/chat.py` 的 `/start` 或 `/assistant/start` 创建 `Chat`。提交问题时，`/question` 进入 `stream_sql()`，由 `LLMService.create()` 完成当前用户、工作空间、助理、数据源和模型配置解析，并初始化 `ChatRecord`。

### 2.2 Agent 适配层

`stream_sql()` 调用 `apps.chat.agent.adapter.stream_agent()`，而不是直接调用旧 Pipeline。适配层负责：

1. 注册 Agent 工具（进程内惰性注册）。
2. 从 `Chat.memory_state` 恢复 `AgentMemory` 元数据。
3. 从 `ChatRecord` 查询近期问答，构建可控长度的对话历史。
4. 注入当前 session、用户、数据源、模型和记录等运行时上下文。
5. 执行 Agent，结束后保存内存元数据和执行日志。

跨轮次不使用 LangGraph Checkpointer。完整查询行不会写入 `memory_state`，而是保存到当前 `ChatRecord.data`。

### 2.3 Profile 和图

`backend/apps/chat/agent/engine.py` 定义三个 `AgentProfile`：

| Profile | 用途 | 后处理 |
|---|---|---|
| `qa` | 表探索、SQL 生成、查询和图表 | `execute_and_chart` |
| `analysis` | 基于已有记录做数据分析和图表 | `text_and_chart` |
| `predict` | 分析、预测及必要的外部信息搜索 | `text_and_chart` |

`backend/apps/chat/agent/graph.py` 构建 LangGraph ReAct 图。LLM 节点和工具节点循环执行；达到终端工具、最大迭代次数或无待执行 tool call 时结束。`executor.py` 负责事件转发、记录落库和后处理。

## 3. 工具和数据访问

工具定义分布在 `backend/apps/chat/agent/tools/`，由 `register_all.py` 注册到 `ToolRegistry`。典型职责如下：

- `schema_tools.py`：搜索相关表、读取表结构和样例数据。
- `sql_tools.py`：创建或编辑 SQL，并进行方言/安全校验。
- `query_tools.py`：执行查询、返回字段和结果数据。
- `chart_tools.py`：创建或编辑图表配置。
- `permission_tools.py`：应用数据源、行和列权限。
- `advanced_tools.py`：澄清、摘要等辅助能力。

多数据库连接和 SQL 执行由 `backend/apps/db/` 提供；术语库、SQL 示例、训练数据和表结构 embedding 由各自业务模块维护。系统元数据和 Agent 持久化状态使用 PostgreSQL/SQLModel，数据库变更使用 `backend/alembic/` 迁移。

## 4. SSE 和持久化

Agent executor 通过异步队列将事件交给 `stream_agent()`，API 再以 `text/event-stream` 返回。内部工具默认不直接暴露给前端；用户可见的工具调用、结果、思考片段、文本增量、图表和结束事件由前端聊天状态处理。

一轮结束时至少需要关注：

- `ChatRecord.sql`：当前问答生成的 SQL。
- `ChatRecord.data`：完整查询结果，供前端展示和图表重渲染。
- `ChatRecord.chart`：图表配置或结果。
- `ChatRecord.execution_log`：工具和执行步骤日志。
- `Chat.memory_state`：可跨轮次恢复的轻量 Agent 元数据。

## 5. 修改入口速查

| 需求 | 首先阅读 |
|---|---|
| 修改问答策略或 Agent 角色 | `backend/apps/chat/agent/engine.py`、`graph.py` |
| 新增/调整工具 | `agent/tools/registry.py`、`register_all.py` 及对应工具文件 |
| 修改 SQL 执行或权限 | `agent/tools/query_tools.py`、`permission_tools.py`、`apps/db/` |
| 修改图表类型 | `agent/chart_registry.py`、`chart_tools.py` 和前端聊天渲染 |
| 修改 SSE 事件 | `agent/executor.py`、`agent/adapter.py`、`frontend/src/` 的聊天 API/状态 |
| 修改登录、工作空间或数据源隔离 | `apps/system/`、`apps/datasource/`、权限依赖和迁移 |
| 修改部署/端口/镜像 | `docker-compose.yml`、`Dockerfile-base`、`installer/` |

相关开发、部署和 Agent 文档见 [`docs/README.md`](../README.md)。
