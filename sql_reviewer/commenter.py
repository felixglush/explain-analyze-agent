from __future__ import annotations
import logging
from typing import Literal

import httpx

from sql_reviewer.analyzer import Finding

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
MARKER = "<!-- sql-reviewer -->"

SEVERITY_EMOJI: dict[str, str] = {
    "info": "ℹ️",
    "warning": "⚠️",
    "critical": "🔴",
}


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _build_comment_body(finding: Finding) -> str:
    emoji = SEVERITY_EMOJI.get(finding.severity, "ℹ️")
    lines = [
        MARKER,
        f"{emoji} **{finding.severity}** — {finding.summary}",
    ]
    if finding.has_suggestion and finding.suggestion:
        lines.append(f"\n```sql\n{finding.suggestion}\n```")
    lines.append(
        f"\n<details>\n<summary>EXPLAIN ANALYZE output</summary>\n\n"
        f"```\n{finding.plan_text}\n```\n</details>"
    )
    return "\n".join(lines)


def _delete_previous_comments(repo: str, pr_number: int, token: str) -> None:
    headers = _headers(token)
    resp = httpx.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/comments",
        headers=headers,
    )
    resp.raise_for_status()
    for comment in resp.json():
        if MARKER in comment.get("body", ""):
            del_resp = httpx.delete(
                f"{GITHUB_API}/repos/{repo}/pulls/comments/{comment['id']}",
                headers=headers,
            )
            if del_resp.status_code not in (204, 404):
                logger.warning("Failed to delete comment %d: %s", comment["id"], del_resp.status_code)


def post_findings(
    findings: list[Finding],
    repo: str,
    pr_number: int,
    token: str,
    total_queries: int,
) -> None:
    headers = _headers(token)
    _delete_previous_comments(repo, pr_number, token)

    postable = [f for f in findings if f.diff_position is not None]

    if not postable:
        # No findings (or all skipped) — post plain issue comment
        body = f"{MARKER} SQL Review: no issues found in {total_queries} quer{'y' if total_queries == 1 else 'ies'} analyzed"
        resp = httpx.post(
            f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
            headers=headers,
            json={"body": body},
        )
        resp.raise_for_status()
        return

    comments = [
        {
            "path": f.filename,
            "position": f.diff_position,
            "body": _build_comment_body(f),
        }
        for f in postable
    ]

    resp = httpx.post(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews",
        headers=headers,
        json={
            "body": MARKER,
            "event": "COMMENT",
            "comments": comments,
        },
    )
    resp.raise_for_status()
    logger.info("Posted %d finding(s) on PR #%d", len(postable), pr_number)
