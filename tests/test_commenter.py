from __future__ import annotations
import pytest
import respx
import httpx
from sql_reviewer.analyzer import Finding
from sql_reviewer.commenter import post_findings, _build_comment_body, MARKER

REPO = "owner/repo"
PR_NUMBER = 7
TOKEN = "ghtoken"
BASE = "https://api.github.com"


def make_finding(line: int = 5, diff_pos: int | None = 5, severity: str = "warning") -> Finding:
    return Finding(
        filename="src/app.py",
        line_number=line,
        diff_position=diff_pos,
        severity=severity,
        summary="Sequential scan on users",
        suggestion="CREATE INDEX idx ON users (active);",
        has_suggestion=True,
        plan_text="Seq Scan on users  (cost=0.00..1.00 rows=1 width=36)",
    )


def test_build_comment_body_warning():
    f = make_finding(severity="warning")
    body = _build_comment_body(f)
    assert MARKER in body
    assert "⚠️" in body
    assert "Sequential scan" in body
    assert "CREATE INDEX" in body
    assert "Seq Scan" in body
    assert "<details>" in body


def test_build_comment_body_critical():
    f = make_finding(severity="critical")
    body = _build_comment_body(f)
    assert "🔴" in body


def test_build_comment_body_info():
    f = make_finding(severity="info")
    body = _build_comment_body(f)
    assert "ℹ️" in body


def test_build_comment_body_no_suggestion():
    f = Finding(
        filename="src/app.py", line_number=1, diff_position=1,
        severity="info", summary="Looks fine", suggestion=None,
        has_suggestion=False, plan_text="Index Scan...",
    )
    body = _build_comment_body(f)
    assert "suggestion" not in body.lower() or "CREATE" not in body


@respx.mock
def test_post_findings_with_inline_comments():
    findings = [make_finding(diff_pos=4)]

    # No existing sql-reviewer comments
    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    # Post review
    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=3)


@respx.mock
def test_post_findings_deletes_old_comments():
    findings = [make_finding(diff_pos=4)]

    # Two existing sql-reviewer comments
    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[
            {"id": 101, "body": f"{MARKER}\nold comment"},
            {"id": 102, "body": f"{MARKER}\nanother old comment"},
            {"id": 103, "body": "unrelated comment"},
        ])
    )
    respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/101").mock(
        return_value=httpx.Response(204)
    )
    respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/102").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=1)


@respx.mock
def test_post_no_findings_uses_issue_comment():
    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )

    post_findings([], REPO, PR_NUMBER, TOKEN, total_queries=5)


@respx.mock
def test_post_skips_finding_with_none_diff_position():
    findings = [
        make_finding(diff_pos=None),   # should be skipped
        make_finding(diff_pos=3),      # should be included
    ]

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    posted_bodies = []
    def capture_review(request, route):
        import json as _json
        body = _json.loads(request.content)
        posted_bodies.append(body)
        return httpx.Response(200, json={"id": 1})

    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(side_effect=capture_review)

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=2)
    assert len(posted_bodies) == 1
    assert len(posted_bodies[0]["comments"]) == 1  # only the one with diff_position=3
