from __future__ import annotations

import json as _json

import httpx
import pytest
import respx

from sql_reviewer.analyzer import Finding
from sql_reviewer.commenter import MARKER, _build_comment_body, post_findings

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


@pytest.mark.parametrize(
    "severity,icon",
    [
        ("warning", "⚠️"),
        ("critical", "🔴"),
        ("info", "ℹ️"),
    ],
)
def test_build_comment_body_severity_icon(severity, icon):
    body = _build_comment_body(make_finding(severity=severity))
    assert icon in body


def test_build_comment_body_no_suggestion():
    f = Finding(
        filename="src/app.py",
        line_number=1,
        diff_position=1,
        severity="info",
        summary="Looks fine",
        suggestion=None,
        has_suggestion=False,
        plan_text="Index Scan...",
    )
    body = _build_comment_body(f)
    assert "suggestion" not in body.lower() or "CREATE" not in body


@respx.mock
def test_post_findings_with_inline_comments():
    findings = [make_finding(diff_pos=4)]

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    review_route = respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=3)

    assert review_route.called, "POST /reviews should be called for inline findings"
    body = _json.loads(review_route.calls[0].request.content)
    assert len(body["comments"]) == 1
    assert body["comments"][0]["position"] == 4


@respx.mock
def test_post_findings_deletes_old_comments(paginated):
    findings = [make_finding(diff_pos=4)]

    old_comments = [
        {
            "id": 101,
            "path": "src/app.py",
            "position": 99,
            "body": f"{MARKER}\nold comment at pos 99",
        },
        {
            "id": 102,
            "path": "src/other.py",
            "position": 5,
            "body": f"{MARKER}\nstale finding",
        },
        {"id": 103, "body": "unrelated comment"},
    ]
    # Pagination: data on page 1, empty on page 2
    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(side_effect=paginated(old_comments))
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    delete_101 = respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/101").mock(
        return_value=httpx.Response(204)
    )
    delete_102 = respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/102").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert delete_101.called, "stale comment 101 should have been deleted"
    assert delete_102.called, "stale comment 102 should have been deleted"


@respx.mock
def test_post_no_findings_uses_issue_comment():
    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )

    post_findings([], REPO, PR_NUMBER, TOKEN, total_queries=5)


@respx.mock
def test_post_skips_finding_with_none_diff_position():
    findings = [
        make_finding(diff_pos=None),  # skipped — no diff position
        make_finding(diff_pos=3),  # included
    ]

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    posted_bodies = []

    def capture_review(request, route):
        posted_bodies.append(_json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(side_effect=capture_review)

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=2)
    assert len(posted_bodies) == 1
    assert len(posted_bodies[0]["comments"]) == 1  # only the finding with diff_position=3


@respx.mock
def test_post_findings_skips_identical_comment(paginated):
    finding = make_finding(diff_pos=4)
    existing_body = _build_comment_body(finding)

    existing_comment = {
        "id": 200,
        "path": "src/app.py",
        "position": 4,
        "body": existing_body,
    }

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        side_effect=paginated([existing_comment])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    delete_route = respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/200").mock(
        return_value=httpx.Response(204)
    )
    post_route = respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings([finding], REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert not delete_route.called, "DELETE should not be called for identical comment"
    assert not post_route.called, "POST /reviews should not be called for identical comment"


@respx.mock
def test_post_all_unanchored_findings_uses_issue_comment():
    """All findings have diff_position=None → posts issue-level summary with each finding listed."""
    findings = [
        make_finding(diff_pos=None, severity="warning"),
        Finding(
            filename="src/b.py", line_number=2, diff_position=None,
            severity="critical", summary="Full table scan",
            suggestion=None, has_suggestion=False,
            plan_text="Seq Scan on orders",
        ),
    ]

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    issue_route = respx.post(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=2)

    assert issue_route.called
    body = _json.loads(issue_route.calls[0].request.content)["body"]
    assert "could not be anchored" in body
    assert "⚠️" in body
    assert "🔴" in body


@respx.mock
def test_post_no_findings_deletes_stale_review_comments(paginated):
    """Stale bot review comments are deleted even when there are no new findings."""
    stale_review = {
        "id": 300, "path": "src/app.py", "position": 5,
        "body": f"{MARKER}\nold finding",
    }

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        side_effect=paginated([stale_review])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    delete_route = respx.delete(f"{BASE}/repos/{REPO}/pulls/comments/300").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    post_findings([], REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert delete_route.called


@respx.mock
def test_post_no_findings_replaces_changed_issue_comment(paginated):
    """Stale 'no issues' issue comment is deleted and replaced when the body differs."""
    old_body = f"{MARKER} SQL Review: no issues found in 3 queries analyzed"
    existing_issue = {"id": 400, "body": old_body}

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    # paginated so _fetch_existing_bot_issue_comments exercises its extend/page+1 branch
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        side_effect=paginated([existing_issue])
    )
    delete_route = respx.delete(f"{BASE}/repos/{REPO}/issues/comments/400").mock(
        return_value=httpx.Response(204)
    )
    post_route = respx.post(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    # total_queries=1 → "1 query" body differs from the existing "3 queries" comment
    post_findings([], REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert delete_route.called
    assert post_route.called


@respx.mock
def test_post_findings_deletes_stale_issue_comment(paginated):
    """Stale 'no issues' issue comment is deleted when new findings are being posted."""
    stale_issue = {"id": 500, "body": f"{MARKER} SQL Review: no issues found in 1 query analyzed"}

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        side_effect=paginated([stale_issue])
    )
    delete_route = respx.delete(f"{BASE}/repos/{REPO}/issues/comments/500").mock(
        return_value=httpx.Response(204)
    )
    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings([make_finding(diff_pos=3)], REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert delete_route.called


@respx.mock
def test_post_findings_deduplicates_same_position():
    """Two findings at the same (filename, diff_position) — only the first is posted."""
    findings = [make_finding(diff_pos=5), make_finding(diff_pos=5)]

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    posted_bodies = []

    def capture(request, route):
        posted_bodies.append(_json.loads(request.content))
        return httpx.Response(200, json={"id": 1})

    respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(side_effect=capture)

    post_findings(findings, REPO, PR_NUMBER, TOKEN, total_queries=2)

    assert len(posted_bodies) == 1
    assert len(posted_bodies[0]["comments"]) == 1


@respx.mock
def test_post_findings_patches_changed_comment(paginated):
    finding = make_finding(diff_pos=4)
    stale_body = f"{MARKER}\nold stale content that differs"

    existing_comment = {
        "id": 201,
        "path": "src/app.py",
        "position": 4,
        "body": stale_body,
    }

    respx.get(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/comments").mock(
        side_effect=paginated([existing_comment])
    )
    respx.get(f"{BASE}/repos/{REPO}/issues/{PR_NUMBER}/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    patch_route = respx.patch(f"{BASE}/repos/{REPO}/pulls/comments/201").mock(
        return_value=httpx.Response(200, json={"id": 201})
    )
    post_route = respx.post(f"{BASE}/repos/{REPO}/pulls/{PR_NUMBER}/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1})
    )

    post_findings([finding], REPO, PR_NUMBER, TOKEN, total_queries=1)

    assert patch_route.called, "PATCH should be called to update the changed comment"
    assert not post_route.called, "POST /reviews should not be called when PATCHing existing comment"

    import json as _json2

    patch_request = patch_route.calls[0].request
    patched_body = _json2.loads(patch_request.content)["body"]
    assert patched_body == _build_comment_body(finding)
