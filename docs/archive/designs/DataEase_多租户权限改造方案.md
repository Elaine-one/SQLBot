# DataEase 多租户分层治理改造方案

> 版本: v2.0
> 日期: 2026-07-21
> 基于: DataEase 2.10.25 社区版 + SQLBot

---

## 一、背景

### 1.1 问题起源

在评估 DataEase 是否满足需求时，最初的问题很直接：

> **DataEase 是否有权限功能？管理员配置好数据源，之后注册子账户，子账户登录后不用二次配置数据源，可以直接使用 SQLBot 插件来进行问图表作业？**

这背后是对 DataEase 权限体系的一个基本判断需求——能不能做到管理员集中配置、子账户开箱即用。

### 1.2 业务场景

一家电商公司，IT 部门需要为财务部、运营部、市场部等多个业务部门提供数据分析能力。

**IT 部门的角色**：负责在 DataEase 中配置数据源、定义数据表（数据集）。配置完成之后，按部门打标签分配——哪些数据源/数据集给财务部，哪些给运营部，哪些两个部门都能用。IT 是基础设施的提供者。

**业务部门的角色**：每个部门的成员登录 DataEase 后，应该自动看到分配给自己部门的资源，直接进行 BI 看板搭建和 SQLBot 自然语言问答。部门成员也可以自己创建新的数据源、自己搭建 BI 看板。IT 提供的是公共基础数据，部门可以在基础上自助扩展。

**部门之间的关系**：数据完全隔离。财务部不能看到运营部的数据，反之亦然。每个部门的 BI 看板只在组织内同步，SQLBot 问答也只能访问本组织被分配的数据。

**最终目的**：业务赋能——让业务部门能够自助完成数据分析，不需要每次都找 IT 提数或配连接。IT 管控边界，业务在边界内自由使用。

### 1.3 核心需求

**数据源标签分配：**

- IT 配置数据源（MySQL/PG/CK 等物理数据库连接）后，通过打标签的方式分配给一个或多个组织
- 例如 "MySQL-订单库" 同时打上「财务部」和「运营部」标签，两个部门都能用；"PG-财务库"只打「财务部」标签，仅财务部可用
- 非独占——一个数据源可以同时分配给多个组织

**用户与组织关系：**

- 一个用户只能属于一个组织。登录后自动进入所在组织，看到该组织被分配的数据源、数据集、BI 看板
- IT 可以创建组织内的账户（账号+密码），分配给对应部门

**组织内自建资源：**

- 部门成员自己也能够创建数据源和数据集
- 自建的资源默认只有创建者自己可见，可以手动设为"组织内全员可见"

**BI 看板：**

- 成员创建 BI 看板后，默认仅自己可见
- 可以手动设为"组织内同步"——设为同步后同组织成员可查看和复用
- 支持随时在"仅自己可见"和"组织内同步"之间切换

**SQLBot 问答：**

- SQLBot 是 DataEase 的一个嵌入式工具，不需要独立配置数据源
- DataEase 配置好数据源和数据集后，SQLBot 通过 DataEase 的 API 自动加载当前用户可见的数据集
- 子账户打开 SQLBot 就能直接用——不需要知道数据库 IP、端口、账号、密码，不需要在 SQLBot 里再配一遍

**管理系统：**

- 支持多个管理员账号（不只是社区版硬编码的 uid=1 超级管理员）
- IT 部门可以有多个具备管理权限的账号

### 1.4 社区版现状

DataEase 社区版已经具备数据源管理、数据集建模、BI 仪表板编辑、SQLBot 嵌入基础框架等能力。

但不具备的功能——组织隔离、两层独立授权（数据源+数据集）、子账户登录后按权限加载数据、SQLBot 权限继承——这些需要企业版 xpack 插件。

本次基于 DataEase 2.10.25 社区版进行二次开发，实现上述需求。

### 1.5 部署架构：一个前端，不分管理端和用户端

DataEase 当前只有一个服务（`localhost:8100`），Spring Boot 单体应用，同时承载前端 Vue SPA 和后端 REST API。管理员和普通用户登录的是**同一个地址**，没有独立的"管理后台端"或"用户端"。

改造后同样如此——**不新建用户端，不新增端口**。权限区分通过后端返回的菜单树实现：

```text
后端 /auth/busiResource 返回当前用户可见的菜单树

系统管理员登录                         普通成员（财务部小王）登录
──────────────────                    ──────────────────────────
  📁 数据源                              📁 数据源
  📁 数据集                              📁 数据集
  📁 仪表板                              📁 仪表板
  📁 系统管理 ← 管理员专有                （看不到系统管理）
    ├─ 用户管理
    ├─ 角色管理
    ├─ 组织管理
    └─ 权限分配
```

前端已有的动态路由机制（`establish.ts`）就是为此设计的——后端返回什么菜单，前端就渲染什么页面。管理员登录看到管理菜单，普通成员登录看不到，同一套前端代码、同一个 8100 端口。

---

## 二、关键概念：数据源与数据集

在讨论权限模型之前，必须先厘清 DataEase 中两个容易混淆的概念。

### 2.1 数据源（Datasource）—— 物理连接层

底层是 `core_datasource` 表，存储的是物理数据库的连接信息：

```text
MySQL-订单库  →  host: 10.0.1.5, port: 3306, database: orders, user: xxx, password: xxx
PG-用户库    →  host: 10.0.2.8, port: 5432, database: users,   user: xxx, password: xxx
CK-日志库    →  host: 10.0.3.2, port: 8123, database: logs,    user: xxx, password: xxx
```

数据源本身不含任何表选择、字段映射、SQL 逻辑——它只是一个"数据库连接串"。

### 2.2 数据集（Dataset）—— 逻辑建模层

底层是 `core_dataset_group` + `core_dataset_table` + `core_dataset_table_field` 表，存储的是基于数据源构建的逻辑模型：

```text
"订单全链路视图" →  基于 MySQL-订单库
                   选了 orders 表 + products 表
                   LEFT JOIN orders.product_id = products.id
                   字段：订单号、产品名、订单金额、下单时间...

"月度收入汇总"  →  基于 MySQL-订单库
                   自定义 SQL: SELECT ... GROUP BY DATE_FORMAT(..., '%Y-%m')
                   字段：月份、收入总额
```

数据集是 SQLBot 问答和 BI 看板直接消费的对象——用户在 SQLBot 下拉框选的"当前数据集"就是它，在 BI 中拖拽的字段也来自它。

### 2.3 两者关系

```text
数据源 (物理连接)                    数据集 (逻辑模型)
────────────────────               ────────────────────
MySQL-订单库 ────┬──→ "订单全链路视图" ←── SQLBot 问答用这个
                 │      (Join 建模)          BI 看板拖这个的字段
                 │
                 ├──→ "月度收入汇总"
                 │      (SQL 建模)
                 │
                 └──→ 成员也可以基于这个数据源自己建新的数据集

PG-用户库   ────→ "用户行为明细"
```

**数据源是管道，数据集是水龙头。** 用户可以拿到管道的接入权自己装水龙头，也可以直接用 IT 装好的水龙头。

### 2.4 前端命名的混淆点

DataEase 前端"新建数据源"这个入口实际做了两步合并操作：先填物理数据库连接信息（host/port/user/password），再在这个连接上选表、定义字段——后者本质是建数据集。但 UI 统称为"数据源管理"。而到了 SQLBot 的下拉框，又变成了"当前数据集"。这导致用户觉得概念模糊，但实际上：

- 前端"数据源管理"列表 = 物理连接 + 基于它自动生成的初始数据集
- SQLBot 下拉框里的选项 = 数据集（`tableName` 是数据集名，`dsName` 是底层数据源名）

---

## 三、两层独立授权模型

从源码 `AuthResourceEnum` 可以看到，数据源和数据集的权限是独立管理的：

```java
DATASET(5, 3), DATASOURCE(6, 4)  // 两个独立资源类型，各自授权
```

企业版 `queryEnterprise` SQL 中也印证了这一点——数据源权限（`rt_id=4`）和数据集权限（`rt_id=3`）分开查询，取交集。

### 3.1 IT 管理员可以分配两层

```text
IT 分配数据源                          IT 分配数据集
（物理连接）                           （逻辑模型）
─────────────────                    ─────────────────
MySQL-订单库 → [财务部][运营部]         "订单全链路视图" → [财务部]
PG-用户库   → [运营部]                "月度收入汇总"   → [财务部]
CK-日志库   → [运营部][市场部]         "用户行为明细"   → [运营部]
```

### 3.2 两层不是替代关系，是互补的

| 分配什么 | 成员拿到后能做什么 |
|----------|------------------|
| **只给数据源** | 可以自己基于数据源建新数据集（选表、SQL 建模），但不能直接用已有数据集 |
| **只给数据集** | 可以直接用数据集做 SQLBot 问答和 BI 看板，但不能自己建新的 |
| **两个都给** | 既有现成的数据集直接用，又能基于数据源自建扩展 |

### 3.3 典型场景

```text
财务部成员 小王 登录后：

  数据源面板                           数据集面板（SQLBot 下拉框）
  ┌─────────────────────────┐         ┌────────────────────────────┐
  │ IT 分配的数据源：         │         │ IT 分配的数据集：            │
  │  MySQL-订单库    ✅       │         │  "订单全链路视图"     ✅     │
  │  MySQL-财务库    ✅       │         │  "月度收入汇总"       ✅     │
  │                           │         │                            │
  │ 我自己建的数据源：         │         │ 我自己建的数据集：           │
  │  本地 Excel 导入           │         │  "个人分析视图" (私有)       │
  │                           │         │  "华东区订单"    (组织可见)  │
  └─────────────────────────┘         └────────────────────────────┘

  两种使用路径：
  路径 A：用 IT 给的现成数据集 → SQLBot 选"订单全链路视图" → 直接问答
  路径 B：用 IT 给的数据源 MySQL-订单库 → 自己建数据集 → 再用
```

### 3.4 可见性规则汇总

| 资源 | 来源 | 默认可见范围 | 可否修改 |
|------|------|-------------|---------|
| 数据源 | IT 配置 + 标签分配 | 被分配组织全员可见 | 仅系统管理员可修改标签 |
| 数据集 | IT 基于数据源构建 + 分配 | 被分配组织全员可见 | 仅系统管理员可修改 |
| 数据源 | 组织成员自建 | 仅创建者自己可见 | 可手动设为"组织全员可见" |
| 数据集 | 基于自建数据源创建 | 跟随数据源可见性 | 数据集自身也可单独调整可见性 |
| BI 看板 | 组织成员创建 | 仅创建者自己可见 | 可手动设为"组织内同步"，设为后组织内全员可查看，可随时撤回 |

### 3.5 可见性切换实现方案

#### 数据库设计：方案 A —— 加 visibility 字段

在 `core_datasource` 和 `core_dataset_group` 表中各加一个字段（BI 看板同理，具体表名待确认）：

```sql
ALTER TABLE core_datasource     ADD COLUMN visibility VARCHAR(16) DEFAULT 'private';
-- 值：'private'（仅自己可见） / 'org'（组织内公开）

ALTER TABLE core_dataset_group ADD COLUMN visibility VARCHAR(16) DEFAULT 'private';
```

查询时三合一：

```sql
WHERE (
    -- 1. IT 分配的（per_busi_resource 中 org_id 匹配）
    resource_id IN (SELECT resource_id FROM per_busi_resource WHERE org_id = #{oid})
    -- 2. 组织内公开的自建资源（同 org 成员建的且设为公开的）
    OR (visibility = 'org' AND create_by IN (
        SELECT uid FROM sys_user_org WHERE oid = #{oid}
    ))
    -- 3. 自己建的私有资源
    OR (visibility = 'private' AND create_by = #{uid})
)
```

> 为什么不用 `per_busi_resource` 来管自建公开（方案 B）：IT 标签分配和用户自建公开是两件不同的事——IT 分配是"强制的、跨组织的"，自建公开是"自主的、仅限本组织内"。用一个字段区分更直观，避免 `per_busi_resource` 里混在一起难以追溯来源。

#### 前端：资源编辑页增加开关

在数据源编辑页、数据集编辑页、BI 看板编辑页的可见区域，增加一个开关组件：

```text
┌──────────────────────────────────────────────┐
│  数据源名称: [MySQL-订单库___________]         │
│  连接信息:   host: [______] port: [____]     │
│  ...                                         │
│                                              │
│  可见范围:   ○ 仅自己可见                     │
│              ◉ 组织内公开（财务部全员可见）      │
│                           [保存]              │
└──────────────────────────────────────────────┘
```

| 组件 | 位置 | 逻辑 |
|------|------|------|
| 单选开关 / Switch | 数据源编辑表单底部 | 调用 API：`PUT /datasource/{id}/visibility` |
| 单选开关 / Switch | 数据集编辑表单底部 | 调用 API：`PUT /dataset/{id}/visibility` |
| 单选开关 / Switch | BI 看板编辑页工具栏 | 调用 API：`PUT /chart/{id}/visibility` |

后端新增三个接口：

| 接口 | 说明 |
|------|------|
| `PUT /datasource/{id}/visibility` | body: `{"visibility": "private" \| "org"}` |
| `PUT /dataset/{id}/visibility` | 同上 |
| `PUT /chart/{id}/visibility` | 同上 |

开关行为：

| 当前状态 | 操作 | 结果 |
|---------|------|------|
| 仅自己可见 → 点击开关 | 设为组织内公开 | 同组织其他成员立即可见 |
| 组织内公开 → 点击开关 | 撤回到仅自己可见 | 其他成员不可见（已引用此资源的看板/SQLBot 会话不受影响） |
| IT 分配的资源 | 开关不可用（灰掉） | 提示"此资源由管理员分配，无法修改可见性" |

---


## 四、角色定义

```text
IT 部门（系统管理员）
    │
    ├── 组织：财务部
    │   ├── 财务主管（组织管理员）
    │   ├── 会计小王（普通成员）
    │   └── 财务总监（只读成员）
    │
    ├── 组织：运营部
    │   ├── 运营主管（组织管理员）
    │   └── 数据分析师（普通成员）
    │
    └── 组织：市场部
        └── ...
```

| 角色 | 谁担任 | 能做什么 |
|------|--------|----------|
| **系统管理员** | IT 部门 | 创建组织；配置数据源；构建数据集；将数据源和数据集分配给组织；管理全局 |
| **组织管理员** | 各部门主管 | 管理本组织成员；自建数据源和数据集；管理组织内 BI 看板 |
| **普通成员** | 部门员工 | 使用分配的数据源和数据集；自建数据源和数据集；搭建 BI 看板；使用 SQLBot 问答 |
| **只读成员** | 管理层 | 查看 BI 看板；使用 SQLBot 问答（不可自建数据源和数据集） |

---

## 五、功能清单

| 编号 | 功能 | 说明 |
|------|------|------|
| F1 | 组织管理 | 创建/编辑/删除组织，组织树形结构 |
| F2 | 数据源 CRUD | 系统管理员或组织成员创建物理数据库连接 |
| F3 | 数据集 CRUD | 基于数据源构建数据集（选表、字段编辑、Join/Union/SQL 建模） |
| F4 | 数据源标签分配 | 将数据源打标签分配给一个或多个组织 |
| F5 | 数据集标签分配 | 将数据集打标签分配给一个或多个组织 |
| F6 | 组织间数据隔离 | 不同组织的成员只看到分配给本组织的数据源和数据集 |
| F7 | 组织内共享 | 同组织成员可看到彼此的数据源（组织可见的）、数据集和 BI 看板 |
| F8 | 自建资源私有默认 | 组织成员自建的数据源和数据集默认仅自己可见，可手动设为组织可见 |
| F9 | 角色管理 | 创建角色，区分系统管理员/组织管理员/普通成员/只读成员 |
| F10 | 子账户管理 | 创建用户（账号+密码），绑定组织+角色 |
| F11 | 子账户免配置登录 | 登录后自动加载所属组织的数据源和数据集 |
| F12 | BI 可视化 | 拖拽字段生成图表/仪表板 |
| F13 | BI 看板可见性切换 | 新建看板默认仅自己可见，可手动设为"组织内同步"，支持随时切换 |
| F14 | SQLBot 嵌入 | iframe 嵌入 DataEase 仪表板，打开即用 |
| F15 | SQLBot 自动加载数据集 | SQLBot 通过 DataEase API 获取当前用户可见的数据集 |
| F16 | 多系统管理员 | 支持多个超级管理员账号（社区版硬编码 uid==1） |
| F17 | 行/列级权限 | 敏感字段脱敏、数据行过滤（可选增强） |

---

## 六、SQLBot 的数据流转

SQLBot 不自己存储数据源配置，也不直接扫描数据库。嵌入模式下，它通过 DataEase 的 API 获取数据：

```text
子账户登录 DataEase
    │
    ▼
打开嵌入式 SQLBot（iframe，携带子账户 token/cookie）
    │
    ├── 步骤 1：SQLBot 调用 GET /sqlbot/dataset/{dvId}
    │   → DataEase 返回：该仪表板关联的数据集列表
    │   → 前端渲染下拉框：[订单全链路视图, 月度收入汇总, ...]
    │   → 用户选择一个数据集
    │
    ├── 步骤 2：SQLBot 调用 GET /sqlbot/datasource
    │   → DataEase 根据当前用户 oid + uid 过滤
    │   → 返回：所选数据集对应的数据库连接信息 + 表结构 + 字段元数据
    │
    ▼
SQLBot 用连接信息直连目标数据库
    → LLM 根据表结构 + 字段生成 SQL
    → 执行 SQL → 返回图表
    → 用户看到结果
```

**两层 API 的调用：**

| API | 用途 | 返回内容 |
|-----|------|---------|
| `GET /sqlbot/dataset/{dvId}` | 获取可用的数据集列表 | `(tableId, tableName, dsId, dsName)` |
| `GET /sqlbot/datasource` | 获取数据集底层的连接信息+ Schema | `(host, port, user, password, database, tables[].fields[])` |

**关键结论：SQLBot 不自己配置数据源，DataEase 传给它什么它就问什么。安全边界完全在 DataEase 的 API 层——只要 API 按 org + 权限正确过滤，SQLBot 就不会越权。**

### 社区版的安全风险

目前社区版 `DatasetSQLBotManage.getDatasourceList()`：

```java
if (!isAdmin) {
    return null;  // 非 admin 直接返回 null，SQLBot 不可用
}
list = queryAll();  // SQL 无任何 org_id 过滤，返回全部数据
```

`queryAll` 的 SQL：

```sql
-- 无条件，全表返回
SELECT ... FROM core_dataset_table cdt
JOIN core_datasource cd ON ...
JOIN core_dataset_group cdg ON ...
WHERE cdg.is_cross != 1 AND cd.status != 'Error'
```

**如果只去掉 isAdmin 限制但不同时替换 SQL，任意子账户登录后 SQLBot 会看到全库所有数据源和数据集，组织隔离完全失效。**

---

## 七、社区版 Gap 汇总

### 7.1 后端

| 文件 | 行号/方法 | 问题 | 影响 |
|------|----------|------|------|
| `DatasetSQLBotManage.java` | L184-188 | 非 admin 直接 `return null` | SQLBot 子账户不可用 |
| `DataSetAssistantMapper.java` | `queryAll()` | SQL 无 org_id 过滤 | 即使去掉 isAdmin，全部数据暴露 |
| `DataSourceManage.java` | L92 `tree()` | `@XpackInteract(invalid=true)` | 数据源树无 org 隔离 |
| `DatasetGroupManage.java` | L238 `tree()` | `@XpackInteract(invalid=true)` | 数据集树无 org 隔离 |
| `CoreVisualizationManage.java` | L77 `tree()` | `@XpackInteract(invalid=true)` | 仪表板树无 org 隔离 |
| `DatasetSQLBotManage.java` | L176 | `isAdmin = uid == 1` 硬编码 | 不支持多个管理员 |

### 7.2 前端

| 功能 | 社区版现状 |
|------|-----------|
| 用户管理 UI | ❌ 不存在（xpack 插件组件） |
| 角色管理 UI | ❌ 不存在（xpack 插件组件） |
| 组织管理 UI | ❌ 不存在（xpack 插件组件） |
| 数据源/数据集分配组织 UI | ❌ 不存在（xpack 插件组件） |
| 数据源管理 | ✅ 已有 |
| 数据集管理 | ✅ 已有 |
| BI 仪表板 | ✅ 已有 |
| SQLBot 嵌入基础 | ✅ `assistant.vue` 已有 |

### 7.3 需要保持不变的

以下功能社区版已完整支持，改造时直接复用：

- 数据源连接管理（添加/编辑/删除 MySQL/PG/CK/...）
- 数据集建模（选表、字段编辑、Union/Join/SQL）
- BI 仪表板编辑器（拖拽图表、大屏）
- SQLBot 嵌入基础框架（`assistant.vue` + `SQDatasetSelect.vue`）
- 系统参数配置（基本设置、引擎、地图、第三方嵌入）

---

## 八、二次开发方案

### 8.1 后端改造

#### 改造 1：新增按组织过滤的查询 SQL

**文件：** `DataSetAssistantMapper.java`

新增 `queryByOrg(oid, uid)` 方法，替代社区版 `queryAll()`。核心逻辑：

```sql
-- 返回的数据源和数据集 =
--   1. per_busi_resource 中标记分配给当前 org 的（IT 分配的）
--   2. 当前用户在组织内自建且设为"组织可见"的
--   3. 当前用户自建的私有数据
SELECT ... FROM core_dataset_table cdt
JOIN core_datasource cd     ON cdt.datasource_id = cd.id
JOIN core_dataset_group cdg ON cdg.id = cdt.dataset_group_id
JOIN core_dataset_table_field cdtf ON cdtf.dataset_group_id = cdg.id
WHERE
    -- IT 分配的数据源 + 数据集（标签匹配）
    (EXISTS per_busi_resource WHERE org_id = #{oid} AND resource_id IN (cd.id, cdg.id))
    -- 或自建数据源
    OR (cd.create_by = #{uid})
```

#### 改造 2：去掉 isAdmin 限制，切换为 org 过滤

**文件：** `DatasetSQLBotManage.java`

- 删除 `if (!isAdmin) return null`
- 将 `queryAll` / `queryCommunity` 调用替换为新的 `queryByOrg`

#### 改造 3：数据源树加 org 过滤

**文件：** `DataSourceManage.java` — `tree()` 方法

去掉 `@XpackInteract(invalid=true)` 依赖，直接实现按 oid 过滤的数据源树。

#### 改造 4：数据集树加 org 过滤

**文件：** `DatasetGroupManage.java` — `tree()` 方法

同理。

#### 改造 5：仪表板树加 org 过滤

**文件：** `CoreVisualizationManage.java` — `tree()` 方法

同理。

#### 改造 6：per_busi_resource 管理 API

| 接口 | 说明 |
|------|------|
| `POST /auth/saveBusiPer` | 已有接口，保存数据源/数据集的 org 分配 |
| `GET /org/{orgId}/datasources` | 查询某组织被分配的数据源 |
| `GET /org/{orgId}/datasets` | 查询某组织被分配的数据集 |
| `POST /org/assign` | 批量给数据源/数据集打标签分配给组织 |

#### 改造 7：支持多系统管理员

去掉 `isAdmin = uid == 1` 硬编码，改为从 `per_role` 表判断管理员角色。

### 8.2 前端改造

#### 新建页面

| 页面 | 路径 | 功能 |
|------|------|------|
| 用户列表 | `views/system/user/index.vue` | 子账户分页列表、搜索、启用/禁用 |
| 用户编辑 | `views/system/user/form.vue` | 创建/编辑用户、绑定组织+角色 |
| 角色管理 | `views/system/role/index.vue` | 角色 CRUD、菜单+资源授权 |
| 组织管理 | `views/system/org/index.vue` | 组织树、CRUD |
| 组织分配 | 在数据源/数据集编辑页增加 | 给资源打标签分配给组织的多选组件 |

#### 改造现有页面

| 页面 | 改动 |
|------|------|
| `datasource/form/index.vue` | 增加"分配给组织"多选标签 |
| `dataset/form/index.vue` | 增加"分配给组织"多选标签 |

### 8.3 工作量估算

| 模块 | 内容 | 人天 |
|------|------|------|
| 后端 | queryByOrg SQL + isAdmin 去除 | 1 |
| 后端 | tree() × 3 方法 org 隔离 | 1.5 |
| 后端 | per_busi_resource 管理 API | 1 |
| 后端 | visibility 字段 ALTER + 切换 API（3个资源） | 0.5 |
| 后端 | 多管理员支持 | 0.5 |
| 前端 | 用户管理（列表+编辑） | 2 |
| 前端 | 角色管理 | 1.5 |
| 前端 | 组织管理 | 1 |
| 前端 | 数据源/数据集组织分配组件 | 1.5 |
| 前端 | 可见性开关组件（数据源+数据集+BI看板） | 1 |
| 前端 | 现有页面改造 | 0.5 |
| 联调测试 | 前后端 + SQLBot 权限逐项验证 | 2 |
| **合计** | | **14 人天** |

---

## 九、安全性验证清单

- [ ] 财务部用户登录 SQLBot，下拉框只能看到分配给财务部的数据集
- [ ] 运营部用户登录 SQLBot，下拉框只能看到分配给运营部的数据集
- [ ] `/sqlbot/datasource` API 不会返回用户无权访问的数据源连接信息
- [ ] 财务部用户在 BI 中看不到运营部的仪表板
- [ ] 用户自建的数据源默认仅自己可见
- [ ] 用户将自建数据源设为"组织可见"后，同组织其他人可看到
- [ ] 组织 A 用户无法通过修改 API 参数枚举到组织 B 的数据源
- [ ] SQLBot 执行 SQL 时使用 DataEase 传递的数据库凭据，不额外存储
- [ ] 多个管理员账号均可正常管理组织、分配数据源和数据集

---

## 十、关键源码索引

### 后端

| 文件 | 路径 |
|------|------|
| SQLBot 数据源 API | `core-backend/.../dataset/server/DatasetSQLBotServer.java` |
| SQLBot 数据源构建 | `core-backend/.../dataset/manage/DatasetSQLBotManage.java` |
| SQL 查询 Mapper | `core-backend/.../dataset/dao/ext/mapper/DataSetAssistantMapper.java` |
| 数据源管理 | `core-backend/.../datasource/manage/DataSourceManage.java` |
| 数据集管理 | `core-backend/.../dataset/manage/DatasetGroupManage.java` |
| 仪表板管理 | `core-backend/.../visualization/manage/CoreVisualizationManage.java` |
| 权限注解 | `sdk/common/.../auth/DePermit.java` |
| 资源枚举 | `sdk/common/.../constant/AuthResourceEnum.java` |
| 用户 API | `sdk/api/api-permissions/.../user/api/UserApi.java` |
| 角色 API | `sdk/api/api-permissions/.../role/api/RoleApi.java` |
| 组织 API | `sdk/api/api-permissions/.../org/api/OrgApi.java` |
| 权限 API | `sdk/api/api-permissions/.../auth/api/AuthApi.java` |

### 前端

| 文件 | 路径 |
|------|------|
| 动态路由加载 | `core-frontend/src/router/establish.ts` |
| Xpack 组件加载器 | `core-frontend/src/components/plugin/src/index.vue` |
| SQLBot 嵌入 | `core-frontend/src/views/sqlbot/assistant.vue` |
| SQLBot 数据集选择 | `core-frontend/src/views/sqlbot/SQDatasetSelect.vue` |
| 系统参数 | `core-frontend/src/views/system/parameter/index.vue` |
| 数据源表单 | `core-frontend/src/views/visualized/data/datasource/form/index.vue` |

### SQLBot

| 文件 | 路径 |
|------|------|
| README | `SQLBot-main/README.md` |
| 执行流程分析 | `SQLBot-main/docs/sqlbot-execution-flow.md` |
| 数据集编译器 | `SQLBot-main/docs/SQLBot_Dataset_Compiler_Design.md` |
