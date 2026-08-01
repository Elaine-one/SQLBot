# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此仓库中工作提供指导。目标是**最精简、只含 Claude 无法从代码直接推断的信息**。详细架构见下方 docs 引用。

## 项目概述

SQLBot 是开源 ChatBI（对话式数据分析）系统：用户用自然语言提问，系统自动探索数据库 Schema、生成 SQL、执行查询并渲染图表。支持多租户、DataEase 数据集集成、MCP Server 嵌入。

- **后端**：Python 3.11、FastAPI、LangChain/LangGraph、pgvector、SQLModel、uv 包管理
- **前端**：Vue 3 + TypeScript、Vite 6、Element Plus、Pinia、AntV/G2/S2
- **数据库**：PostgreSQL 15 + pgvector

## 构建、运行与测试

### 后端（`cd backend`）

```bash
uv sync --extra cpu                          # 安装依赖（CPU 版 PyTorch）
uv run uvicorn main:app --port 8000 --reload # 开发服务器
uv run pytest tests/ -v                      # 全部测试
uv run ruff check .                          # 代码检查
uv run mypy apps/                            # 类型检查
```

### 前端（`cd frontend`）

```bash
npm install && npm run dev    # 开发服务器 :5173
npm run build                 # 生产构建
```

### Docker（开发模式）

```bash
docker compose up -d          # API :8000, MCP :8001, 前端 :5173（源码挂载 + 热重载）
```

> ⚠️ **部署/构建请先读 `docs/deployment/docker-build-and-deploy.md`（SSOT）**。关键约束：
> - 开发模式基于本地构建的基座镜像 `sqlbot-python-pg:local`（`docker build -f Dockerfile-base -t sqlbot-python-pg:local .`）+ 源码挂载，改代码即时生效，不用重建镜像
> - **不要 `docker pull` 上游 `dataease/sqlbot` 镜像**——本地代码不在其中
> - `backend/uv.lock` 必须存在（应用镜像 `installer/Dockerfile` 的 bind mount 引用它）
> - 部署/离线包：`installer/`（含应用镜像 `installer/Dockerfile`、`installer/start.sh`、离线 compose）

### 环境变量

后端从 `../.env`（`backend/` 上一级，即仓库根）读取。核心配置类 `backend/common/core/config.py`（`Settings(BaseSettings)`）。关键变量见 `docs/deployment/docker-build-and-deploy.md` 附录。

## 架构概述

```
浏览器 → POST /api/v1/chat/question (SSE) → Agent 引擎（LangGraph ReAct）
  → Agent 调用工具（探索表/生成SQL/执行/生成图表）→ 流式 SSE 事件渲染
```

- **问答引擎**：`backend/apps/chat/agent/`（核心）。三种角色 `qa`/`analysis`/`predict`，定义于 `engine.py` 的 `AgentProfile`
- **工具系统**：`backend/apps/chat/agent/tools/`，全局注册表 `registry.py` + 15 个工具
- **图表类型 SSOT**：`backend/apps/chat/agent/chart_registry.py`（`CHART_TYPE_REGISTRY`）——所有 LLM 提示词 + 前端配置由此派生
- **多数据库查询**：`backend/apps/db/db.py`（`exec_sql()`，支持 12+ 类型）
- **嵌入搜索**：`backend/apps/ai_model/embedding.py`（`EmbeddingModelCache`，pgvector 语义搜索）

详细架构：
- 开发手册（如何启动/跑）：`docs/development/quickstart.md`
- Agent 系统维护：`docs/agent-maintenance/README.md`
- 部署/构建：`docs/deployment/docker-build-and-deploy.md`
- 问答执行流程：`docs/operations/execution-flow.md`

## 关键设计决策（Claude 猜不到、影响行为的）

1. **双引擎并存**：旧 Pipeline（`task/llm.py`）逐步废弃，新 Agent（`chat/agent/`）是默认（`stream_sql(engine="agent")`）。**改问答逻辑改 Agent，不要改旧 Pipeline**。
2. **图表类型唯一配置源是 `chart_registry.py`**：新增图表类型只需在 `CHART_TYPE_REGISTRY` 加一条记录，派生函数自动生效。不要在前端硬编码图表类型。
3. **登录凭据是加密的**：`apps/system/api/login.py` 对 username/password 先 `sqlbot_decrypt`，前端提交加密后的凭据。curl 测试登录需先加密，不能传明文。
4. **AgentMemory 不持久化查询数据**（`Chat.memory_state` JSON 列只存元数据，完整数据在 `ChatRecord.data`）。图表重渲染需从 ChatRecord 回读。
5. **社区版限制**：前端 `vue-tsc` 类型检查会失败（企业版类型缺失），开发用 `npx vite` 跳过；`sqlbot-xpack`（企业版）import 缺失模块需谨慎处理。

## 文档约定（给 Agent 的工作准则）

**① 改造前先检查，避免重复造轮子。** 任何新增/修复/改造，先 `grep` / 搜索代码与 docs，确认没有现成方案再动手。若 docs 已有某主题文档，改动后**同步更新它**。

**② 文档放正确的文件夹。** 写完新文档在 `docs/README.md` 索引登记，**不要写到 C 盘临时路径**。

| 文件夹 | 用途 |
|--------|------|
| `docs/development/` | 开发手册（如何启动/跑/配置/常见问题）。SSOT: `quickstart.md` |
| `docs/deployment/` | 部署与构建。SSOT: `docker-build-and-deploy.md` |
| `docs/design/` | 架构设计与技术方案 |
| `docs/dev-plans/` | 分阶段开发计划与实施（phase-N、踩坑记录） |
| `docs/operations/` | 运维手册 & 执行流程 |
| `docs/agent-maintenance/` | Agent 系统维护 |
| `docs/reference/` | 外部参考资料 |
| `docs/testing/` | 测试用例 |

**③ 撰写/审查要求。** 文档基于代码事实（给 `文件:行号` 引用），不臆造；改动代码后文档同步；大改动先出方案再实施。
