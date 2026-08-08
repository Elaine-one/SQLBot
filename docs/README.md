# SQLBot 文档中心

本文档中心面向项目使用者、维护者和编码 Agent。阅读时先确认文档状态：只有标为 **Current** 的文档可以作为当前实现依据；代码与测试是运行行为的最终事实来源。

## 文档状态

| 状态 | 含义 | 使用方式 |
|---|---|---|
| **Current** | 已按当前代码或部署配置核对 | 可作为开发、运维和交接依据 |
| **Decision** | 已确定的架构取舍及其理由 | 修改相关边界前必须阅读 |
| **Proposed** | 尚未批准或尚未完成的规格 | 不能当作既有能力 |
| **Archived** | 历史设计、实施计划或排障记录 | 仅用于追溯背景，不代表当前实现 |
| **Reference** | 外部项目或专项集成的调研资料 | 仅作参考，不能代替本项目事实 |

新的 Current、Decision 和 Proposed 文档应在标题后写明状态、最后验证日期、适用范围与代码/测试来源。

## 阅读路径

- **首次接手项目**：从 [开发手册](development/quickstart.md) → [代码库地图](architecture/codebase-map.md) → [问答 Agent 流程](architecture/chat-agent-flow.md) 开始。
- **修改 Agent、SSE 或图表**：阅读 [问答 Agent 流程](architecture/chat-agent-flow.md) 与 [Agent 维护手册](development/agent-maintenance.md)。
- **修改权限、账户或数据隔离**：阅读 [权限与子账户体系](architecture/permissions-and-tenancy.md)。
- **构建、部署与安装**：阅读 [部署与构建](deployment/docker-build-and-deploy.md)、[运维手册](operations/DataEase_SQLBot_运维手册.md) 和 [installer 使用说明](../installer/README.md)。
- **规划或实施重要改动**：先阅读 [开发变更流程](development/change-workflow.md)、[Spec 规范](specs/README.md) 和 [架构决策记录](decisions/README.md)。

## 当前文档

```text
docs/
├── architecture/     当前架构、数据流和模块边界
├── development/      本地开发、测试与维护流程
├── deployment/       构建、镜像和部署
├── operations/       当前运维与运行操作
├── reference/        外部研究与专项集成参考
├── testing/          测试资产和测试场景
├── decisions/        架构决策记录（ADR，待逐步建立）
├── specs/            已批准变更的规格（待逐步建立）
└── archive/          历史资料，不代表当前实现
```

### architecture/（Current）

| 文档 | 内容 |
|---|---|
| [overview.md](architecture/overview.md) | 系统模块边界与运行时全景 |
| [chat-agent-flow.md](architecture/chat-agent-flow.md) | 问答、分析、预测、SSE 与持久化主链路 |
| [state-and-storage.md](architecture/state-and-storage.md) | Chat、ChatRecord 与 AgentMemory 的存储边界 |
| [permissions-and-tenancy.md](architecture/permissions-and-tenancy.md) | 用户、工作空间、数据权限与子账户体系 |
| [codebase-map.md](architecture/codebase-map.md) | 项目目录、模块归属和常见改动入口 |
| [deployment-topology.md](architecture/deployment-topology.md) | Docker 开发与生产构建拓扑 |

### development/（Current）

| 文档 | 内容 |
|---|---|
| [quickstart.md](development/quickstart.md) | 本地与 Docker 开发、测试和常见问题 |
| [agent-maintenance.md](development/agent-maintenance.md) | Agent 工具、Profile、SSE 和维护入口 |
| [change-workflow.md](development/change-workflow.md) | 规格、实现、验证与文档更新流程 |

### deployment/ 与 operations/（Current）

| 文档 | 内容 |
|---|---|
| [docker-build-and-deploy.md](deployment/docker-build-and-deploy.md) | 构建链、开发镜像、生产镜像和离线包 |
| [DataEase_SQLBot_运维手册.md](operations/DataEase_SQLBot_运维手册.md) | 启停、日志与日常运维速查 |
| [installer 使用说明](../installer/README.md) | 安装、升级和卸载 |

### specs/ 与 decisions/（Current）

| 文档 | 内容 |
|---|---|
| [specs/README.md](specs/README.md) | 重要变更的规格、状态流转和模板 |
| [decisions/README.md](decisions/README.md) | 架构决策记录（ADR） |

### testing/（Current）

| 文档 | 内容 |
|---|---|
| [erp-test-cases.md](testing/erp-test-cases.md) | ERP 跨境电商测试场景与验收样例 |

### reference/（Reference）

| 文档 | 内容 |
|---|---|
| [metabase-ai-analysis.md](reference/metabase-ai-analysis.md) | Metabase AI（Metabot）架构调研 |
| [dingtalk/analysis.md](reference/dingtalk/analysis.md) | 钉钉接入可行性分析 |
| [dingtalk/guide.md](reference/dingtalk/guide.md) | 钉钉专项接入指南；不代表默认能力 |

### archive/（Archived）

[archive/README.md](archive/README.md) 汇集已完成、被替代或尚未批准的设计和实施计划。需要追溯历史时再阅读；开始新工作必须按新的 Spec 流程重新确认范围和验收标准。
