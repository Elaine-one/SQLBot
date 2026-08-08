# SQLBot Agent 遗留问题与迁移计划

> 编写日期: 2026-07-14（更新: 2026-07-15）
> 状态: 持续更新中
> 策略: 先替换（旧管线 → Agent），后优化（图表/主题/语义层）
> 背景: 主问答已迁移至 Agent 引擎，数据分析/预测/执行详情等功能仍在旧线性管线上

---

## 一、Agent 引擎已修复问题（已完成）

| # | 问题 | 根因 | 修复状态 |
|---|------|------|---------|
| 1 | `iter_count` NameError | 局部变量跨方法引用 | ✅ 已修复 |
| 2 | `'list' object has no attribute 'keys'` | `exec_sql` 返回 `fields` 是 list 而非 dict | ✅ 已修复 |
| 3 | 连续对话 Memory 失忆 | AgentMemory 每次请求重建，未持久化 | ✅ Chat.memory_state JSON 列 |
| 4 | 追问 LLM 400 错误 | MemorySaver 泄露上轮 tool_call 消息 | ✅ 移除 MemorySaver，每问独立 thread_id |
| 5 | 左侧历史标题显示日期 | create_chat 未传问题文本时 brief 设为 datetime | ✅ `_update_chat_brief()` 自动修复 |
| 6 | 历史数据"暂无数据" | `finish_record()` 从未调用 | ✅ finally 块保证执行 |
| 7 | 追问 chart-only edit 不渲染 | `query.data` DB 恢复后为 None + sql-data 先于 save 发出 | ✅ 修顺序，从旧 Record 复制数据 |
| 8 | 前端 SSE 贪婪正则吞事件 | `/data:.*}\n\n/g` 贪婪匹配嵌套 JSON | ✅ 改为 `\n\n` 分隔 |
| 9 | 图表等 finish 才显示 | `ChartBlock.vue` 条件 `!message.isTyping &&` | ✅ 移除 isTyping 限制 |
| 10 | SQL LIMIT 规则过严 | "全部""所有"加 LIMIT + 聚合也加 LIMIT | ✅ 修正为仅明细加 LIMIT |
| 11 | 线程池紧轮询阻塞事件循环 | `_chunk_list` + 紧轮询 + `is_running()` 检查 | ✅ asyncio.Queue + run_in_executor |
| 12 | chart-only edit SSE 流为空 | 线程池未启动时 await_result 已排空 | ✅ queue 架构消除竞态 |
| 13 | 视图查询挂死 | `SELECT * FROM view LIMIT 3` 视图全表扫描 | ✅ TABLESAMPLE SYSTEM(0.1) + 30s 超时 |
| 14 | 提示词模块冲突 | 语义层 vs Know Your Data 优先级矛盾 | ✅ 三层优先级（语义层→探索→查询规范） |
| 15 | f-string 中文花括号报错 | `{数据源名}` 被 Python f-string 解析 | ✅ 双花括号转义 |
| 16 | 字段语义误解导致利润计算错误 | Agent 用 `subtotal`+`cost_price` 算利润，实际应用 `erp_profit_snapshots` | ✅ 语义层 JSON（erp_metrics + erp_segments + erp_derived_metrics） |
| 17 | Agent 思考文本在"思考中"折叠 | `sql_answer` 只放在 collapsible 块 | ✅ 主回答区直接展示 `sql_answer` |
| 18 | 思考块不展示工具调用过程 | `reasoningName` 只有 chart_answer | ✅ tool_calls_log 展示工具进度 |

## 二、图表架构问题（前后端协作）

### 2.1 三层责任分散，无协调

当前图表生成跨三层：

```
[executor.py]   → 关键词匹配决定 chart_type（"柱状图"→column,"扇形"→? 匹配不到）
[Chart LLM]     → 再调一次 LLM 生成 axis/columns/series JSON（额外 token + 延迟）
[前端 G2]       → 渲染：颜色写死、轴标题关掉、标签自动隐藏
```

**核心问题**：Chart LLM 是多余的。executor 已有数据和 chart_type 提示，前端完全可以根据数据特征自动选图表。Metabase 不做 Chart LLM——数据返回后 fingerprinting 自动选 viz。

### 2.2 坐标轴标签不显示/随机隐藏

- **文件**: `Column.ts:86-95`, `Bar.ts` 同
- **根因**: `axis.x.title = false` 硬编码关闭；`labelAutoHide: { type: 'hide' }` 自动隐藏重叠标签
- **状态**: 待优化

### 2.3 图表类型硬编码为 5 种

- **后端**: [template.yaml:414](backend/templates/template.yaml#L414) 规则写死 table/column/bar/line/pie
- **前端**: 对应 5 个 chart class — 无堆叠图、面积图、散点图
- **executor.py**: `_generate_chart` 关键词匹配太粗暴，"扇形"匹配不到 pie
- **状态**: 待扩充

### 2.4 Chart LLM 调用冗余

- **位置**: [executor.py](backend/apps/chat/agent/executor.py) `_generate_chart` → 调 `self.llm.stream(chart_messages)`
- **问题**: 每次问答额外调一次 LLM，增加 2-3s 延迟和 ~500 tokens 消耗
- **方向**: 削掉这一层 → 前端根据数据特征自动选图表 → agent 通过 `edit_chart` 切换类型
- **状态**: 待设计

### 2.5 图表颜色/主题/交互

- 颜色、图例位置、轴样式全部在前端各 chart class 硬编码
- 无用户自定义主题能力
- **状态**: 待优化

## 三、旧管线功能清单（待迁移 — 当前按钮均不可用）

> **状态**: 所有以下按钮仍在使用旧线性 Pipeline（`LLMService.run_*`），
> 未适配 Agent 引擎。用户点击后会看到旧格式的响应或错误。

### 3.1 数据分析按钮 (Data Analysis)

- **UI 位置**: 图表卡片下方 "数据分析" 按钮
- **入口**: `POST /api/v1/chat/record/{id}/analysis`
- **当前实现**: [chat.py:474-542](backend/apps/chat/api/chat.py#L474-L542) → `LLMService.run_analysis_or_predict_task_async()`
- **前端组件**: `AnalysisAnswer.vue` — 监听 `analysis-result` / `analysis` SSE 事件（旧格式）
- **Agent 替代**: `analyze_query_result` 工具（已有）
- **SSE 格式差异**: 旧管线发 `analysis-result` + `analysis`，agent 发 `text-delta` + `chart` + `finish`

### 3.2 数据预测按钮 (Data Prediction)

- **UI 位置**: 图表卡片下方 "数据预测" 按钮
- **入口**: `POST /api/v1/chat/record/{id}/predict`
- **当前实现**: 同 analysis，走 `run_analysis_or_predict_task_async()`
- **前端组件**: `PredictAnswer.vue` — 监听 `predict-result` / `predict`
- **Agent 替代**: 需新增 `predict_data` tool（**当前不存在，无法迁移**）
- **短期方案**: 先在前端隐藏此按钮，待 predict tool 开发完成后放回

### 3.3 执行详情按钮 (Execution Details)

- **UI 位置**: 图表卡片 "执行详情"
- **入口**: `GET /api/v1/chat/record/{id}/log`
- **当前实现**: [curd/chat.py](backend/apps/chat/curd/chat.py) `get_chat_log_history()` — 读取 `chat_log` 表
- **格式**: 旧 Pipeline 步骤名称（GENERATE_SQL / GENERATE_CHART / ANALYSIS 等）
- **Agent 状态**: Agent 不写 `chat_log` 表 → 执行详情为空或显示旧格式
- **Agent 替代**: 前端 `tool_calls_log`（已捕获 tool-call/tool-result）
- **前端组件**: `ExecutionDetails.vue`

### 3.4 推荐追问 (Recommend Questions)

- **触发**: 问答完成后自动请求
- **入口**: `POST /api/v1/chat/recommend_questions/{record_id}`
- **当前**: 两引擎并存，默认 pipeline
- **Agent 替代**: `stream_agent_recommend()` 已可用

### 3.5 旧 Pipeline 主问答回退

- **触发**: `POST /api/v1/chat/question` 时 `engine=pipeline`
- **当前**: [chat.py:383-427](backend/apps/chat/api/chat.py#L383-L427)
- **建议**: 灰度期后删除

## 四、可删除的旧代码（确认后执行）

| 文件 | 删除范围 | 影响 |
|------|---------|------|
| `task/llm.py` | `run_analysis_or_predict_task_async()` | 分析/预测旧管线 |
| `task/llm.py` | `generate_analysis_task()` / `generate_predict_task()` | 内部方法 |
| `task/llm.py` | `generate_recommend_questions_task()` | 推荐追问旧管线 |
| `task/llm.py` | `run_recommend_questions_task_async()` | 同上 |
| `models/chat_model.py` | 旧 `AiModelQuestion` 中的 analysis/predict prompt 方法 | 旧模板 |
| `templates/generate_analysis/` | 整个目录 | 旧模板 |
| `templates/generate_predict/` | 整个目录 | 旧模板 |
| `api/chat.py` | `engine=pipeline` 分支 | 旧主问答 |
| `api/chat.py` | `analysis_or_predict()` 旧路由 | 改为 agent 分发 |

## 五、统一 Agent 架构设计（草案）

### 5.1 目标

```
POST /chat/question
  → AgentEngine.dispatch(profile, question, memory)
      ├─ profile="qa"        → 主问答 Agent（全部 14 工具）
      ├─ profile="analysis"  → 分析 Agent（仅 analyze_query_result）
      ├─ profile="predict"   → 预测 Agent（predict + analyze tools）
      └─ profile="recommend" → 推荐追问 Agent（轻量 prompt）
```

### 5.2 Agent Profile 定义

```python
@dataclass  
class AgentProfile:
    name: str                   # "qa" | "analysis" | "predict"
    system_prompt: str          # 专用 System Prompt
    tools: list[str]            # 允许的工具名列表
    terminal_tools: list[str]   # 终端工具
    max_iterations: int         # 最大迭代数
    post_process: str           # "execute_and_chart" | "text_only" | "emit_chart"
```

### 5.3 三个 Profile

| Profile | 工具 | 迭代上限 | 后处理 |
|---------|------|---------|--------|
| `qa` | 全部 13 工具 | 50 | execute_and_chart |
| `analysis` | `analyze_query_result` | 5 | text_only |
| `predict` | `predict_data`（新增）, `analyze_query_result` | 10 | emit_chart |

## 八、新架构：语义层（2026-07-15 新增）

### 8.1 三层设计

```
System Prompt（通用，不变）     →  教 agent 何时加载语义文件、如何组装 SQL
Semantic JSON（数据源特定）     →  指标 SQL 片段、派生公式、可复用筛选条件
load_skill（通用加载机制）      →  按需加载，不占初始 prompt
```

### 8.2 当前文件

```
templates/business/
  erp_metrics.json          18 个基础指标（每个带 SQL 片段、别名、涉及表、JOIN 条件）
  erp_segments.json          3 个筛选条件（有效订单、中国以外、已完成发货）
  erp_derived_metrics.json   8 个派生指标（毛利率=毛利/收入*100 等）
  erp_cross_border.yaml      数据域分类、字段语义说明（人工可读）
```

### 8.3 语义层指标框架（参考 agent-data-demo）

| 文件 | agent-data-demo | SQLBot ERP |
|------|----------------|------------|
| base_metrics.json | ✅ | ✅ erp_metrics.json |
| derived_metrics.json | ✅ | ✅ erp_derived_metrics.json |
| segments.json | ✅ | ✅ erp_segments.json |
| schema.json | ✅ | ❌ 用 get_table_metadata 工具替代 |
| relationships.json | ✅ | ❌ 用 _search_cache + agent 探索替代 |
| query_templates.json | ✅ | ❌ 待补充 |

### 8.4 已知限制

1. **语义层和探索的边界**: prompt 写"语义层优先"，但 agent 仍无法区分哪些指标在语义层中（需先 `load_skill` 才能知道）
2. **派生指标手动翻译**: "净利润 = 有效订单收入 - 采购成本 - 头程运费..." 由 LLM 翻译为 SQL，未做程序自动展开
3. **skill_id 需要 agent 自行推断**: `business-{数据源名}` 中的 `数据源名` 不是变量，agent 需从上下文推理
4. **TABLESAMPLE 仅 PostgreSQL**: MySQL/ClickHouse 等不支持，fallback 到 LIMIT 可能仍然慢

## 六、当前缺陷（P0/P1/P2）

### P0 — 阻塞使用

| 问题 | 状态 | 详情 |
|------|------|------|
| ~~追问 400 错误~~ | ✅ 已修复 | MemorySaver 泄露消息 |
| ~~历史数据"暂无数据"~~ | ✅ 已修复 | finish_record 保证执行 |
| ~~图表不渲染等 finish~~ | ✅ 已修复 | ChartBlock isTyping 移除 |
| ~~chart-only edit SSE 空流~~ | ✅ 已修复 | queue 架构消除竞态 |
| 数据分析按钮走旧管线，结果不渲染 | ⚠️ 待迁移 | 旧 SSE 格式 vs 新 Agent 格式 |
| 数据预测按钮走旧管线，且 predict tool 不存在 | ⚠️ 先隐藏按钮 | 缺少 `predict_data` 工具，无法迁移 |
| 执行详情按钮展示旧 Pipeline 日志 | ⚠️ 待迁移 | Agent 不写 chat_log 表，需改为读取 tool_calls_log |

### P1 — 影响体验（替换阶段处理）

| 问题 | 阶段 | 详情 |
|------|------|------|
| 推荐追问默认仍用 pipeline | 替换 | 改默认参数即可，一行 |
| 坐标轴标签硬编码关闭/随机隐藏 | 优化 | Column.ts:86 `title: false` + `labelAutoHide` |
| 图表类型仅 5 种（无堆叠/面积/散点） | 优化 | template.yaml 写死 + 前端 5 个 class |
| Chart LLM 调用冗余（每次多 2-3s） | 优化 | 前端可自动选图表，不需要 LLM 生成 JSON |
| 左侧历史标题日期格式残留 | ✅ 已修复 | SQL 补丁已执行 |
| 代理思考文本在"思考中"折叠 | ✅ 已修复 | 主回答区直接展示 |

### P2 — 功能增强

| 问题 | 状态 | 详情 |
|------|------|------|
| 图表颜色/主题单一 | 待优化 | G2 默认色板，各 chart class 硬编码 |
| 数据预测功能 | 待开发 | 需新增 predict tool |
| 术语表集成 | 待开发 | terminology 表未接入 agent |
| 语义层 JSON 机械展开 | 待开发 | 派生指标公式需手动翻译，应程序自动展开 |

## 七、实施优先级（先替换，后优化）

### P0/P1/P2 → Step 对照

| 缺陷 | 等级 | → Step | 说明 |
|------|------|--------|------|
| 数据分析按钮走旧管线 | P0 | Step 2 | 替换 |
| 数据预测按钮不可用 | P0 | Step 2.5 | 先隐藏，等 Step 10 |
| 执行详情展示旧日志 | P0 | Step 3 | 替换 |
| AgentProfile 体系缺失 | **地基** | Step 1 | 所有迁移的前置依赖 |
| 推荐追问默认 pipeline | P1 | Step 4 | 替换 |
| Chart LLM 冗余 | P1 | Step 6 | 优化 |
| 坐标轴标签不显示 | P1 | Step 7 | 优化 |
| 图表类型仅 5 种 | P1 | Step 8 | 优化 |
| 图表颜色/主题单一 | P2 | Step 9 | 优化 |
| 数据预测功能 | P2 | Step 10 | 增强 |
| 语义层 JSON 机械展开 | P2 | Step 11 | 增强 |
| 术语表集成 | P2 | Step 12 | 增强 |

---

### 阶段 1：替换 — 旧管线全部切到 Agent（本周）

```
Step 1 ─ AgentProfile 体系
         │  创建 agent/profiles/{qa,analysis}.py
         │  AgentEngine.dispatch(profile, ...) 统一入口
         │  不建 predict profile（tool 不存在）
         │
Step 2 ─ 数据分析按钮 → Agent
         │  依赖 Step 1
         │  POST /chat/record/{id}/analysis → dispatch(profile="analysis")
         │  前端 AnalysisAnswer.vue → 改为监听 text-delta + finish
         │
Step 2.5 ─ 预测按钮 → 前端隐藏
         │  不依赖 Step 1，直接改前端
         │  predict tool 开发完成后再放回
         │
Step 3 ─ 执行详情 → tool_calls_log
         │  不依赖 Step 1/2
         │  前端 ExecutionDetails.vue → 读 tool_calls_log 替代 chat_log
         │
Step 4 ─ 推荐追问默认值
         │  一行修改，无依赖
         │  recommend_questions 默认 engine="agent"
         │
Step 5 ─ 旧代码删除
          依赖 Step 2 灰度验证通过
          删除 llm.py: run_analysis_or_predict_task_async 及相关方法
          删除 templates/generate_analysis/ + generate_predict/
```

**替换阶段完成标志**：分析按钮走 Agent，执行详情展示 Agent 日志，旧管线代码物理删除。

---

### 阶段 2：优化 — 图表架构 + 体验（下周起）

```
Step 6 ─ Chart LLM 裁剪
         │  设计前端指纹识别方案（参考 Metabase）
         │  削除 executor.py _generate_chart 中的 LLM 调用
         │  前端根据数据特征自动选图表类型
         │
Step 7 ─ 坐标轴标签修复
         │  Column.ts/Bar.ts/Line.ts: title: false → 使用字段名
         │  labelAutoHide: 从 hide 改为 overlap
         │
Step 8 ─ 图表类型扩充
         │  堆叠柱状图、面积图、散点图
         │  前端新增 3 个 chart class
         │
Step 9 ─ 图表颜色/主题
         │  BaseG2Chart 统一色板配置
```

---

### 阶段 3：增强 — 新功能 + 语义层（两周后）

```
Step 10 ─ predict_data 工具开发
          │  新增 agent/profiles/predict.py
          │  前端 PredictAnswer.vue 适配 agent SSE 格式
          │  恢复"数据预测"按钮
          │
Step 11 ─ 语义层机械展开
          │  派生指标公式自动展开（不再依赖 LLM 翻译）
          │  语义层和探索的边界清晰化
          │
Step 12 ─ 术语表集成
          │  search_terminology 工具接入 Agent
```

---

### 依赖关系图

```
Step 1 (AgentProfile) ──→ Step 2 (分析迁移)
                              │
Step 2.5 (隐藏预测) ─── 独立  │
Step 3 (执行详情)  ─── 独立  │  这三项可并行
Step 4 (推荐追问)  ─── 独立  │
                              │
                          Step 5 (删旧代码)
                          
─── 阶段分割线：以上是替换，以下是优化 ───

Step 6 (Chart LLM)  ──→ Step 7 (轴标签) ──→ Step 8 (图表类型) ──→ Step 9 (主题)

Step 10 (predict tool) ──→ Step 11 (语义层) ──→ Step 12 (术语表)
```

---

> **决策记录**: 
> - 数据分析：AgentProfile 实现后立即迁移。
> - 数据预测：先隐藏按钮，待 predict tool 开发完成后再放回。
> - Chart LLM：不急于删除——先完成管线替换，确保功能可用后再做架构裁剪。
> - 旧 Pipeline 代码：分析迁移验证通过后删除，不再保留双引擎。
> **下次评审**: 不晚于 2026-07-21
