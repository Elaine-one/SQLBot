# SQLBot DataEase 数据集编译层 — 实施方案

## 1. 问题

DataEase 嵌入模式下，Agent 把数据集（逻辑视图）当成物理表来处理：
- `search_relevant_tables` 返回的表名是中文数据集名（如 "数据集 1：订单全链路视图"）
- `get_table_metadata` 返回了字段列表，但丢弃了 `table.sql`（DataEase 建模层已提供的底层可执行 SQL）
- LLM 用中文数据集名写 SQL → `EXPLAIN` 失败 → 重试循环 → `GraphRecursionError`

## 2. 总体架构

```
LLM SQL（逻辑引用）:
  SELECT 目的国, SUM(订单总额cny)
  FROM "数据集 1：订单全链路视图"
  GROUP BY 目的国

         │  DatasetSQLCompiler
         ▼

可执行 SQL（物理引用）:
  SELECT ds_0."destination_country" AS "目的国",
         SUM(ds_0."order_total_cny") AS "订单总额cny"
  FROM (
    SELECT a.destination_country, a.order_total_cny
    FROM orders a LEFT JOIN customers b ON ...
  ) AS ds_0
  GROUP BY ds_0."destination_country"
```

编译时机：**校验时编译（EXPLAIN），存储时保留原始 SQL。**

## 3. 判别器

```python
# 两个条件 AND 关系
条件 A: _is_assistant_mode(memory)  →  memory.out_ds_instance is not None
条件 B: table.sql is not None        →  此表是 DataEase 数据集，不是物理表

条件 A = False → 独立 SQLBot 模式 → 编译器永久空转，零开销
条件 A = True AND 条件 B = False → 物理表 → 不编译，直接透传
条件 A = True AND 条件 B = True  → DataEase 数据集 → 编译
```

## 4. 新增文件

### 4.1 `apps/chat/agent/compiler.py`

核心类：

```python
@dataclass
class DatasetMapping:
    """单个数据集的映射信息"""
    sql: str                              # 底层可执行 SQL
    alias: str                            # 子查询别名，如 "ds_0"
    column_map: dict[str, str]            # display_name → physical_name
    physical_columns: set[str]            # 物理列名集合

class DatasetSQLCompiler:
    """LLM 逻辑 SQL → 可执行物理 SQL"""
    
    def __init__(self, memory: AgentMemory):
        self.mappings: dict[str, DatasetMapping] = {}
        if not _is_assistant_mode(memory):
            return  # 独立 SQLBot → 空表，编译永远透传
        self._build_mappings(memory)
    
    def _build_mappings(self, memory):
        """遍历 ds.tables，只对有 sql 的数据集构建映射"""
        alias_idx = 0
        for table in (memory.ds.tables or []):
            if not table.sql:
                continue  # 物理表 → 跳过
            
            # 构建列名映射: display_name → physical_name
            # display_name = field.comment（业务名）或 field.name（当comment为空时）
            column_map = {}
            physical_cols = set()
            for f in (table.fields or []):
                physical = f.name
                display = (f.comment or f.name)
                column_map[display] = physical
                physical_cols.add(physical)
            
            self.mappings[table.name] = DatasetMapping(
                sql=table.sql,
                alias=f"ds_{alias_idx}",
                column_map=column_map,
                physical_columns=physical_cols,
            )
            alias_idx += 1
    
    def compile(self, llm_sql: str) -> str:
        """编译 SQL：表引用→子查询，列名→物理列名
        
        如果 self.mappings 为空（独立 SQLBot），直接返回原 SQL。
        """
        if not self.mappings:
            return llm_sql
        
        return self._transform(llm_sql)
    
    def compile_sample_query(self, table_name: str) -> str | None:
        """为 get_table_sample_data 生成编译后的查询"""
        if mapping := self.mappings.get(table_name):
            return f"SELECT * FROM ({mapping.sql}) AS {mapping.alias} LIMIT 3"
        return None
    
    def compile_field_values_query(self, table_name: str, field_name: str, 
                                    limit: int, quote: str) -> str | None:
        """为 get_field_values 生成编译后的查询"""
        if mapping := self.mappings.get(table_name):
            physical_name = self._resolve_column(mapping, field_name)
            return (
                f"SELECT DISTINCT {quote}{physical_name}{quote} "
                f"FROM ({mapping.sql}) AS {mapping.alias} "
                f"WHERE {quote}{physical_name}{quote} IS NOT NULL "
                f"LIMIT {limit}"
            )
        return None
    
    def is_dataset(self, table_name: str) -> bool:
        return table_name in self.mappings
    
    def _resolve_column(self, mapping: DatasetMapping, llm_name: str) -> str:
        """将 LLM 写的列名映射为物理列名"""
        # 1. 精确匹配 display_name
        if llm_name in mapping.column_map:
            return mapping.column_map[llm_name]
        # 2. 已经是物理名 → 直接返回
        if llm_name in mapping.physical_columns:
            return llm_name
        # 3. 找不到 → 保持原样（后续 EXPLAIN 会报错，LLM 自我纠正）
        return llm_name
    
    def _transform(self, sql: str) -> str:
        """使用 sqlglot 做 AST 级别的转换"""
        import sqlglot
        from sqlglot import exp
        
        try:
            tree = sqlglot.parse_one(sql)
        except Exception:
            # sqlglot 解析失败 → 回退到简单字符串替换
            return self._transform_fallback(sql)
        
        # 1. 遍历 FROM/JOIN 子句，替换表引用
        for table_exp in tree.find_all(exp.Table):
            table_name = table_exp.name
            if mapping := self.mappings.get(table_name):
                # 保存别名
                table_exp.set("alias", exp.TableAlias(this=exp.Identifier(this=mapping.alias)))
                # 替换为子查询
                subquery = sqlglot.parse_one(f"({mapping.sql})")
                table_exp.replace(subquery)
        
        # 2. 遍历列引用，替换列名
        for col in tree.find_all(exp.Column):
            col_name = col.name
            table_ref = col.table  # 可能是 None、"数据集名"、或已编译的别名
            
            # 找到这个列属于哪个数据集
            for table_name, mapping in self.mappings.items():
                if table_ref is None or table_ref == table_name or table_ref == mapping.alias:
                    physical = self._resolve_column(mapping, col_name)
                    if physical != col_name:
                        col.set("name", exp.Identifier(this=physical))
                    # 如果没有表前缀，加上别名
                    if table_ref is None:
                        col.set("table", exp.Identifier(this=mapping.alias))
                    break
        
        return tree.sql()
    
    def _transform_fallback(self, sql: str) -> str:
        """当 sqlglot 解析失败时的简单字符串替换"""
        for table_name, mapping in self.mappings.items():
            # 替换表引用
            sql = sql.replace(
                f'"{table_name}"', f'({mapping.sql}) AS {mapping.alias}'
            )
            sql = sql.replace(
                f"'{table_name}'", f'({mapping.sql}) AS {mapping.alias}'
            )
            # 替换列名（带前缀）
            for display, physical in mapping.column_map.items():
                if display != physical:
                    sql = sql.replace(
                        f'{mapping.alias}."{display}"',
                        f'{mapping.alias}."{physical}"'
                    )
        return sql
```

## 5. 修改文件

### 5.1 `apps/chat/agent/tools/schema_tools.py`

#### 5.1a `_build_table_schema_assistant` — 返回 `is_dataset` 和 `sql`

```python
def _build_table_schema_assistant(table) -> dict:
    fields = [_build_field_dict_assistant(f) for f in (table.fields or [])]
    result: dict = {
        "table_name": table.name,
        "fields": fields,
    }
    if table.sql:
        result["is_dataset"] = True
        result["sql"] = (
            table.sql[:200] + "..."
            if len(table.sql) > 200
            else table.sql
        )
    else:
        result["is_dataset"] = False
    if table.rule:
        result["rule"] = table.rule
    if table.comment and table.comment != table.name:
        result["description"] = table.comment
    return result
```

#### 5.1b `_build_field_dict_assistant` — 明确区分 `name`（SQL列名）和 `display_name`（业务名）

```python
def _build_field_dict_assistant(field) -> dict:
    info: dict = {
        "name": field.name,        # SQL 列名
        "type": field.type or "unknown",
    }
    # display_name: LLM 在 SQL 中引用的业务名
    if field.comment and field.comment != field.name:
        info["display_name"] = field.comment
    if field.comment:
        info["description"] = field.comment
    return info
```

#### 5.1c `search_relevant_tables` — 标记 `is_dataset`

```python
# 在 assistant 模式下：
result = {
    "tables": [
        {
            "name": t.name,
            "comment": t.comment or "",
            "is_dataset": bool(t.sql),  # ← 新增
        }
        for t in tables
    ],
}
```

#### 5.1d `get_table_sample_data` — 数据集用子查询

```python
# 在 assistant 模式下：
if _is_assistant_mode(memory):
    for table in (memory.ds.tables or []):
        if table.name == table_name:
            from apps.chat.agent.compiler import DatasetSQLCompiler
            compiler = DatasetSQLCompiler(memory)
            if compiled := compiler.compile_sample_query(table_name):
                result = exec_sql(ds=ds, sql=compiled, origin_column=True)
            elif table.sql:
                # 有 sql 但编译器没返回（不应该发生，兜底）
                sql = f"SELECT * FROM ({table.sql}) AS _t LIMIT 3"
                result = exec_sql(ds=ds, sql=sql, origin_column=True)
            else:
                # 物理表
                sql = f"SELECT * FROM {q}{table_name}{q} LIMIT 3"
                result = exec_sql(ds=ds, sql=sql, origin_column=True)
            ...
```

### 5.2 `apps/chat/agent/tools/sql_tools.py`

#### 5.2a `create_sql_query` — 校验前编译

```python
async def create_sql_query(sql, memory):
    from apps.chat.agent.compiler import DatasetSQLCompiler
    
    # 1. 编译（如果是独立SQLBot，这步空转）
    compiler = DatasetSQLCompiler(memory)
    compiled = compiler.compile(sql)
    
    # 2. 语法校验（针对编译后的 SQL）
    valid, error = _validate_sql_syntax(compiled, memory.datasource_type or "")
    if not valid:
        memory.sql_retry_count += 1
        return {"success": False, "error": error, ...}
    
    # 3. 只读检查
    from apps.db.db import check_sql_read
    try:
        if not check_sql_read(compiled, memory.ds):
            return {"success": False, "error": "仅支持 SELECT / WITH 查询"}
    except ValueError as exc:
        return {"success": False, "error": str(exc)}
    
    # 4. 表存在性检查（针对原始 SQL 中的数据集名，编译后的子查询别名跳过）
    used_original = _extract_table_names(sql)
    unknown = [
        t for t in used_original
        if t not in memory.explored_tables and not compiler.is_dataset(t)
    ]
    if unknown:
        return {"success": False, "error": f"表 {unknown} 的字段结构尚未获取", ...}
    
    # 5. EXPLAIN 校验（针对编译后的 SQL）
    try:
        from apps.db.db import exec_sql
        exec_sql(ds=memory.ds, sql=f"EXPLAIN {compiled}")
    except Exception as exc:
        err_msg = str(exc).split("\\n")[0][:300]
        return {"success": False, "error": f"SQL 校验失败: {err_msg}", ...}
    
    # 6. 存储原始 SQL（LLM 可读），不是编译后的
    record_id = f"r_{_next_seq(memory)}"
    memory.queries[record_id] = QueryRecord(
        record_id=record_id,
        sql=sql,          # ← 原始 SQL
        tables_used=_extract_table_names(compiled),  # ← 编译后的表名（物理表）
        status="created",
    )
    memory.terminal_triggered = True
    return {"success": True, "record_id": record_id, "sql": sql, ...}
```

#### 5.2b `edit_sql_query` — 同理

```python
async def edit_sql_query(record_id, edits, memory):
    # ... 应用编辑（针对原始 SQL）...
    edited_sql = ...
    
    # 编译
    compiler = DatasetSQLCompiler(memory)
    compiled = compiler.compile(edited_sql)
    
    # EXPLAIN 校验
    exec_sql(ds=memory.ds, sql=f"EXPLAIN {compiled}")
    
    # 存储编辑后的原始 SQL
    query.sql = edited_sql
```

### 5.3 `apps/chat/agent/tools/query_tools.py`

#### 5.3a `execute_sql_query` — 执行前编译

```python
async def execute_sql_query(record_id, memory):
    query = memory.queries.get(record_id)
    if not query:
        return {"success": False, "error": f"查询 {record_id} 不存在", ...}
    
    from apps.chat.agent.compiler import DatasetSQLCompiler
    from apps.db.db import exec_sql
    
    compiler = DatasetSQLCompiler(memory)
    executable = compiler.compile(query.sql)  # 编译原始 SQL
    
    try:
        result = exec_sql(ds=memory.ds, sql=executable)
        query.data = result
        query.status = "executed"
        query.row_count = len(result.get("data", []))
        query.result_fields = list(result["fields"])
        return {"success": True, "record_id": record_id, "row_count": query.row_count}
    except Exception as exc:
        query.status = "failed"
        return {"success": False, "error": f"SQL 执行失败: {exc}"}
```

#### 5.3b `get_field_values` — 数据集时编译查询

```python
async def get_field_values(table_name, field_name, memory, limit=20):
    from apps.chat.agent.compiler import DatasetSQLCompiler
    
    ds_type = (memory.datasource_type or "").lower()
    quote = '"'  # ... 根据方言选择引号
    
    compiler = DatasetSQLCompiler(memory)
    if compiled := compiler.compile_field_values_query(
        table_name, field_name, limit, quote
    ):
        # 数据集 → 使用编译后的查询
        query = compiled
    else:
        # 物理表 → 使用原逻辑
        query = f"SELECT DISTINCT {quote}{field_name}{quote} FROM {quote}{table_name}{quote} ..."
    
    from apps.db.db import exec_sql
    result = exec_sql(ds=memory.ds, sql=query, origin_column=True)
    ...
```

### 5.4 `apps/chat/agent/tools/advanced_tools.py`

#### `replace_sql_fragment` — 同 `edit_sql_query`，编译后再校验

逻辑与 `edit_sql_query` 相同：应用替换 → 编译 → EXPLAIN → 存储原始 SQL。

### 5.5 `apps/chat/agent/graph.py`

在 system prompt 的"工具表"之前插入（仅 `_is_assistant_mode` 时）：

```python
if memory.out_ds_instance is not None:
    base += """
## DataEase 数据集

部分表标注了 is_dataset=true，它们是预定义的逻辑视图，底层有完整的 SQL 查询。
你不需关心底层 SQL 细节——像操作普通表一样写 SQL 即可，系统会自动处理转换。

规则：
- 用字段的 display_name（如果有）或 name 作为列名写入 SQL
- 用 get_table_metadata 返回的 table_name 作为 FROM 后的表名
- 不要尝试修改或"优化"数据集的内部逻辑
"""
```

## 6. 兼容性保证

### 6.1 独立 SQLBot（`_is_assistant_mode = False`）

```python
compiler = DatasetSQLCompiler(memory)
# compiler.mappings = {}  ← 空字典

compiler.compile(sql)        → 原样返回 sql
compiler.is_dataset(name)    → 永远 False
compiler.compile_sample_query(name)  → 永远 None
# → 所有代码路径等同于改动前
```

### 6.2 DataEase 嵌入 — 物理表（场景A）

```python
# buildTable 已把 table.name 换成物理表名 → table.sql = None
# → compiler.mappings 中没有此表
compiler.is_dataset("orders") → False
# → 走原逻辑，不触发编译
```

### 6.3 DataEase 嵌入 — 数据集（场景B-F）

```python
# table.sql 非空 → compiler.mappings 中有此表
# → 所有工具自动编译
```

### 6.4 旧 Pipeline 引擎

完全不受影响。Pipeline 走 `LLMService.run_task()` → `exec_sql()`，不经过 Agent 工具。

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| sqlglot 解析失败 | 回退到 `_transform_fallback()` 字符串替换 |
| 列名在映射中找不到 | 保持原列名，让 EXPLAIN 报 "column not found" → LLM 自我纠正 |
| 编译后的 SQL EXPLAIN 失败 | 返回 `success=False, error=...` 给 LLM，不消耗额外重试次数 |
| `memory.ds.tables` 为空 | `compiler.mappings = {}` → 空转 |
| DataEase 侧 `table.sql` 本身有语法错误 | EXPLAIN 会暴露，LLM 无法修复（这是 DataEase 建模层的问题） |

## 8. 测试要点

### 8.1 单元测试（`tests/test_compiler.py`）

```
test_compiler_empty_mappings_returns_original   # 独立SQLBot → 透传
test_compiler_table_replacement                  # FROM "数据集" → FROM (subquery) AS ds_0
test_compiler_column_mapping                     # display_name → physical_name
test_compiler_column_already_physical            # 已经是物理名 → 不变
test_compiler_missing_column                     # 找不到映射 → 保持原样
test_compiler_fallback_on_parse_error            # sqlglot 失败 → 字符串替换
test_compiler_multiple_datasets                  # 多个数据集 → 不同别名 ds_0, ds_1
test_compiler_physical_table_passthrough          # table.sql=None → 不编译
test_compiler_sql_with_cte                       # WITH 子句 → 正确编译
test_compiler_sql_with_join                      # JOIN → 正确编译
test_build_mappings_scenario_b                   # 场景B: originName 字段
test_build_mappings_scenario_c                   # 场景C: rebuild 后的 alias 字段
```

### 8.2 集成测试

```
test_agent_direct_db_mode_unchanged              # 独立SQLBot 回归测试
test_agent_dataset_simple_query                  # 场景B: SQL数据集简单查询
test_agent_dataset_complex_query                 # 场景C: 复杂数据集
test_agent_edit_query_on_dataset                 # 编辑数据集查询
test_agent_sample_data_on_dataset                # 数据集样本数据
test_agent_field_values_on_dataset               # 数据集字段值
```

## 9. 改动汇总

| 文件 | 改动类型 | 大约行数 |
|------|---------|---------|
| **新增** `apps/chat/agent/compiler.py` | 新文件 | ~180 行 |
| `apps/chat/agent/tools/schema_tools.py` | 修改 4 处 | +40 行 |
| `apps/chat/agent/tools/sql_tools.py` | 修改 2 处 | +25 行 |
| `apps/chat/agent/tools/query_tools.py` | 修改 2 处 | +20 行 |
| `apps/chat/agent/tools/advanced_tools.py` | 修改 1 处 | +10 行 |
| `apps/chat/agent/graph.py` | system prompt | +15 行 |
| **新增** `tests/test_compiler.py` | 新文件 | ~150 行 |
| DataEase Java 侧 | **不改** | 0 |
