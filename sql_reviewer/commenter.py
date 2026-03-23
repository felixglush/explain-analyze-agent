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


def _fetch_existing_bot_review_comments(
    repo: str, pr_number: int, token: str
) -> dict[tuple[str, int], dict]:
    """Return bot review comments keyed by (path, position)."""
    resp = httpx.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/comments",
        headers=_headers(token),
    )
    resp.raise_for_status()
    result: dict[tuple[str, int], dict] = {}
    for c in resp.json():
        if MARKER in c.get("body", "") and c.get("position") is not None:
            result[(c["path"], c["position"])] = c
    return result


def _fetch_existing_bot_issue_comments(
    repo: str, pr_number: int, token: str
) -> list[dict]:
    """Return bot issue-level comments (e.g. 'no issues found')."""
    resp = httpx.get(
        f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
        headers=_headers(token),
    )
    resp.raise_for_status()
    return [c for c in resp.json() if MARKER in c.get("body", "")]


def _delete_review_comment(comment_id: int, repo: str, token: str) -> None:
    resp = httpx.delete(
        f"{GITHUB_API}/repos/{repo}/pulls/comments/{comment_id}",
        headers=_headers(token),
    )
    if resp.status_code not in (204, 404):
        logger.warning("Failed to delete review comment %d: %s", comment_id, resp.status_code)


def _delete_issue_comment(comment_id: int, repo: str, token: str) -> None:
    resp = httpx.delete(
        f"{GITHUB_API}/repos/{repo}/issues/comments/{comment_id}",
        headers=_headers(token),
    )
    if resp.status_code not in (204, 404):
        logger.warning("Failed to delete issue comment %d: %s", comment_id, resp.status_code)


def _patch_review_comment(comment_id: int, body: str, repo: str, token: str) -> None:
    """Update an existing review comment body in-place (preserves thread)."""
    resp = httpx.patch(
        f"{GITHUB_API}/repos/{repo}/pulls/comments/{comment_id}",
        headers=_headers(token),
        json={"body": body},
    )
    resp.raise_for_status()


def post_findings(
    findings: list[Finding],
    repo: str,
    pr_number: int,
    token: str,
    total_queries: int,
) -> None:
    headers = _headers(token)
    postable = [f for f in findings if f.diff_position is not None]

    existing_review = _fetch_existing_bot_review_comments(repo, pr_number, token)
    existing_issue = _fetch_existing_bot_issue_comments(repo, pr_number, token)

    if not postable:
        # No findings — post plain issue comment (deduplicated)
        body = f"{MARKER} SQL Review: no issues found in {total_queries} quer{'y' if total_queries == 1 else 'ies'} analyzed"
        if not any(c["body"] == body for c in existing_issue):
            for c in existing_issue:
                _delete_issue_comment(c["id"], repo, token)
            for c in existing_review.values():
                _delete_review_comment(c["id"], repo, token)
            resp = httpx.post(
                f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments",
                headers=headers,
                json={"body": body},
            )
            resp.raise_for_status()
        return

    # Clean up any stale "no issues found" issue comments
    for c in existing_issue:
        _delete_issue_comment(c["id"], repo, token)

    desired: dict[tuple[str, int], Finding] = {
        (f.filename, f.diff_position): f  # type: ignore[index]
        for f in postable
    }

    # Delete comments for findings that no longer exist
    for key in set(existing_review) - set(desired):
        _delete_review_comment(existing_review[key]["id"], repo, token)

    # Update changed comments in-place; collect genuinely new ones
    to_post: list[Finding] = []
    for key, f in desired.items():
        body = _build_comment_body(f)
        if key in existing_review:
            if existing_review[key]["body"] != body:
                _patch_review_comment(existing_review[key]["id"], body, repo, token)
            # else: identical — leave untouched
        else:
            to_post.append(f)

    if not to_post:
        logger.info("No new findings to post on PR #%d", pr_number)
        return

    comments = [
        {"path": f.filename, "position": f.diff_position, "body": _build_comment_body(f)}
        for f in to_post
    ]
    resp = httpx.post(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/reviews",
        headers=headers,
        json={"body": MARKER, "event": "COMMENT", "comments": comments},
    )
    resp.raise_for_status()
    logger.info("Posted %d new finding(s) on PR #%d", len(to_post), pr_number)
