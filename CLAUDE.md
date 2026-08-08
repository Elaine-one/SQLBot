# 项目协作指南

本文件是本仓库的持久协作规则，供开发者与编码 Agent 使用。它只记录跨任务都必须遵守的约定；架构细节、历史背景和实施方案请查看 [docs/README.md](docs/README.md)。

## 项目与事实来源

- 本仓库是基于 DataEase SQLBot 的社区二次开发版本。当前行为以代码、迁移、自动化测试和标记为 **Current** 的文档为准；不要按上游旧文档猜测实现。
- `backend/` 是 Python/FastAPI 服务，`frontend/` 是 Vue/TypeScript 应用，`g2-ssr/` 提供图表服务端渲染，`installer/` 管理生产镜像和离线安装。
- 修改前先检查现有代码、迁移与文档；保留用户未提交的修改，不执行未经明确授权的删除、回滚或覆盖操作。

## 不可违反的架构边界

1. 默认问答由 `stream_sql()` 进入 `apps.chat.agent.adapter.stream_agent()`；旧 `apps/chat/task/llm.py` 只承担配置与兼容职责，不能将新问答行为加回旧 Pipeline。
2. Agent Profile 为 `qa`、`analysis`、`predict`。新增工具必须完成注册、Profile 分配、权限审查和测试，详见 [Agent 维护手册](docs/development/agent-maintenance.md)。
3. `Chat.memory_state` 仅存可恢复的轻量元数据；完整查询结果存于 `ChatRecord.data`。跨轮次上下文由数据库受控恢复，不引入共享 LangGraph Checkpointer。
4. 图表类型以 `backend/apps/chat/agent/chart_registry.py` 为唯一配置源；后端校验、提示词和前端渲染必须同步。
5. 所有数据访问必须保留当前用户、工作空间和数据源权限边界；登录接口接收前端加密后的凭据。
6. Docker 开发必须使用本地 `sqlbot-python-pg:local` 基座和源码挂载，不得用上游发布镜像代替本地代码。

## 变更流程

- 小型、局部且可验证的修复：先定位代码与测试，实施后运行相关检查，并同步更新受影响的 Current 文档。
- 新能力、跨模块重构、数据/API/权限/部署/Agent 行为变化：先在 `docs/specs/` 建立或更新 Spec，明确目标、非目标、验收条件和风险。
- 难以逆转的架构取舍：新增 ADR 到 `docs/decisions/`，不要只在聊天记录或提交说明中保留理由。
- 历史计划仅作背景；重新启动其工作时必须按新的 Spec 再验证，不可直接按归档方案实施。

## 完成定义

- 代码、迁移、测试、接口与文档保持一致；不留下失效链接或把 Proposed/Archived 内容描述成既有能力。
- 提交前检查 `git diff` 与 `git status`；按变更范围运行后端测试、静态检查或前端构建。
- 不确定文件是否废弃时，先检查 GitHub Actions、依赖配置、Docker/installer 引用和本地调用点。

## 常用命令

```bash
# Docker 开发
docker build -f Dockerfile-base -t sqlbot-python-pg:local .
docker compose up -d

# 后端（cd backend）
uv sync --extra cpu
uv run uvicorn main:app --port 8000 --reload
uv run pytest ../tests/ -v
uv run ruff check .
uv run mypy apps/

# 前端（cd frontend）
npm install
npm run dev                 # 若 vue-tsc 失败，改用 npx vite
npm run build
```

若社区版 `npm run dev` 因 xpack 缺失类型导致 `vue-tsc` 失败，可用 `npx vite --host 0.0.0.0` 启动开发服务器；不得为绕过该问题删除兼容代码。

## 文档入口

- [文档中心](docs/README.md)
- [系统架构](docs/architecture/overview.md)
- [问答 Agent 流程](docs/architecture/chat-agent-flow.md)
- [开发变更流程](docs/development/change-workflow.md)
- [Spec 规范](docs/specs/README.md)
- [架构决策记录](docs/decisions/README.md)
