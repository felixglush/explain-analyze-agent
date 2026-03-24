from __future__ import annotations
import ast
import json
import logging
from dataclasses import dataclass
from typing import Literal

import sqlglot

from sql_reviewer.diff_parser import ChangedFile

logger = logging.getLogger(__name__)

SQL_KEYWORDS = {"select", "insert", "update", "delete", "with", "merge"}


@dataclass
class ExtractedQuery:
    sql: str
    filename: str
    line_number: int
    diff_position: int | None  # None if no nearby changed line (ORM path)
    source: Literal["raw", "orm"]
    source_context: str = ""  # 5 lines before/after the query for Claude's prompt


def _is_valid_sql(text: str) -> bool:
    """Return True if sqlglot can parse the text as a SQL statement."""
    try:
        statements = sqlglot.parse(text, dialect="postgres")
        return bool(statements and statements[0] is not None)
    except Exception:
        return False


def _extract_sql_strings(source_code: str) -> list[tuple[int, str]]:
    """
    Walk the AST of Python source and return (line_number, sql_string) for
    string literals that look like SQL (contain a SQL keyword and parse cleanly).
    Also handles text("...") and execute("...") call patterns.
    """
    results: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return results

    parent_map = {
        child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)
    }

    for node in ast.walk(tree):
        # String literals
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Bug 2 fix: skip Constant nodes that are parts of f-strings
            if isinstance(parent_map.get(node), ast.JoinedStr):
                continue
            val = node.value.strip()
            # Bug 1 fix: use substring search instead of split+set-membership
            val_lower = val.lower()
            if not any(kw in val_lower for kw in SQL_KEYWORDS):
                continue
            if _is_valid_sql(val):
                results.append((node.lineno, val))

    return results


def _extract_raw_queries(changed_file: ChangedFile) -> list[ExtractedQuery]:
    changed_line_numbers = {cl.line_number for cl in changed_file.changed_lines}
    line_to_position = {
        cl.line_number: cl.diff_position for cl in changed_file.changed_lines
    }

    all_sql = _extract_sql_strings(changed_file.full_content)
    queries = []
    for line_num, sql in all_sql:
        if line_num not in changed_line_numbers:
            continue
        lines = changed_file.full_content.splitlines()
        start = max(0, line_num - 1 - 5)
        end = min(len(lines), line_num + 5)
        context = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start, end))
        queries.append(
            ExtractedQuery(
                sql=sql,
                filename=changed_file.filename,
                line_number=line_num,
                diff_position=line_to_position.get(line_num),
                source="raw",
                source_context=context,
            )
        )
    return queries


def _find_nearest_diff_position(
    line_number: int,
    changed_file: ChangedFile,
    window: int = 10,
) -> int | None:
    """Find the diff_position of the nearest changed line within `window` lines."""
    line_to_position = {
        cl.line_number: cl.diff_position for cl in changed_file.changed_lines
    }
    if line_number in line_to_position:
        return line_to_position[line_number]
    for delta in range(1, window + 1):
        if line_number - delta in line_to_position:
            return line_to_position[line_number - delta]
        if line_number + delta in line_to_position:
            return line_to_position[line_number + delta]
    return None


def _extract_orm_queries(
    changed_file: ChangedFile,
    anthropic_client,
) -> list[ExtractedQuery]:
    """Send changed SQLAlchemy code to Claude and get back inferred SQL."""
    if "sqlalchemy" not in changed_file.full_content.lower():
        return []

    changed_line_numbers = {cl.line_number for cl in changed_file.changed_lines}
    lines = changed_file.full_content.splitlines()
    changed_code = "\n".join(
        f"{i + 1}: {lines[i]}"
        for i in range(len(lines))
        if (i + 1) in changed_line_numbers
    )

    prompt = (
        "The following Python code uses SQLAlchemy. "
        "Extract the SQL queries this code would generate. "
        "Return ONLY a JSON array with objects containing 'sql' (the SQL string) "
        "and 'line_number' (the approximate source line). "
        "Use PostgreSQL dialect. If no queries can be extracted, return [].\n\n"
        f"Code:\n{changed_code}"
    )

    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        items = json.loads(raw.strip())
    except Exception as e:
        logger.warning("ORM extraction failed for %s: %s", changed_file.filename, e)
        return []

    queries = []
    for item in items:
        sql = item.get("sql", "").strip()
        line_number = item.get("line_number", 0)
        if not sql:
            continue
        file_lines = changed_file.full_content.splitlines()
        # Bug 4 fix: validate line_number before using it
        if not line_number or line_number < 1 or line_number > len(file_lines):
            logger.warning(
                "Skipping ORM query with invalid line_number=%s", line_number
            )
            continue
        diff_position = _find_nearest_diff_position(line_number, changed_file)
        start = max(0, line_number - 1 - 5)
        end = min(len(file_lines), line_number + 5)
        context = "\n".join(f"{i + 1}: {file_lines[i]}" for i in range(start, end))
        queries.append(
            ExtractedQuery(
                sql=sql,
                filename=changed_file.filename,
                line_number=line_number,
                diff_position=diff_position,
                source="orm",
                source_context=context,
            )
        )
    return queries


def extract_queries(
    changed_files: list[ChangedFile],
    anthropic_client,
) -> list[ExtractedQuery]:
    results = []
    for cf in changed_files:
        results.extend(_extract_raw_queries(cf))
        if anthropic_client is not None:
            results.extend(_extract_orm_queries(cf, anthropic_client))
    return results
