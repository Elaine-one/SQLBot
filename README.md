<p align="center"><img src="https://resource-fit2cloud-com.oss-cn-hangzhou.aliyuncs.com/sqlbot/sqlbot.png" alt="SQLBot" width="300" /></p>

<h3 align="center">基于 DataEase SQLBot 的 Agent 化智能问数社区二次开发版</h3>

<p align="center">面向关系型数据源的对话式分析：自然语言提问、受控 SQL 查询、图表呈现与后续分析。</p>

> 本仓库是在 [DataEase SQLBot](https://github.com/dataease/SQLBot) 基础上的社区二次开发版本。当前实现以 Agent 作为默认问答引擎；本文档描述本仓库，而非上游发布版。

## 当前能力

- **Agent 化问答**：QA Agent 按需检索表结构、样例数据和字段值，生成、编辑、校验并执行 SQL。
- **分析与预测**：可基于已有查询记录启动独立的分析或预测流程，而不重复执行原始问答链路。
- **业务上下文**：术语、SQL 示例、提示词、数据源元数据和历史记录共同辅助问答。
- **流式交互**：后端通过 SSE 返回可见工具事件、文本、图表和完成状态。
- **权限与隔离**：问答运行时携带用户、工作空间和数据源上下文，数据访问遵循既有权限边界。
- **集成与部署**：提供 Web 前端、MCP 服务、Docker 开发编排、生产镜像及离线安装能力。

## 工作原理

### 面向用户的工作原理

用户可以通过自然语言提出数据问题。SQLBot 会结合业务配置和数据源信息，由 Agent 探索数据结构、生成并执行 SQL，最后返回数据表、图表和分析结果。

<p align="center">
  <img src="https://github.com/user-attachments/assets/8a8e90d8-920a-47e4-a6ba-93fc5a3a8b7c" alt="SQLBot 工作原理" width="900" />
</p>

产品层面可以概括为“自然语言提问 → 理解与查询 → 回答与分析”。实际执行不是单次 Prompt：Agent 会按需读取表结构、样例数据和字段信息，生成并执行 SQL（必要时自动修正），再将结果转换为用户可读的数据、图表或分析内容。

### 面向开发者的源码架构

下面的图对应当前仓库的实际调用链，说明 API、Agent 适配层、Agent Profile、工具、数据源、持久化和 SSE 之间的关系。

<p align="center">
  <img src="https://github.com/user-attachments/assets/027c8d9e-a13d-4c84-8b9c-8a338296a3b0" alt="SQLBot 源码架构" width="900" />
</p>

详细说明见 [系统架构概览](docs/architecture/overview.md)、[问答 Agent 流程](docs/architecture/chat-agent-flow.md) 和 [对话状态边界](docs/architecture/state-and-storage.md)。

## 快速开始（开发）

### Docker 开发环境

前置条件：Docker 与 Docker Compose。

```bash
docker build -f Dockerfile-base -t sqlbot-python-pg:local .
docker compose up -d
```

默认启动端口：主 API `8000`、前端 `5173`、PostgreSQL `5432`。`8001` 虽映射为 MCP 端口，但默认 Compose 不启动 `mcp_app`；G2 SSR 也需单独启动。首次运行前请根据 [.env.sample](.env.sample) 创建并配置 `.env`。

### 本地开发

```bash
# 后端
cd backend
uv sync --extra cpu
uv run uvicorn main:app --port 8000 --reload

# 前端（另开终端）
cd frontend
npm install
npm run dev                 # 若 vue-tsc 失败，改用 npx vite
```

完整开发、部署、安装和运维说明请从 [文档中心](docs/README.md) 进入。

## 开发与文档规范

- 当前架构、部署和操作说明均在 [`docs/`](docs/README.md)；只有标为 **Current** 的文档可作为当前实现依据。
- 新能力、跨模块重构、数据/API/权限/部署或 Agent 行为变化，应先按 [Spec 规范](docs/specs/README.md) 明确范围与验收标准。
- 长期架构取舍记录在 [ADR](docs/decisions/README.md)；历史计划和设计已移入 `docs/archive/`，不可直接作为实施依据。
- 开发者和编码 Agent 的通用约束见 [CLAUDE.md](CLAUDE.md)。

## 项目文档

| 主题 | 入口 |
|---|---|
| 代码库结构与改动入口 | [代码库地图](docs/architecture/codebase-map.md) |
| Agent、SSE 与持久化 | [问答 Agent 流程](docs/architecture/chat-agent-flow.md) |
| 权限与子账户 | [权限与子账户体系](docs/architecture/permissions-and-tenancy.md) |
| 本地开发与测试 | [开发手册](docs/development/quickstart.md) |
| 构建、镜像与离线安装 | [部署与构建](docs/deployment/docker-build-and-deploy.md) |
| 日常运维 | [运维手册](docs/operations/DataEase_SQLBot_运维手册.md) |

## 开源许可与致谢

本仓库遵循 [FIT2CLOUD Open Source License](LICENSE)：它基于 GPLv3，且包含附加条件。使用、分发或二次开发前请阅读完整许可证文本。

该许可证要求在 SQLBot 前端控制台或应用中不得移除或修改 SQLBot Logo 与版权信息；除附加条件外，其他权利与限制遵循 GPLv3。感谢 DataEase SQLBot 开源项目及其贡献者。
