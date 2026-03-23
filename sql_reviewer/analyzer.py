from __future__ import annotations
import json
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from sql_reviewer.explainer import ExplainResult

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_BATCH = 10
MAX_PLAN_TOKENS_PER_BATCH = 8000
CHARS_PER_TOKEN = 4


@dataclass
class Finding:
    filename: str
    line_number: int
    diff_position: int | None
    severity: Literal["info", "warning", "critical"]
    summary: str
    suggestion: str | None
    has_suggestion: bool
    plan_text: str


def _build_batches(results: list[ExplainResult]) -> list[list[ExplainResult]]:
    """Split results into batches respecting MAX_QUERIES and token budget."""
    batches: list[list[ExplainResult]] = []
    current_batch: list[ExplainResult] = []
    current_tokens = 0

    for result in results:
        plan_tokens = len(result.plan_text) // CHARS_PER_TOKEN
        if current_batch and (
            len(current_batch) >= MAX_QUERIES_PER_BATCH
            or current_tokens + plan_tokens > MAX_PLAN_TOKENS_PER_BATCH
        ):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(result)
        current_tokens += plan_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _build_prompt(batch: list[ExplainResult]) -> str:
    parts = [
        "Review these SQL queries and their EXPLAIN ANALYZE output. "
        "Focus on structural issues: missing indexes, sequential scans on large tables, "
        "inefficient join strategies, unanchored LIKE patterns, unnecessary sorts. "
        "Do NOT comment on cost values — the database has no rows so cost estimates are meaningless. "
        "Return ONLY a JSON array. Each object must have: "
        "line_number (int), severity ('info'|'warning'|'critical'), "
        "summary (string), suggestion (string or null), has_suggestion (bool). "
        "If a query looks fine, omit it from the array. Return [] if no issues found.\n"
    ]

    for i, result in enumerate(batch, 1):
        query = result.query
        parts.append(f"\n--- Query {i} (file: {query.filename}, line: {query.line_number}) ---")
        if query.source_context:
            parts.append(f"Source context:\n{query.source_context}")
        parts.append(f"SQL:\n{query.sql}")
        parts.append(f"\nEXPLAIN ANALYZE:\n{result.plan_text}")

    return "\n".join(parts)


def _analyze_batch(
    batch: list[ExplainResult],
    anthropic_client,
    filename: str,
) -> list[Finding]:
    # Build a lookup: line_number → ExplainResult (for diff_position resolution)
    line_to_result = {r.query.line_number: r for r in batch}

    prompt = _build_prompt(batch)
    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        items = json.loads(raw)
    except Exception as e:
        logger.warning("Analyzer batch failed: %s", e)
        return []

    findings = []
    for item in items:
        line_number = item.get("line_number")
        source_result = line_to_result.get(line_number)
        diff_position = source_result.query.diff_position if source_result else None
        plan_text = source_result.plan_text if source_result else ""

        findings.append(Finding(
            filename=filename,
            line_number=line_number,
            diff_position=diff_position,
            severity=item.get("severity", "info"),
            summary=item.get("summary", ""),
            suggestion=item.get("suggestion"),
            has_suggestion=item.get("has_suggestion", False),
            plan_text=plan_text,
        ))

    return findings


def analyze_results(
    results: list[ExplainResult],
    anthropic_client,
) -> list[Finding]:
    # Group by filename first, then split each file's results into batches.
    # This ensures _analyze_batch always receives results from a single file,
    # so Finding.filename is always correct.
    by_file: dict[str, list[ExplainResult]] = defaultdict(list)
    for result in results:
        by_file[result.query.filename].append(result)

    findings = []
    for file_name, file_results in by_file.items():
        for batch in _build_batches(file_results):
            findings.extend(_analyze_batch(batch, anthropic_client, filename=file_name))
    return findings
