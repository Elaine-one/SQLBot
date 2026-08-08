# 思考过程（Thinking Process）配置统一架构 — 实施计划

## Context（为什么做这件事）

"思考过程"功能默认不显示，根因不是"缺开关"，而是**缺 DB 种子**：

```
xpack 编译模块 arg_manage.so 在 sys_arg 表为空时返回内置默认 'false'
  → get_groups() 拿到 'false'
  → LLMService.expand_thinking_block = False
  → executor._emit_reasoning = False  ← SSE reasoning 被完全抑制
  → 前端思考面板无内容
```

排查中还发现配置散落在 12 处（含 1 个死开关 `hide_thinking_block`、1 个绕过配置的代码 hack、若干调试日志），且**没有架构文档**说明哪些该留、哪些冗余。

**目标**：① 用 alembic 迁移种子 `chat.expand_thinking_block='true'` 作为权威默认；② 撤掉绕过配置的代码 hack；③ 还原验证有用的 stash 代码、清掉调试噪音；④ 补齐架构文档与代码注释。

**预期结果**：`llm.py` 零逻辑改动回到"尊重配置"的原始代码，DB 种子让原始代码自然返回 True，思考功能开箱即用；dev/prod/全新库都自动生效（alembic 每次启动跑）。

---

## 开关清单与处置决策

| 开关 | 位置 | 作用 | 处置 |
|------|------|------|------|
| `chat.expand_thinking_block` (xpack 内置) | arg_manage.so | 表空时的事实默认 `'false'` | **不改**（闭源），用 DB 种子覆盖 |
| `chat.expand_thinking_block` (sys_arg) | DB | 用户/系统权威值 | **★ 迁移 073 种子 `'true'`**（核心修复）|
| UI 开关 | parameter/index.vue:129 | 用户可见的开关 | **保留**，已存在并接好 |
| 前端 store | chatConfig.ts:18 / BaseAnswer.vue:35 | 面板展开/折叠偏好（非内容门控）| **保留**，加注释说明职责 |
| LLMService 类默认 | llm.py:47 (=True) | 兜底默认 | **保留**，加注释（实际总被 :151 覆盖）|
| AgentMemory | memory.py:95 (=True) | 传给 executor | **保留**，加注释 |
| `_emit_reasoning` | executor.py:282 | SSE reasoning 真正的门 | **保留**，加注释说明权威源 |
| `enable_thinking` | executor.py:240 / openai/llm.py:108 | 模型是否产生 reasoning_content | **保留**（与 expand_thinking_block 是不同概念）|
| `_thinking_disabled` | openai/llm.py + model_factory.py（stash 中）| 400 自动降级重试 | **从 stash 还原**（非 R1 模型必需）|
| `chat.hide_thinking_block` | xpack 返回 | 无任何代码消费 | **文档标注为死开关**（不可从 .so 移除）|
| 一行 hack | llm.py:154 (`= True`) | 绕过配置强制开 | **撤回**，改回 `config.pval...== 'true'` |
| `[Agent:debug]` 日志 | executor.py | 排查用 | **删除** |
| ChartAnswer console.log | ChartAnswer.vue | 排查用 | **不还原**（stash 里只有这两行调试）|

**关键认知**：`expand_thinking_block`（是否展示/emit）与 `enable_thinking`（模型是否思考）是两个不同概念，都保留。所谓"开关太多"其实是**一个配置值在多层传播**，真正要减的是：死开关（文档说明）、代码 hack（撤回）、调试日志（删除）。

---

## 1. 迁移 073（核心修复）

新建 `backend/alembic/versions/073_seed_expand_thinking_block.py`，`down_revision = '072a1b2c3d4e5'`（当前 head，已核实）。

```python
"""073_seed_expand_thinking_block

Seed chat.expand_thinking_block='true' into sys_arg.

WHY: xpack arg_manage.so returns builtin 'false' when sys_arg is empty,
silently suppressing all reasoning SSE emission. Migration 035 only
created the table (no seed), leaving 'false' as the effective value.
This seeds the authoritative 'true' default; still user-overridable
via the UI toggle (parameter/index.vue). Idempotent: sys_arg has NO
unique constraint on pkey, so uses INSERT ... WHERE NOT EXISTS.

Revision ID: 073a1b2c3d4e5
Revises: 072a1b2c3d4e5
"""
from alembic import op

revision = '073a1b2c3d4e5'
down_revision = '072a1b2c3d4e5'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.execute(
        "INSERT INTO sys_arg (pkey, pval, ptype, sort_no) "
        "SELECT 'chat.expand_thinking_block', 'true', 'str', 1 "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM sys_arg WHERE pkey = 'chat.expand_thinking_block'"
        ")"
    )

def downgrade() -> None:
    op.execute("DELETE FROM sys_arg WHERE pkey = 'chat.expand_thinking_block'")
```

- 幂等：`WHERE NOT EXISTS` 防重复（sys_arg 无 pkey 唯一约束，`ON CONFLICT` 不可用）
- 不指定 `id`：BigInt 主键自增
- main.py:52 lifespan 的 `run_migrations()` 每次启动跑 → dev/prod/全新库自动应用，无需手动 `alembic upgrade`

---

## 2. 代码改动

### 2.1 撤回一行 hack（`backend/apps/chat/task/llm.py:151-154`）
```python
# 撤回前（hack）
instance.expand_thinking_block = True
# 撤回后（尊重配置，DB 种子让它返回 'true'）
instance.expand_thinking_block = config.pval.lower().strip() == 'true'
```
配同一文件的 :47 类默认加注释：标注"兜底默认，总被 :151 覆盖；权威源是 sys_arg（迁移 073 种子）"。

### 2.2 从 stash 还原承重文件
`git stash pop` 后，按需保留以下文件（已验证承重）：
- `backend/apps/ai_model/model_factory.py` — `_patch_llm_for_thinking_support()`（全 LLM 类型注入 + 捕获 + 400 降级）
- `backend/apps/ai_model/openai/llm.py` — `_thinking_disabled` + `_stream` 降级重试（BaseChatOpenAI）
- `backend/apps/chat/agent/executor.py` — 非 R1 空 content 合成思考叙述
- `frontend/.../ChartBlock.vue` — v-if 移到 el-tooltip（修 ElOnlyChild）
- `frontend/.../AnalysisAnswer.vue` `PredictAnswer.vue` `BaseAnswer.vue` — SSE `\n\n` 解析 + DB 恢复 JSON 解析

### 2.3 还原后清理
- **executor.py**：删除所有 `[Agent:debug]` 的 `_log.info`（保留合成叙述逻辑和非 debug 的 `[Agent]` 日志）
- **ChartBlock.vue**：把 `:title="t('chart.type')"` 改回 `t('chat.type')`（stash 引入的回归，`chart.type` key 不存在）
- **ChartAnswer.vue**：**不还原**（stash 里只有 2 行 console.log 调试）

### 2.4 关键站点加注释（不改逻辑）
在以下位置加 1-3 行注释，指向架构文档：
- `executor.py:240`（enable_thinking）— 标注"与 expand_thinking_block 不同：本项控制模型是否产生 reasoning_content"
- `executor.py:282`（_emit_reasoning）— 标注"SSE reasoning 权威门，源自 memory.expand_thinking_block ← sys_arg"
- `memory.py:95`、`openai/llm.py:108`、`BaseAnswer.vue:32-35`、`chatConfig.ts:18`、`parameter/index.vue:129` — 各加职责说明

---

## 3. 文档（用户强调"注意文档规范"）

### 3.1 新建架构文档 `docs/design/thinking-config.md`
- 位置：`docs/design/`（已核实是架构设计文档规范目录，含 `multi-agent-design.md` 等）
- 在 `docs/README.md` 的 design/ 索引表登记
- 内容结构：背景与根因 / 配置链路图（单一权威源）/ 两个不同概念（expand_thinking_block vs enable_thinking）/ 开关清单表 / 非 R1 适配（patch + 降级 + 合成叙述）/ 用户控制（UI 开关位置与持久化）/ 死开关（hide_thinking_block）/ 相关文件清单

### 3.2 迁移 docstring
已嵌入迁移文件（见上 §1），说明 xpack 内置 'false' 根因与种子必要性，沿用 071/072 文档风格。

### 3.3 代码注释
见 §2.4，每个开关站点指向架构文档。

---

## 4. 验证步骤

1. **基线**：`git stash list` 仍有 stash；`alembic_version`=072；sys_arg 无 expand_thinking_block 行
2. **迁移应用**：启动后端 → lifespan 跑 alembic → 073 自动应用 → `SELECT pval FROM sys_arg WHERE pkey='chat.expand_thinking_block'` 返回 `'true'`；`alembic_version`=073
3. **幂等**：再重启，确认无重复行
4. **QA 路径**：问问题 → 思考面板渲染（R1 原生 reasoning / 非 R1 合成叙述）
5. **分析/预测路径**：analysis_thinking / predict_reasoning 流式，无 SSE 解析错误
6. **非 R1 模型**：切到非推理模型 → 日志见降级重试，不崩溃，合成叙述填充面板
7. **刷新恢复**：回答完成后刷新 → BaseAnswer 从 JSON 解析 reasoning_content，不显示原始 JSON
8. **配置尊重验证**：UI 开关关掉保存 → 新问题无 reasoning；开回来 → 恢复（证明 llm.py:154 尊重 config 而非强制 True）
9. **回归**：ChartBlock tooltip 显示 chat.type 文本，ElOnlyChild 警告消失；后端无 [Agent:debug] 噪音；前端无 console.log 噪音
10. **降级安全**：`alembic downgrade 072` → 行被删，思考回到 OFF（验证种子重要性）；`upgrade head` 重新种子

---

## 5. 实施顺序

1. 新建迁移 073（独立，无代码依赖）
2. 撤回 `llm.py:151-154` hack（与迁移同批，启动时迁移先跑再读配置）
3. `git stash pop` 还原承重文件
4. 清理（删 [Agent:debug]、修 t('chart.type')、跳过 ChartAnswer）
5. 加代码注释（§2.4）
6. 写 `docs/design/thinking-config.md` + 登记 `docs/README.md`

可单次提交，或拆成"配置修复 / 还原 stash 修复 / 文档"三个提交。

---

## 关键文件

- `backend/alembic/versions/073_seed_expand_thinking_block.py`（新建 — 核心修复）
- `backend/apps/chat/task/llm.py`（撤 hack :151-154，注释 :47）
- `backend/apps/chat/agent/executor.py`（还原合成叙述，删 debug 日志，注释 :240/:282）
- `backend/apps/ai_model/model_factory.py`（还原 `_patch_llm_for_thinking_support`）
- `backend/apps/ai_model/openai/llm.py`（还原降级）
- `frontend/src/views/chat/answer/BaseAnswer.vue`（还原 JSON 解析，注释 :32-35）
- `frontend/src/views/chat/chat-block/ChartBlock.vue`（修 `t('chart.type')`→`t('chat.type')`）
- `frontend/src/views/chat/answer/AnalysisAnswer.vue` `PredictAnswer.vue`（还原 SSE 解析）
- `docs/design/thinking-config.md`（新建）+ `docs/README.md`（登记）
