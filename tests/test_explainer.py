"""
Integration tests — require a real Postgres instance.
Set DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review before running.
"""
import os
import pytest
import psycopg2
from sql_reviewer.diff_parser import ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import explain_queries, substitute_params, ExplainResult

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:test@localhost:5432/sql_review")


@pytest.fixture(scope="module")
def db_conn():
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        yield conn
        conn.close()
    except Exception:
        pytest.skip("Postgres not available — set DATABASE_URL to run integration tests")


@pytest.fixture(scope="module", autouse=True)
def create_test_table(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS test_users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    yield
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_users")


def make_query(sql: str, line: int = 1) -> ExtractedQuery:
    return ExtractedQuery(
        sql=sql,
        filename="src/app.py",
        line_number=line,
        diff_position=line,
        source="raw",
    )


# --- Unit tests for parameter substitution (no DB needed) ---

def test_substitute_positional_params():
    sql = "SELECT * FROM users WHERE id = $1 AND created_at > $2"
    result = substitute_params(sql)
    assert "$1" not in result
    assert "$2" not in result
    assert "1" in result  # id heuristic
    assert "2024-01-01" in result  # created_at heuristic


def test_substitute_named_params():
    sql = "SELECT * FROM users WHERE is_active = :is_active AND user_id = :user_id"
    result = substitute_params(sql)
    assert ":is_active" not in result
    assert ":user_id" not in result


def test_substitute_psycopg2_style():
    sql = "SELECT * FROM t WHERE x = %s AND y = %(name)s"
    result = substitute_params(sql)
    assert "%s" not in result
    assert "%(name)s" not in result


def test_substitute_default_placeholder():
    sql = "SELECT * FROM users WHERE email = $1"
    result = substitute_params(sql)
    assert "$1" not in result


# --- Integration tests (require Postgres) ---

def test_explain_simple_select(db_conn):
    query = make_query("SELECT * FROM test_users WHERE active = true")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert isinstance(results[0], ExplainResult)
    assert "Seq Scan" in results[0].plan_text or "Index" in results[0].plan_text
    assert results[0].query is query


def test_explain_skips_invalid_sql(db_conn):
    queries = [
        make_query("SELECT * FROM test_users WHERE active = true", line=1),
        make_query("this is not sql at all", line=2),
    ]
    results = explain_queries(queries, DB_URL)
    assert len(results) == 1  # invalid query skipped
    assert results[0].query.line_number == 1


def test_explain_with_parameterized_query(db_conn):
    query = make_query("SELECT * FROM test_users WHERE id = $1 AND active = $2")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert results[0].plan_text  # got a plan back


def test_explain_write_query_does_not_persist(db_conn):
    query = make_query("INSERT INTO test_users (name, active) VALUES ('test', true)")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1  # INSERT EXPLAIN ANALYZE works
    # Verify the row was NOT actually inserted (transaction rolled back)
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM test_users WHERE name = 'test'")
        count = cur.fetchone()[0]
    assert count == 0
