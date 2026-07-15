"""
Unit tests for DatasetSQLCompiler — simplified version.

Tests the table-name → subquery replacement with no column mapping.
"""

# import pytest  # not available in test environment


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockField:
    def __init__(self, name, type="varchar", comment=None):
        self.name = name
        self.type = type
        self.comment = comment


class MockTable:
    def __init__(self, name, sql=None, fields=None, comment=None):
        self.name = name
        self.sql = sql
        self.fields = fields or []
        self.comment = comment


class MockDs:
    def __init__(self, tables=None):
        self.tables = tables or []


class MockMemory:
    def __init__(self, out_ds_instance=None, ds=None):
        self.out_ds_instance = out_ds_instance
        self.ds = ds
        self.explored_tables = {}


# ---------------------------------------------------------------------------
# Tests: DatasetSQLCompiler
# ---------------------------------------------------------------------------

class TestCompilerInit:
    """Test compiler initialization and mapping construction."""

    def test_standalone_mode_empty_mappings(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        memory = MockMemory(out_ds_instance=None)
        compiler = DatasetSQLCompiler(memory)
        assert compiler.mappings == {}

    def test_physical_tables_only(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("orders", sql=None, fields=[MockField("id")]),
            MockTable("products", sql=None, fields=[MockField("name")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        assert compiler.mappings == {}

    def test_mixed_physical_and_dataset(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("orders", sql=None, fields=[MockField("order_id")]),
            MockTable("数据集_销售", sql="SELECT a,b FROM sales",
                      fields=[MockField("a")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        assert len(compiler.mappings) == 1
        assert "数据集_销售" in compiler.mappings
        assert "orders" not in compiler.mappings

    def test_semicolon_stripped(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT x FROM t;  ",
                      fields=[MockField("x")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        m = compiler.mappings["ds1"]
        assert not m.sql.endswith(";")
        assert m.sql == "SELECT x FROM t"

    def test_alias_assignment(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("t1", sql="SELECT 1"),
            MockTable("t2", sql="SELECT 2"),
            MockTable("t3", sql=None),  # skipped
            MockTable("t4", sql="SELECT 4"),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        assert compiler.mappings["t1"].alias == "ds_0"
        assert compiler.mappings["t2"].alias == "ds_1"
        assert "t3" not in compiler.mappings
        assert compiler.mappings["t4"].alias == "ds_2"


class TestCompile:
    """Test the compile() pipeline — pure string replacement."""

    def test_empty_mappings_passthrough(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        memory = MockMemory(out_ds_instance=None)
        compiler = DatasetSQLCompiler(memory)
        sql = "SELECT * FROM orders WHERE id = 1"
        assert compiler.compile(sql) == sql

    def test_double_quoted_replacement(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("数据集_销售", sql="SELECT a,b FROM real_sales",
                      fields=[MockField("a")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile('SELECT a FROM "数据集_销售"')
        assert "(SELECT a,b FROM real_sales) AS ds_0" in result
        assert '"数据集_销售"' not in result

    def test_backtick_quoted_replacement(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT 1", fields=[]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile("SELECT * FROM `ds1`")
        assert "(SELECT 1) AS ds_0" in result
        assert "`ds1`" not in result

    def test_unquoted_replacement(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("orders_view", sql="SELECT x FROM orders",
                      fields=[MockField("x")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile("SELECT x FROM orders_view")
        assert "(SELECT x FROM orders) AS ds_0" in result
        assert "orders_view" not in result

    def test_multiple_datasets(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds_a", sql="SELECT 1 AS v", fields=[]),
            MockTable("ds_b", sql="SELECT 2 AS v", fields=[]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile('SELECT * FROM "ds_a" JOIN "ds_b" ON 1=1')
        # Both datasets should be replaced with subqueries
        assert "(SELECT 1 AS v) AS ds_0" in result
        assert "(SELECT 2 AS v) AS ds_1" in result
        # Original quoted names should be gone
        assert '"ds_a"' not in result
        assert '"ds_b"' not in result

    def test_physical_table_not_replaced(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT 1", fields=[]),
            MockTable("orders", sql=None, fields=[]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile('SELECT * FROM orders')
        assert result == 'SELECT * FROM orders'
        assert '(SELECT' not in result

    def test_table_name_in_alias_not_replaced(self):
        """Dataset name in SELECT alias should NOT be replaced — only FROM ref."""
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT 1", fields=[]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile('SELECT x AS "ds1" FROM "ds1"')
        # The FROM "ds1" should be replaced
        assert "(SELECT 1) AS ds_0" in result
        # The column alias "ds1" should NOT be replaced
        assert 'AS "ds1"' in result
        assert result.count("(SELECT 1) AS ds_0") == 1

    def test_chinese_name_with_spaces(self):
        """Chinese dataset name with spaces, quoted — only FROM ref replaced."""
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("数据集 2：商品销售明细", sql="SELECT a,b FROM sales",
                      fields=[MockField("a")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile(
            'SELECT a, b FROM "数据集 2：商品销售明细" WHERE a > 0'
        )
        assert "(SELECT a,b FROM sales) AS ds_0" in result
        assert '"数据集 2：商品销售明细"' not in result


class TestCompileShortcuts:
    """Test compile_sample_query."""

    def test_sample_query_dataset(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT x FROM t", fields=[MockField("x")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile_sample_query("ds1")
        assert result == "SELECT * FROM (SELECT x FROM t) AS ds_0 LIMIT 3"

    def test_sample_query_physical(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("orders", sql=None, fields=[MockField("id")]),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        result = compiler.compile_sample_query("orders")
        assert result is None


class TestIsDataset:
    """Test is_dataset check."""

    def test_dataset_true(self):
        from apps.chat.agent.compiler import DatasetSQLCompiler
        ds = MockDs(tables=[
            MockTable("ds1", sql="SELECT 1"),
            MockTable("orders", sql=None),
        ])
        memory = MockMemory(out_ds_instance="assistant", ds=ds)
        compiler = DatasetSQLCompiler(memory)
        assert compiler.is_dataset("ds1") is True
        assert compiler.is_dataset("orders") is False


# ---------------------------------------------------------------------------
# Tests: schema_tools helpers
# ---------------------------------------------------------------------------

class TestSchemaToolsHelpers:
    """Test _build_table_schema_assistant and _resolve_backing_sql."""

    def test_build_table_schema_dataset(self):
        from apps.chat.agent.tools.schema_tools import _build_table_schema_assistant
        table = MockTable("数据集1", sql="SELECT a FROM t",
                          fields=[MockField("a", comment="列A")],
                          comment="测试数据集")
        result = _build_table_schema_assistant(table)
        assert result["table_name"] == "数据集1"
        assert result["is_dataset"] is True
        assert result["sql"] == "SELECT a FROM t"
        assert result["comment"] == "测试数据集"
        assert len(result["fields"]) == 1

    def test_build_table_schema_physical(self):
        from apps.chat.agent.tools.schema_tools import _build_table_schema_assistant
        table = MockTable("orders", sql=None, fields=[MockField("id", type="int")])
        result = _build_table_schema_assistant(table)
        assert result["table_name"] == "orders"
        assert result["is_dataset"] is False
        assert "sql" not in result

    def test_resolve_backing_sql_cached(self):
        from apps.chat.agent.tools.schema_tools import _resolve_backing_sql
        memory = MockMemory()
        memory.explored_tables["ds1"] = {"sql": "SELECT 1", "is_dataset": True}
        result = _resolve_backing_sql("ds1", memory)
        assert result == "SELECT 1"

    def test_resolve_backing_sql_cold_cache(self):
        from apps.chat.agent.tools.schema_tools import _resolve_backing_sql
        ds = MockDs(tables=[MockTable("ds1", sql="SELECT x FROM t", fields=[])])
        memory = MockMemory(ds=ds)
        result = _resolve_backing_sql("ds1", memory)
        assert result == "SELECT x FROM t"

    def test_resolve_backing_sql_not_found(self):
        from apps.chat.agent.tools.schema_tools import _resolve_backing_sql
        ds = MockDs(tables=[])
        memory = MockMemory(ds=ds)
        result = _resolve_backing_sql("nonexistent", memory)
        assert result is None


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def run():
    """Run all tests manually."""
    passed = 0
    failed = 0
    import sys

    for cls in [TestCompilerInit, TestCompile, TestCompileShortcuts,
                TestIsDataset, TestSchemaToolsHelpers]:
        print(f"\n=== {cls.__name__} ===")
        instance = cls()
        for name in sorted(dir(instance)):
            if name.startswith("test_"):
                try:
                    getattr(instance, name)()
                    print(f"  PASS {name}")
                    passed += 1
                except Exception as e:
                    print(f"  FAIL {name}: {e}")
                    import traceback
                    traceback.print_exc()
                    failed += 1
    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
