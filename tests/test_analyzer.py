from __future__ import annotations

from unittest.mock import MagicMock

from sql_reviewer.analyzer import analyze_results
from sql_reviewer.explainer import ExplainResult
from sql_reviewer.sql_extractor import ExtractedQuery


def make_result(
    sql: str,
    plan: str,
    line: int = 1,
    diff_pos: int | None = None,
    filename: str = "src/app.py",
) -> ExplainResult:
    query = ExtractedQuery(
        sql=sql,
        filename=filename,
        line_number=line,
        diff_position=diff_pos if diff_pos is not None else line,
        source="raw",
    )
    return ExplainResult(query=query, plan_text=plan)


SAMPLE_PLAN = "Seq Scan on users  (cost=0.00..1.00 rows=1 width=36)\n  Filter: (active = true)"


def _tool_response(name: str, input: dict) -> MagicMock:
    """Simulate a Claude messages.create() response that calls a tool."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input
    return MagicMock(content=[block])


def _finding_response(
    severity="warning", summary="Sequential scan", suggestion=None, has_suggestion=False
) -> MagicMock:
    return _tool_response(
        "report_finding",
        {
            "severity": severity,
            "summary": summary,
            "suggestion": suggestion,
            "has_suggestion": has_suggestion,
        },
    )


def _no_issues_response() -> MagicMock:
    return _tool_response("no_issues", {})


def test_analyze_returns_finding():
    result = make_result("SELECT * FROM users WHERE active = true", SAMPLE_PLAN, line=5)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _finding_response(
        severity="warning",
        summary="Sequential scan on users — consider an index on active",
        suggestion="CREATE INDEX idx_users_active ON users (active) WHERE active = true;",
        has_suggestion=True,
    )
    findings = analyze_results([result], mock_client)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "warning"
    assert f.line_number == 5
    assert f.diff_position == 5
    assert f.has_suggestion is True
    assert f.plan_text == SAMPLE_PLAN


def test_analyze_no_issues_produces_no_finding():
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _no_issues_response()
    assert analyze_results([result], mock_client) == []


def test_analyze_diff_position_comes_from_query():
    """diff_position is taken from the ExplainResult, not from Claude's response."""
    result = make_result("SELECT 1", SAMPLE_PLAN, line=10, diff_pos=42)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _finding_response(summary="ok")
    findings = analyze_results([result], mock_client)
    assert findings[0].diff_position == 42


def test_analyze_skips_on_api_error():
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")
    assert analyze_results([result], mock_client) == []


def test_analyze_retries_on_invalid_severity_then_succeeds():
    """On validation failure, _analyze_one retries once with a correction message."""
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _tool_response(
            "report_finding",
            {
                "severity": "bad",
                "summary": "x",
                "suggestion": None,
                "has_suggestion": False,
            },
        ),
        _finding_response(severity="info", summary="fixed"),
    ]
    findings = analyze_results([result], mock_client)
    assert len(findings) == 1
    assert findings[0].severity == "info"
    assert mock_client.messages.create.call_count == 2


def test_analyze_returns_none_after_max_retries():
    """After MAX_RETRIES failed validations, _analyze_one gives up and returns None."""
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    # Both attempts return bad severity
    mock_client.messages.create.return_value = _tool_response(
        "report_finding",
        {
            "severity": "INVALID",
            "summary": "x",
            "suggestion": None,
            "has_suggestion": False,
        },
    )
    assert analyze_results([result], mock_client) == []


def test_analyze_one_api_call_per_query():
    """Each query triggers exactly one Claude API call (plus retries if any)."""
    results = [make_result(f"SELECT {i}", SAMPLE_PLAN, line=i + 1) for i in range(5)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _no_issues_response()
    analyze_results(results, mock_client)
    assert mock_client.messages.create.call_count == 5


def test_analyze_multiple_files_correct_filenames():
    result_a = make_result("SELECT 1", SAMPLE_PLAN, line=1, filename="src/a.py")
    result_b = make_result("SELECT 2", SAMPLE_PLAN, line=2, filename="src/b.py")
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _finding_response(summary="issue in a"),
        _finding_response(summary="issue in b"),
    ]
    findings = analyze_results([result_a, result_b], mock_client)
    assert len(findings) == 2
    assert {f.filename for f in findings} == {"src/a.py", "src/b.py"}


def test_analyze_empty_summary_retries():
    """Claude returns empty summary; validator rejects it and retries. Second response is valid."""
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _tool_response(
            "report_finding",
            {
                "severity": "warning",
                "summary": "",
                "suggestion": None,
                "has_suggestion": False,
            },
        ),
        _finding_response(severity="warning", summary="Non-empty summary"),
    ]
    findings = analyze_results([result], mock_client)
    assert mock_client.messages.create.call_count == 2
    assert len(findings) == 1
    assert findings[0].summary != ""


def test_analyze_inconsistent_has_suggestion():
    """Claude returns has_suggestion=True but suggestion=None; validator rejects and retries."""
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _tool_response(
            "report_finding",
            {
                "severity": "warning",
                "summary": "Issue found",
                "suggestion": None,
                "has_suggestion": True,
            },
        ),
        _finding_response(
            severity="warning",
            summary="Issue found",
            suggestion="CREATE INDEX ...",
            has_suggestion=True,
        ),
    ]
    findings = analyze_results([result], mock_client)
    assert mock_client.messages.create.call_count == 2
    assert len(findings) == 1
