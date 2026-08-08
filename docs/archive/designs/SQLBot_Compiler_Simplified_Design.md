# SQLBot 编译器简化方案

## 核心原则

**编译器只做一件事：表名 → 子查询替换。不做列名映射。**

列名映射不需要——因为 `get_table_metadata` 返回的 `fields[].name` 已经是 SQL 中的实际列名：

| 模式 | `name` 来源 | 示例 | SQL 中合法？ |
|------|-----------|------|-------------|
| 独立 SQLBot | 物理列名 | `destination_country` | ✅ |
| DataEase + 场景B（SQL数据集） | `originName` | `destination_country` | ✅ |
| DataEase + 场景C-F（复杂数据集） | `rebuildTable` 设置 | `f_ax_0` | ✅ |

## 架构对比

### 当前（复杂，不稳定）

```
LLM SQL
  │
  ├─ sqlglot 解析 LLM SQL ────→ 可能失败（反引号、中文冒号）
  ├─ sqlglot 解析底层 SQL ────→ 可能失败（分号、复杂结构）
  ├─ 列名映射 (comment→name) ──→ 复杂、歧义
  ├─ regex fallback ─────────→ 别名泄漏、括号不匹配
  └─ → 编译后 SQL
```

### 改造后（简单，确定性的）

```
LLM SQL
  │
  └─ 字符串替换: "数据集名" → (底层SQL) AS ds_N
     │
     └─ → 编译后 SQL

无 sqlglot 解析、无列名映射、无 fallback 链。
```

## 编译器改动

### `compiler.py` — 删掉的

- `DatasetMapping.column_map`
- `DatasetMapping.physical_columns`
- `DatasetMapping.resolve_column()`
- `_transform()` — 整个 sqlglot 路径
- `_transform_fallback()` — 整个 regex fallback
- `_replace_single_table()` — 不再需要
- `compile_field_values_query()` — 不再需要列名解析
- `_normalize_identifier()` — 不再需要
- 所有 `import sqlglot`

### `compiler.py` — 保留的

- `DatasetMapping`: 只保留 `sql` + `alias`
- `compile()`: 调用 `_do_replace`
- `compile_sample_query()`: `SELECT * FROM (sql) AS ds_N LIMIT 3`
- `_build_mappings()`: 构建 `{表名 → DatasetMapping}`，strip `;`
- `is_dataset()`: 不变

### `compiler.py` — 新增的

`_do_replace(sql)`: 遍历 `self.mappings`，对每个数据集做字符串替换：

```python
def _do_replace(self, sql: str) -> str:
    """Replace dataset names with subqueries — pure string replacement."""
    for table_name, mapping in self.mappings.items():
        subq = f"({mapping.sql}) AS {mapping.alias}"
        # Try each quote style.  replace() is deterministic:
        # it either finds the quoted name and replaces it, or doesn't.
        for q in ('"', "'", "`"):
            quoted = f"{q}{table_name}{q}"
            if quoted in sql:
                sql = sql.replace(quoted, subq)
                break
        else:
            # Unquoted — replace after FROM/JOIN keywords only.
            sql = re.sub(
                rf'\b(FROM|JOIN)\s+{re.escape(table_name)}\b',
                rf'\1 {subq}',
                sql,
                flags=re.IGNORECASE,
            )
    return sql
```

### Schema Tools — `get_table_metadata`

当 `is_dataset=True` 时，hint 改为：

```
"字段的 name 是 SQL 中的实际列名，请用它来写 SELECT、WHERE、GROUP BY。"
"comment 是业务说明，如果需要美化输出，用 AS 别名映射。"
```

System prompt 中已有的那行保持：
```
"用 get_table_metadata 返回的字段 name 作为列名写入 SQL"
```

### Query Tools — `get_field_values`

`compile_field_values_query` 退化为简单拼接——不需要列名解析。调用方已经知道要用 `name`。

## 改变的文件和行数

| 文件 | 改动 | 估计行数 |
|------|------|---------|
| `compiler.py` | 删 ~120 行，简化为 ~80 行 | 净减少 ~40 行 |
| `schema_tools.py` | 改 hint 文案 | 2 行 |
| `query_tools.py` | 简化 `get_field_values` | 删 ~15 行 |
| `graph.py` | 不改（已有正确 hint） | 0 |
| `sql_tools.py` | 不改（已正确） | 0 |

## 风险

| 风险 | 评估 |
|------|------|
| 数据集名在 SQL 字符串字面量中 | 极低——中文数据集名几乎不可能出现在字符串字面量里 |
| LLM 用未引号的中文名写 SQL | `else` 分支的 regex fallback 处理 |
| 数据集名有特殊正则字符 | `re.escape()` 处理 |
| 底层 SQL 带 `;` | `_build_mappings` 中已 strip |

## 兼容性

| 模式 | `mappings` | `_do_replace` 行为 |
|------|-----------|-------------------|
| 独立 SQLBot | `{}` | 不循环，返回原 SQL |
| DataEase + 物理表 | 不包含物理表名 | SQL 中的物理表名保持不变 |
| DataEase + 数据集 | 包含数据集名 | 替换为子查询 |
