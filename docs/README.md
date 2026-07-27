# SQLBot 文档索引

本目录包含 SQLBot 项目的设计文档、开发计划、运维手册和参考资料。

## 目录结构

```
docs/
├── README.md                  ← 本文件
├── README.en.md               ← English README
├── project-structure.md       ← 项目目录结构说明
├── agent-maintenance/         ← Agent 系统维护手册
├── design/                    ← 架构设计文档
├── dev-plans/                 ← 分阶段开发计划
├── operations/                ← 运维手册 & 流程分析
├── reference/                 ← 外部参考资料
└── testing/                   ← 测试用例
```

---

## [agent-maintenance/](agent-maintenance/)

Agent 系统维护与开发指南。

| 文件 | 说明 |
|------|------|
| [README.md](agent-maintenance/README.md) | Agent 系统维护手册：工具注册、角色定义、添加新工具的完整流程 |
| [case-study-tool-responsibility.md](agent-maintenance/case-study-tool-responsibility.md) | 案例研究：工具职责混乱导致内部查询泄露到前端的排查与修复 |

---

## [design/](design/)

架构设计与技术方案文档。

| 文件 | 说明 |
|------|------|
| [agent-comparison.md](design/agent-comparison.md) | Agent 升级：改造前后差异对比 |
| [agent-upgrade-plan.md](design/agent-upgrade-plan.md) | Agent 架构升级方案 |
| [legacy-issues-and-migration.md](design/legacy-issues-and-migration.md) | 遗留问题与迁移计划 |
| [multi-agent-design.md](design/multi-agent-design.md) | 多 Agent 架构设计 |
| [DataEase_多租户权限改造方案.md](design/DataEase_多租户权限改造方案.md) | DataEase 多租户分层治理改造方案 |
| [SQLBot_Compiler_Simplified_Design.md](design/SQLBot_Compiler_Simplified_Design.md) | 编译器简化方案 |
| [SQLBot_Dataset_Compiler_Design.md](design/SQLBot_Dataset_Compiler_Design.md) | DataEase 数据集编译层实施方案 |
| [SQLBot_旧管线代码清理分析.md](design/SQLBot_旧管线代码清理分析.md) | 旧 Pipeline 管线代码清理分析 |
| [dingtalk-integration-analysis.md](design/dingtalk-integration-analysis.md) | 钉钉接入可行性分析：鉴权/权限隔离/图表渲染 |
| [dingtalk-integration-guide.md](design/dingtalk-integration-guide.md) | 钉钉接入操作指南：从零到代码的分步流程 |

---

## [dev-plans/](dev-plans/)

分阶段开发计划与实施文档。

| 文件 | 说明 |
|------|------|
| [phase1-agent-migration.md](dev-plans/phase1-agent-migration.md) | 阶段 1：分析/预测功能迁移至 Agent 引擎 |
| [phase1.5-agent-optimization.md](dev-plans/phase1.5-agent-optimization.md) | 阶段 1.5：分析/预测 Agent 增强 |
| [phase2-chart-registry.md](dev-plans/phase2-chart-registry.md) | 阶段 2：图表类型配置化改造 |
| [phase3-chart-robustness.md](dev-plans/phase3-chart-robustness.md) | 阶段 3：图表系统改进方案 |
| [phase3-chart-robustness-impl.md](dev-plans/phase3-chart-robustness-impl.md) | 阶段 3：图表健壮性改造实施文档 |
| [execution-details-v2.md](dev-plans/execution-details-v2.md) | 执行详情优化方案 B |
| [execution-log.md](dev-plans/execution-log.md) | 执行详情 + Token 统计 + 思考中 统一方案 |
| [cleanup-lessons-learned.md](dev-plans/cleanup-lessons-learned.md) | 旧管道清理踩坑记录：Bug 修复、日志路径、Docker 配置经验 |

---

## [operations/](operations/)

运维手册与执行流程分析。

| 文件 | 说明 |
|------|------|
| [DataEase_SQLBot_运维手册.md](operations/DataEase_SQLBot_运维手册.md) | DataEase & SQLBot 运维速查：启动/停止/日志/状态 |
| [execution-flow.md](operations/execution-flow.md) | 问答执行流程与 DataEase 集成模式分析 |
| [conversation-fix.md](operations/conversation-fix.md) | 连续对话架构修复方案：跨轮次记忆持久化 |

---

## [reference/](reference/)

外部参考分析资料。

| 文件 | 说明 |
|------|------|
| [metabase-ai-analysis.md](reference/metabase-ai-analysis.md) | Metabase AI (Metabot) 架构分析 |

---

## [testing/](testing/)

测试用例与测试场景。

| 文件 | 说明 |
|------|------|
| [erp-test-cases.md](testing/erp-test-cases.md) | ERP 跨境电商 测试用例 |
