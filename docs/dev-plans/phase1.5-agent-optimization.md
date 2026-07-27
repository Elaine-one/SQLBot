# 阶段1.5 优化方案：分析/预测 Agent 增强

> 编写日期: 2026-07-15
> 状态: 待实施
> 依赖: 阶段1 分析/预测 Agent 迁移完成

---

## 一、优化目标

阶段1 的 Agent 只是"跑通"——能用，但不好用。当前问题：

| 问题 | 现状 | 优化方向 |
|------|------|---------|
| 数据全部塞进 Prompt | 最多 8000 字符原始数据 dump 进 System Prompt | 改为按需取数据，Prompt 里只放摘要 |
| 不能生成图表 | post_process="text_only"，纯文字 | 复用 QA 的 `create_chart` / `edit_chart` |
| 预测只看历史数据 | LLM 凭空推测 | 加 `search_web` 工具，搜索外部信息 |
| 思考过程不可见 | `reasoning_content` 没被捕获 | 发射 `reasoning` SSE 事件 |
| Prompt 质量一般 | 角色定义僵硬 | 重写，强调"工具优先"的工作方式 |
| 迭代次数太少 | max_iterations=5 | 分析 10 轮，预测 15 轮 |
| 前端事件不全 | 只监听 text-delta + finish | 前端已补全 reasoning/tool-call/tool-result/chart |

## 二、工具体系：注册一次，按名复用

### 2.1 原则

所有工具在 `register_all.py` 中全局注册一次。Agent Profile 通过 `tool_names` 字段按名选择，不是"复制"工具，而是"引用"。

```
ToolRegistry（全局单例）
  ├─ search_relevant_tables     ← QA 专用
  ├─ get_table_metadata         ← QA 专用
  ├─ get_table_sample_data      ← QA 专用
  ├─ get_field_values           ← QA / 分析 / 预测 共用
  ├─ get_data_summary           ← 分析 / 预测 专用（新增）
  ├─ get_data_preview           ← 分析 / 预测 专用（新增）
  ├─ create_sql_query           ← QA 专用
  ├─ edit_sql_query             ← QA 专用
  ├─ replace_sql_fragment       ← QA 专用
  ├─ create_chart               ← QA / 分析 / 预测 共用
  ├─ edit_chart                 ← QA / 分析 / 预测 共用
  ├─ execute_sql_query          ← QA 专用
  ├─ analyze_query_result       ← QA / 分析 / 预测 共用
  ├─ load_skill                 ← QA 专用
  ├─ ask_for_clarification      ← QA 专用
  └─ search_web                 ← 预测 专用（新增）
```

### 2.2 工具分配（profile.tool_names）

```python
# QA
tool_names = []  # 空 = 全部注册工具

# 分析
tool_names = [
    "get_data_summary",
    "get_data_preview",
    "analyze_query_result",
    "create_chart",
    "edit_chart",
]

# 预测
tool_names = [
    "get_data_summary",
    "get_data_preview",
    "analyze_query_result",
    "search_web",           # ← 预测独有
    "create_chart",
    "edit_chart",
]
```

### 2.3 新增工具详解

#### `get_data_summary(record_id, memory) -> dict`

```
输入:  record_id
输出:  {
  success: true,
  row_count: 120,
  fields: [
    {name: "sales_amount", type: "numeric", min: 100, max: 50000, avg: 3200, sum: 384000, nulls: 0},
    {name: "product_name", type: "varchar", distinct: 45, nulls: 0},
    {name: "order_date",  type: "date",    min: "2026-01-01", max: "2026-06-30", nulls: 0}
  ],
  chart_type: "column"
}
```

纯内存计算，不涉及 I/O。对数值字段计算 min/max/avg/sum，对分类字段计算 distinct count，对日期字段给 min/max。

#### `get_data_preview(record_id, offset, limit, memory) -> dict`

```
输入:  record_id, offset=0, limit=20
输出:  {
  success: true,
  rows: [{...}, ...],
  offset: 0,
  returned: 15,
  total: 120,
  has_more: true
}
```

纯内存操作，从已加载的 QueryRecord.data 中切片，不涉及 DB 查询。

#### `search_web(query, max_results=5, memory=None) -> dict`

```
输入:  query="2026年跨境电商市场趋势", max_results=5
输出:  {
  success: true,
  results: [
    {title: "...", snippet: "...", url: "...", date: "..."},
    ...
  ],
  search_time: "0.8s"
}
```

**异步与超时控制（关键）**：

- 使用 `httpx.AsyncClient` 或 `aiohttp`（异步 HTTP，不阻塞事件循环）
- 单次请求超时: **5 秒**（`httpx.Timeout(5.0, connect=3.0)`）
- 连接池复用: 全局单例 `httpx.AsyncClient`，进程启动时创建，避免每次搜索重新建连
- 结果缓存: `session` 级别的 LRU 缓存（同一 query 5 分钟内不重复请求外部搜索引擎）
- 搜索失败不阻塞: 超时或网络错误返回空结果 + 错误描述，Agent 可以用现有数据继续预测
- 搜索引擎: 优先使用内部 API（如果 SQLBot 有配），fallback 到外网搜索

```python
# 实现骨架
import httpx
import asyncio
from functools import lru_cache
import time

_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=3.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client

async def search_web(query: str, max_results: int = 5, memory=None) -> dict:
    t0 = time.monotonic()
    try:
        client = _get_client()
        # ... 调用搜索 API ...
        elapsed = time.monotonic() - t0
        return {"success": True, "results": [...], "search_time": f"{elapsed:.1f}s"}
    except httpx.TimeoutException:
        return {"success": False, "error": "搜索超时(5s)，请用现有数据继续预测"}
    except Exception as e:
        return {"success": False, "error": f"搜索失败: {str(e)[:100]}"}
```

## 三、Profile 配置更新

```python
# engine.py

def build_analysis_profile(base_record) -> AgentProfile:
    prompt = _build_analysis_or_predict_prompt(base_record, mode="analysis")
    return AgentProfile(
        name="analysis",
        system_prompt=prompt,
        tool_names=[
            "get_data_summary",
            "get_data_preview",
            "analyze_query_result",
            "create_chart",
            "edit_chart",
        ],
        terminal_tools=[],
        max_iterations=10,       # 5 → 10
        post_process="text_and_chart",  # text_only → text_and_chart
    )


def build_predict_profile(base_record) -> AgentProfile:
    prompt = _build_analysis_or_predict_prompt(base_record, mode="predict")
    return AgentProfile(
        name="predict",
        system_prompt=prompt,
        tool_names=[
            "get_data_summary",
            "get_data_preview",
            "analyze_query_result",
            "search_web",        # ← 预测独有
            "create_chart",
            "edit_chart",
        ],
        terminal_tools=[],
        max_iterations=15,       # 5 → 15
        post_process="text_and_chart",
    )
```

## 四、Prompt 重写

### 4.1 分析 Agent Prompt

```
# 角色
你是 SQLBot 数据分析师。用户的查询结果已经就绪，你需要进行专业的数据解读。

# 数据概览
字段: {fields_summary}
行数: {row_count}
图表类型: {chart_type}

# 工作方式
你不是一次性看完所有数据再输出，而是像分析师一样逐步探索。每一步只做一件事：

1. 先用 `get_data_summary` 查看字段的统计特征（分布、范围、聚合值）
2. 感兴趣时用 `get_data_preview` 翻阅具体的行数据（每次 20 行）
3. 需要深挖某个维度时用 `analyze_query_result` 做 AI 深度分析
4. 有值得可视化的发现时用 `create_chart` 创建图表

重要：不要在一个工具调用后输出全部结论。先看数据再说话。
每调用一次工具后，基于工具结果输出这一轮的分析，然后决定是否继续探索。

# 分析框架
- **概览层**：数据量和分布特征
- **深入层**：明显的规律、趋势、异常点（解释判断依据）
- **洞察层**：用业务语言解读数据背后的含义
- **行动层**：给出可执行的业务建议

# 输出规范
- Markdown 格式，层次清晰
- 关键数字用**粗体**
- 发现异常时说明判断依据
- 主动用 `create_chart` 展示重要发现（趋势对比、占比分布等）
```

### 4.2 预测 Agent Prompt

```
# 角色
你是 SQLBot 数据预测师。用户的查询结果包含历史数据，你需要基于数据 + 外部信息预测未来趋势。

# 数据概览
字段: {fields_summary}
行数: {row_count}

# 工作方式
每一步只做一件事，基于工具结果逐步推进：

1. `get_data_summary` → 了解数据的统计特征和分布
2. `get_data_preview` → 查看具体数据模式
3. `analyze_query_result` → 对关键维度做深度分析
4. `search_web` → ⭐ 搜索外部信息辅助预测
   - 搜索策略：行业趋势、市场报告、近期新闻
   - 示例查询："{数据相关行业}2026年趋势"、"最新{品类}市场报告"
   - 外部信息用于校准预测方向，不替代数据本身
   - 搜索失败时继续用现有数据预测，不阻塞
5. `create_chart` → 可视化预测结果

# 预测方法论
1. **历史模式识别**：用工具识别数据的周期、趋势线、季节效应
2. **外部校准**：用 `search_web` 找行业趋势或市场数据来调整预测
3. **给出预测**：给出数值范围（非单点），标注假设前提
4. **风险评估**：说明预测的不确定因素和置信度

# 输出规范
- 区分"历史模式分析"和"未来趋势预测"两个部分
- 引用搜索来源时标注 URL 或标题
- 预测结果: 给出 乐观/基准/悲观 三种场景
- 主动用 `create_chart` 展示预测趋势线
```

## 五、后端修改

### 5.1 `advanced_tools.py` — 新增 3 个工具

```
+ get_data_summary(record_id, memory)     — 纯内存，无 IO
+ get_data_preview(record_id, offset, limit, memory) — 纯内存，无 IO
+ search_web(query, max_results, memory)  — 异步 HTTP，超时 5s，有缓存
```

### 5.2 `register_all.py` — 注册新工具

在 `_TOOL_DEFS` 列表中追加 3 条定义，`_lazy_import_tools` 中追加导入。

### 5.3 `engine.py` — Profile 配置 + Prompt 重写

- `build_analysis_profile`: tool_names 更新, max_iterations=10, post_process="text_and_chart"
- `build_predict_profile`: tool_names 更新（含 search_web）, max_iterations=15, post_process="text_and_chart"
- `_build_analysis_or_predict_prompt`: 不再嵌入原始数据，只放字段摘要

### 5.4 `executor.py` — reasoning 捕获 + post_process 新分支

- `_run_agent`: 捕获 `msg.additional_kwargs["reasoning_content"]` → 发射 `reasoning` 事件
- `_post_process`: 新增 `text_and_chart` 分支

```python
# _run_agent 中（L229 附近）
if isinstance(msg, AIMessage):
    # 发射思考过程
    reasoning = getattr(msg, "additional_kwargs", {}).get("reasoning_content", "")
    if reasoning:
        self._emit("reasoning", content=reasoning)

    # 发射正文
    content = msg.content
    if isinstance(content, str) and content.strip():
        self._emit("text-delta", content=content)
        self._text_output += content
```

```python
# _post_process 中
if self.profile.post_process == "text_and_chart":
    self._save_text_answer()
    if self.memory.charts:
        for chart_record in self.memory.charts.values():
            query = self.memory.queries.get(chart_record.record_id)
            if query and query.data:
                await self._emit_chart_from_memory(query, chart_record)
                break  # 只发第一个图表
    return
```

### 5.5 需要新增的 AgentProfile 字段

```python
@dataclass
class AgentProfile:
    name: str
    system_prompt: str
    tool_names: list[str] = field(default_factory=list)
    terminal_tools: list[str] = field(default_factory=list)
    max_iterations: int = 50
    post_process: str = "execute_and_chart"  # 新增 "text_and_chart"
```

## 六、前端（已完成）

AnalysisAnswer.vue 和 PredictAnswer.vue 已由 linter 更新，补充了完整的事件处理：

| 事件 | AnalysisAnswer.vue | PredictAnswer.vue |
|------|:--:|:--:|
| `id` | ✅ | ✅ |
| `text-delta` | ✅ | ✅ |
| `reasoning` | ✅ | ✅ |
| `tool-call` | ✅ (占位) | ✅ (占位) |
| `tool-result` | ✅ (占位) | ✅ (占位) |
| `chart` | — | ✅ (PredictAnswer 已引入 ChartBlock) |
| `finish` | ✅ | ✅ |
| `error` | ✅ | ✅ |

AnalysisAnswer.vue 引入 ChartBlock 同理即可支持分析图表展示。

## 七、实施检查清单

- [ ] `advanced_tools.py`: 新增 `get_data_summary`, `get_data_preview`, `search_web`
- [ ] `register_all.py`: 注册 3 个新工具
- [ ] `engine.py`: Profile 配置更新 + Prompt 重写 + post_process 改 `text_and_chart`
- [ ] `executor.py`: reasoning 捕获 + `text_and_chart` 后处理分支
- [ ] 验证: 分析按钮 → 文本流式 + 可选图表 + 思考过程可见
- [ ] 验证: 预测按钮 → 文本流式 + 搜索进度 + 可选图表
- [ ] 验证: QA 行为不受影响
