# SQLBot 项目目录结构

本文件说明项目根目录下各子目录的用途。

## 目录速览

```
SQLBot-main/
├── backend/      ← Python 后端（API + Agent + 数据库引擎）
├── frontend/     ← Vue 3 前端（聊天界面 + 图表渲染 + 管理系统）
├── tests/        ← 后端测试套件
├── docs/         ← 项目文档（设计、开发计划、运维手册）
├── deploy/       ← Docker Compose 全栈部署配置
├── data/         ← 运行时持久化数据（PostgreSQL + SQLBot 日志）
├── installer/    ← 生产环境安装脚本
├── g2-ssr/       ← G2 图表服务端渲染（SSR，PNG 导出）
└── locales/      ← 后端国际化翻译文件
```

---

## [backend/](backend/)

Python 3.11 + FastAPI + LangGraph 后端服务。

```
backend/
├── main.py                # FastAPI 入口 + MCP Server (8001)
├── apps/                  # 业务模块
│   ├── chat/agent/        # ⭐ Agent 引擎 (ReAct + 15 工具)
│   ├── datasource/        # 数据源管理 + embedding
│   ├── db/                # 多数据库查询引擎 (12+ 类型)
│   ├── system/            # 用户/工作空间/鉴权
│   ├── dashboard/         # 仪表板 CRUD
│   ├── ai_model/          # LLM 供应商抽象
│   ├── terminology/       # 业务术语库
│   ├── data_training/     # SQL 训练示例
│   ├── template/          # 提示词模板 + 语义层
│   └── mcp/               # MCP Server 端点
├── common/core/           # 配置 + 依赖注入 + 工具函数
├── alembic/               # 数据库迁移脚本
├── templates/             # 提示词模板 + 方言指南
├── scripts/               # 辅助脚本
└── pyproject.toml         # uv 包管理配置
```

**关键依赖**：FastAPI, SQLAlchemy, LangChain/LangGraph, pgvector, pymssql, oracledb

---

## [frontend/](frontend/)

Vue 3 + TypeScript + Vite 6 前端。

```
frontend/src/
├── views/
│   ├── chat/              # ⭐ 聊天主界面 + 答案/图表渲染
│   ├── system/            # 系统管理（用户/模型/空间/权限）
│   ├── dashboard/         # 仪表板编辑器
│   ├── ds/                # 数据源表管理
│   └── embedded/          # 嵌入式助手
├── api/                   # API 客户端（SSE 流 + REST）
├── stores/                # Pinia 状态管理
├── router/                # Hash 路由
└── i18n/                  # 国际化（zh/en/ko/zh-TW）
```

**关键依赖**：Element Plus, AntV/G2/S2, Pinia, ECharts

---

## [tests/](tests/)

后端测试套件，覆盖 Agent 工具、编译器、图表系统、SQL 校验、供应商配置。

| 文件 | 覆盖范围 |
|------|---------|
| `test_agent.py` | Agent 工具注册、Memory、SQL 校验、图表工具、语法检查 |
| `test_compiler.py` | DataEase 数据集编译器（替换/映射/边界） |
| `test_supplier_config.py` | LLM 供应商配置（MiniMax 集成） |
| `test_cwe89_escape_fix.py` | SQL 注入防护 |
| `test_minimax_integration.py` | MiniMax API 连通性（需网络） |

---

## [docs/](docs/)

项目文档，按类型分为 6 个子目录。

| 子目录 | 内容 |
|--------|------|
| `design/` | 架构设计文档（Agent 升级、Compiler、多租户） |
| `dev-plans/` | 分阶段开发计划（Phase1~3） |
| `agent-maintenance/` | Agent 系统维护手册 |
| `operations/` | 运维手册 + 执行流程分析 |
| `reference/` | 外部参考资料（Metabase 分析） |
| `testing/` | 测试用例 |

详细索引见 [docs/README.md](docs/README.md)。

---

## [deploy/](deploy/)

Docker Compose 全栈部署配置。

```
deploy/
└── docker-compose.yaml    # API :8000 + MCP :8001 + PostgreSQL :5432
```

启动：`docker compose up -d`

---

## [data/](data/)

容器运行时持久化卷的宿主机挂载点。

```
data/
├── postgresql/            # PostgreSQL 数据文件
└── sqlbot/
    └── logs/              # SQLBot 容器内应用日志
```

---

## [installer/](installer/)

生产环境一键安装脚本。

```
installer/
├── install.sh             # 安装入口
├── uninstall.sh           # 卸载脚本
├── sctl                   # 服务控制工具（start/stop/restart/status）
├── install.conf           # 安装配置文件
└── sqlbot/
    ├── docker-compose.yml # Docker 编排模板
    └── templates/         # Nginx 等配置模板
```

---

## [g2-ssr/](g2-ssr/)

基于 Node.js 的 G2 图表服务端渲染服务。

用于将前端图表导出为 PNG 图片（仪表板导出、报告生成）。

```
g2-ssr/
├── app.js                 # 渲染服务入口
├── charts/                # 图表渲染器
├── Arial_Unicode.ttf      # 中文字体（渲染用）
└── ecosystem.config.js    # PM2 进程管理配置
```

---

## [locales/](locales/)

后端国际化翻译文件，供 OpenAPI 文档动态翻译使用。
