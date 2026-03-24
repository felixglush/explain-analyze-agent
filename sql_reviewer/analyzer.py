from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sql_reviewer.explainer import ExplainResult

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
_VALID_SEVERITIES = {"info", "warning", "critical"}

# Tool_use gives us schema-validated structured output without JSON parsing.
# Claude must call one of these two tools — "report_finding" if an issue exists,
# "no_issues" otherwise.
_TOOLS = [
    {
        "name": "report_finding",
        "description": "Report a structural SQL performance issue found in the query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["info", "warning", "critical"]},
                "summary": {
                    "type": "string",
                    "description": "One-line description of the issue",
                },
                "suggestion": {
                    "type": ["string", "null"],
                    "description": "SQL or config fix, or null",
                },
                "has_suggestion": {"type": "boolean"},
            },
            "required": ["severity", "summary", "suggestion", "has_suggestion"],
        },
    },
    {
        "name": "no_issues",
        "description": "Indicate that the query has no structural performance issues.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


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


def _build_prompt(result: ExplainResult) -> str:
    query = result.query
    parts = [
        "Review this SQL query and its EXPLAIN ANALYZE output. "
        "Focus on structural issues: missing indexes, sequential scans on large tables, "
        "inefficient join strategies, unanchored LIKE patterns, unnecessary sorts. "
        "Do NOT comment on cost values — the database has no rows so cost estimates are meaningless.\n",
    ]
    if query.source_context:
        parts.append(f"Source context:\n{query.source_context}")
    parts.append(f"SQL:\n{query.sql}")
    parts.append(f"\nEXPLAIN ANALYZE:\n{result.plan_text}")
    return "\n".join(parts)


def _validate(data: dict) -> str | None:
    """Return an error description if the tool input is invalid, else None."""
    if data.get("severity") not in _VALID_SEVERITIES:
        return f"severity must be one of {sorted(_VALID_SEVERITIES)}, got {data.get('severity')!r}"
    if not isinstance(data.get("summary"), str) or not data["summary"]:
        return "summary must be a non-empty string"
    if data.get("has_suggestion") is True and data.get("suggestion") is None:
        return "has_suggestion is True but suggestion is null"
    return None


def _analyze_one(result: ExplainResult, anthropic_client) -> Finding | None:
    messages: list[dict] = [{"role": "user", "content": _build_prompt(result)}]

    for attempt in range(MAX_RETRIES):
        try:
            message = anthropic_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                tools=_TOOLS,
                tool_choice={"type": "any"},
                messages=messages,
            )
        except Exception as e:
            logger.warning(
                "API call failed for %s:%s: %s",
                result.query.filename,
                result.query.line_number,
                e,
            )
            return None

        tool_block = next((b for b in message.content if b.type == "tool_use"), None)
        if tool_block is None or tool_block.name == "no_issues":
            return None

        data = tool_block.input
        error = _validate(data)
        if error:
            if attempt < MAX_RETRIES - 1:
                logger.debug("Validation failed (attempt %d): %s", attempt + 1, error)
                messages = [
                    *messages,
                    {"role": "assistant", "content": message.content},
                    {
                        "role": "user",
                        "content": f"Validation failed: {error}. Please call a tool again with the correct format.",  # noqa: E501
                    },
                ]
                continue
            logger.warning(
                "Analyzer validation failed for %s:%s after %d attempts: %s",
                result.query.filename,
                result.query.line_number,
                MAX_RETRIES,
                error,
            )
            return None

        return Finding(
            filename=result.query.filename,
            line_number=result.query.line_number,
            diff_position=result.query.diff_position,
            severity=data["severity"],
            summary=data["summary"],
            suggestion=data.get("suggestion"),
            has_suggestion=bool(data.get("has_suggestion")),
            plan_text=result.plan_text,
        )

    return None


def analyze_results(
    results: list[ExplainResult],
    anthropic_client,
) -> list[Finding]:
    findings = []
    for result in results:
        finding = _analyze_one(result, anthropic_client)
        if finding is not None:
            findings.append(finding)
    return findings
