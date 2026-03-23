from __future__ import annotations
import logging
import re
from dataclasses import dataclass

import psycopg2

from sql_reviewer.sql_extractor import ExtractedQuery

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT_MS = 5000

# Heuristics: (set of substrings to match, dummy_value)
_PARAM_HEURISTICS = [
    ({"id", "count", "num"}, "1"),
    ({"date", "time", "created", "updated"}, "'2024-01-01'"),
    ({"is_", "has_", "active", "enabled"}, "true"),
]
_DEFAULT_DUMMY = "'placeholder'"


def _dummy_value(param_name: str) -> str:
    name = param_name.lower()
    for keywords, value in _PARAM_HEURISTICS:
        if any(kw in name for kw in keywords):
            return value
    return _DEFAULT_DUMMY


def _context_dummy_value(sql: str, match_start: int) -> str:
    """
    Look at the SQL text preceding a positional placeholder to find the column
    name, then apply the same heuristics used for named params.

    Scans backward from match_start for a pattern like `column_name =` or
    `column_name >` etc.  Falls back to _DEFAULT_DUMMY if no identifier found.
    """
    preceding = sql[:match_start]
    # Find the last identifier immediately before the placeholder, allowing for
    # an optional comparison operator and whitespace between identifier and operator.
    # Pattern: <identifier> <optional_whitespace> <operator> <whitespace>
    m = re.search(r'([a-zA-Z_]\w*)\s*(?:[=<>!]+|LIKE|ILIKE|IN|NOT)\s*$', preceding, re.IGNORECASE)
    if m:
        return _dummy_value(m.group(1))
    return _DEFAULT_DUMMY


def substitute_params(sql: str) -> str:
    """Replace parameter placeholders with type-appropriate dummy values."""
    # PostgreSQL positional: $1, $2, ... — look at preceding context for heuristic
    def replace_positional(m: re.Match) -> str:
        return _context_dummy_value(sql, m.start())

    sql = re.sub(r"\$\d+", replace_positional, sql)

    # SQLAlchemy named: :param_name
    def replace_named(m: re.Match) -> str:
        return _dummy_value(m.group(1))

    sql = re.sub(r":([a-zA-Z_]\w*)", replace_named, sql)

    # psycopg2 named: %(name)s
    def replace_psycopg2_named(m: re.Match) -> str:
        return _dummy_value(m.group(1))

    sql = re.sub(r"%\(([^)]+)\)s", replace_psycopg2_named, sql)

    # psycopg2 positional: %s
    sql = sql.replace("%s", _DEFAULT_DUMMY)

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
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
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
                    query.filename, query.line_number, e,
                )
                conn.rollback()
    finally:
        conn.close()

    return results
