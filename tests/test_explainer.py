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


@pytest.fixture(scope="module")
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
    # Positional params ($N) all become "1" — no context-based type inference.
    sql = "SELECT * FROM users WHERE id = $1 AND created_at > $2"
    result = substitute_params(sql)
    assert "$1" not in result
    assert "$2" not in result
    assert result == "SELECT * FROM users WHERE id = 1 AND created_at > 1"


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


def test_substitute_cast_syntax_not_touched():
    """::cast syntax must not be treated as a named param."""
    sql = "SELECT val::int, ts::date FROM events WHERE id = $1"
    result = substitute_params(sql)
    assert "::int" in result
    assert "::date" in result


def test_substitute_named_heuristic_id():
    sql = "SELECT * FROM users WHERE user_id = :user_id AND account_id = :account_id"
    result = substitute_params(sql)
    assert "user_id" not in result.split("WHERE")[1].replace("user_id", "")  # params gone
    assert " 1" in result  # id heuristic → integer 1


def test_substitute_named_heuristic_date():
    sql = "SELECT * FROM events WHERE created_at > :created_at AND updated_at < :updated_at"
    result = substitute_params(sql)
    assert ":created_at" not in result
    assert ":updated_at" not in result
    assert "'2024-01-01'" in result


def test_substitute_named_heuristic_bool():
    sql = "SELECT * FROM users WHERE is_active = :is_active AND has_verified = :has_verified"
    result = substitute_params(sql)
    assert ":is_active" not in result
    assert ":has_verified" not in result
    assert "true" in result


def test_substitute_named_heuristic_default():
    """Params with no matching heuristic get 'placeholder'."""
    sql = "SELECT * FROM users WHERE email = :email AND role = :role"
    result = substitute_params(sql)
    assert ":email" not in result
    assert ":role" not in result
    assert "'placeholder'" in result


def test_substitute_mixed_styles():
    """A query mixing $N, :named, %(name)s, and %s all get substituted."""
    sql = "SELECT * FROM t WHERE a = $1 AND b = :user_id AND c = %(name)s AND d = %s"
    result = substitute_params(sql)
    assert "$1" not in result
    assert ":user_id" not in result
    assert "%(name)s" not in result
    # %s replaced with 1; verify no bare %s remains
    assert "%s" not in result


# --- Integration tests (require Postgres) ---

def test_explain_simple_select(db_conn, create_test_table):
    query = make_query("SELECT * FROM test_users WHERE active = true")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert isinstance(results[0], ExplainResult)
    assert "Seq Scan" in results[0].plan_text or "Index" in results[0].plan_text
    assert results[0].query is query


def test_explain_skips_invalid_sql(db_conn, create_test_table):
    queries = [
        make_query("SELECT * FROM test_users WHERE active = true", line=1),
        make_query("this is not sql at all", line=2),
    ]
    results = explain_queries(queries, DB_URL)
    assert len(results) == 1  # invalid query skipped
    assert results[0].query.line_number == 1


def test_explain_with_parameterized_query(db_conn, create_test_table):
    query = make_query("SELECT * FROM test_users WHERE id = $1 AND active = $2")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert results[0].plan_text  # got a plan back


def test_explain_write_query_does_not_persist(db_conn, create_test_table):
    query = make_query("INSERT INTO test_users (name, active) VALUES ('test', true)")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1  # INSERT EXPLAIN ANALYZE works
    # Verify the row was NOT actually inserted (transaction rolled back)
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM test_users WHERE name = 'test'")
        count = cur.fetchone()[0]
    assert count == 0
