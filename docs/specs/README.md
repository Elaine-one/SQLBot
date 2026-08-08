# 变更规格（Spec）

> 状态：Current  
> 最后验证：2026-08-08  
> 适用范围：新能力、跨模块改造和高风险修复。

Spec 是实施前的共同契约。涉及 API、数据模型、权限、部署、Agent 行为、前后端协作或跨模块重构时，必须先建立一个目录：`docs/specs/<编号>-<主题>/spec.md`。

每份 Spec 至少包含：背景、目标、非目标、范围、验收标准、风险、受影响模块、迁移/回滚策略和验证计划。模板见 [TEMPLATE.md](TEMPLATE.md)。

状态流转：`Proposed` → `Accepted` → `Implemented` → `Archived`。只有 `Accepted` 的 Spec 可以作为实施依据；实现完成后必须更新验收结果或转入归档。

`backlog/` 保存已发现但尚未接受的技术待办，不能视为已承诺工作；正式 Spec 使用编号目录。

当前待办：

- [001：Thinking Process 配置、流式与恢复一致性](001-thinking-process/spec.md)
- [开发环境补齐 MCP 与 G2 SSR 服务](backlog/development-service-topology.md)
