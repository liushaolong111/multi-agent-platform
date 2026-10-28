"""Text-to-SQL 单元测试：SQL 校验、提取、只读执行防护。

完全 hermetic：不依赖真实数据库与 LLM。
执行路径：
    .venv/Scripts/python.exe -m pytest tests/test_text2sql.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_validate_readonly_sql_allows_select():
    from app.repository.business import validate_readonly_sql

    assert validate_readonly_sql("SELECT * FROM cost_records") is None
    assert validate_readonly_sql("  select cost_category, sum(amount) from cost_records group by cost_category  ") is None
    assert validate_readonly_sql("WITH t AS (SELECT 1) SELECT * FROM t") is None
    # 末尾分号允许
    assert validate_readonly_sql("SELECT 1;") is None


def test_validate_readonly_sql_blocks_writes():
    from app.repository.business import validate_readonly_sql

    assert validate_readonly_sql("DELETE FROM cost_records") is not None
    assert validate_readonly_sql("UPDATE cost_records SET amount = 0") is not None
    assert validate_readonly_sql("DROP TABLE cost_records") is not None
    assert validate_readonly_sql("INSERT INTO cost_records VALUES (1)") is not None
    # SELECT 里藏写操作也拦
    assert validate_readonly_sql("SELECT * FROM x; DROP TABLE y") is not None


def test_validate_readonly_sql_blocks_multi_statement():
    from app.repository.business import validate_readonly_sql

    assert validate_readonly_sql("SELECT 1; SELECT 2") is not None
    assert validate_readonly_sql("") is not None


def test_extract_sql_strips_markdown_and_prose():
    from app.agent.nodes.text2sql import _extract_sql

    assert _extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert _extract_sql("好的，查询如下：\nSELECT project_name FROM projects") == \
        "SELECT project_name FROM projects"
    assert _extract_sql("WITH a AS (SELECT 1) SELECT * FROM a;") == \
        "WITH a AS (SELECT 1) SELECT * FROM a"


def test_execute_readonly_query_blocks_write_without_db():
    """即使业务库不可用，写操作也应被静态校验拦截在引擎之前。"""
    from app.repository.business import execute_readonly_query

    result = execute_readonly_query("DROP TABLE cost_records")
    assert "安全校验" in result or "不可用" in result


def test_execute_readonly_query_on_sqlite(tmp_path, monkeypatch):
    """用临时 SQLite 验证：SELECT 正常执行 + 无 LIMIT 自动追加 + 结果格式化。"""
    from sqlalchemy import create_engine, text

    import app.repository.business as biz

    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER, name TEXT)"))
        for i in range(150):
            conn.execute(text(f"INSERT INTO t VALUES ({i}, 'n{i}')"))
        conn.commit()

    monkeypatch.setattr(biz, "business_engine", engine)

    out = biz.execute_readonly_query("SELECT id, name FROM t ORDER BY id")
    assert "查询结果" in out
    assert "100 行" in out  # 自动 LIMIT 100，150 行被截断
    assert "n0" in out

    out2 = biz.execute_readonly_query("SELECT COUNT(*) FROM t")
    assert "150" in out2
