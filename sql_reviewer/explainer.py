from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import psycopg2
import sqlglot

from sql_reviewer.sql_extractor import ExtractedQuery

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT_MS = 5000

# Named-param heuristics: match substrings of the param name to a type-appropriate dummy.
# Realistic types produce more informative EXPLAIN plans (e.g. integer 1 triggers index scans).
_PARAM_HEURISTICS = [
    ({"id", "count", "num"}, "1"),
    ({"date", "time", "created", "updated"}, "'2024-01-01'"),
    ({"is_", "has_", "active", "enabled"}, "true"),
]


def _dummy_value(param_name: str) -> str:
    name = param_name.lower()
    for keywords, value in _PARAM_HEURISTICS:
        if any(kw in name for kw in keywords):
            return value
    return "'placeholder'"


def substitute_params(sql: str) -> str:
    """Replace parameter placeholders with type-appropriate dummy values."""
    # PostgreSQL positional: $1, $2, ... — use integer; type mismatches are caught
    # by the try/except in explain_queries and logged as a warning.
    sql = re.sub(r"\$\d+", "1", sql)

    # SQLAlchemy named: :param_name  (negative lookbehind skips ::cast syntax)
    sql = re.sub(r"(?<!:):([a-zA-Z_]\w*)", lambda m: _dummy_value(m.group(1)), sql)

    # psycopg2 named: %(name)s
    sql = re.sub(r"%\(([^)]+)\)s", lambda m: _dummy_value(m.group(1)), sql)

    # psycopg2 positional: %s
    sql = re.sub(r"(?<!['\w])%s(?!['\w])", "1", sql)

    return sql


@dataclass
class ExplainResult:
    query: ExtractedQuery
    plan_text: str


def explain_queries(queries: list[ExtractedQuery], database_url: str) -> list[ExplainResult]:
    results: list[ExplainResult] = []

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
    except Exception as e:
        logger.error("Could not connect to database: %s", e)
        raise

    try:
        for query in queries:
            sql = substitute_params(query.sql)
            try:
                with conn.cursor() as cur:
                    cur.execute("SET LOCAL statement_timeout = %s", (STATEMENT_TIMEOUT_MS,))
                    try:
                        statements = sqlglot.parse(sql)
                        if statements and isinstance(
                            statements[0],
                            (
                                sqlglot.expressions.Create,
                                sqlglot.expressions.Drop,
                                sqlglot.expressions.Alter,
                                sqlglot.expressions.TruncateTable,
                            ),
                        ):
                            logger.warning(
                                "Skipping DDL statement for EXPLAIN ANALYZE in %s line %d",
                                query.filename,
                                query.line_number,
                            )
                            continue
                    except sqlglot.errors.ParseError:
                        pass  # let it through; EXPLAIN will catch syntax errors
                    # psycopg2 has no way to parameterize EXPLAIN ANALYZE;
                    # `sql` is already a substituted literal string, not user input.
                    cur.execute(f"EXPLAIN ANALYZE {sql}")  # noqa: S608
                    plan_rows = cur.fetchall()
                    plan_text = "\n".join(row[0] for row in plan_rows)
                conn.rollback()
                results.append(ExplainResult(query=query, plan_text=plan_text))
            except Exception as e:
                logger.warning(
                    "EXPLAIN ANALYZE failed for query in %s line %d: %s",
                    query.filename,
                    query.line_number,
                    e,
                )
                conn.rollback()
    finally:
        conn.close()

    return results
