import pytest
from unittest.mock import MagicMock, patch
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import ExplainResult
from sql_reviewer.analyzer import analyze_results, Finding, _build_batches


def make_result(sql: str, plan: str, line: int = 1, filename: str = "src/app.py") -> ExplainResult:
    query = ExtractedQuery(
        sql=sql, filename=filename, line_number=line,
        diff_position=line, source="raw",
    )
    return ExplainResult(query=query, plan_text=plan)


SAMPLE_PLAN = "Seq Scan on users  (cost=0.00..1.00 rows=1 width=36)\n  Filter: (active = true)"


def test_analyze_returns_findings(mocker):
    results = [make_result("SELECT * FROM users WHERE active = true", SAMPLE_PLAN, line=5)]
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="""
[
  {
    "line_number": 5,
    "severity": "warning",
    "summary": "Sequential scan on users — consider an index on active",
    "suggestion": "CREATE INDEX idx_users_active ON users (active) WHERE active = true;",
    "has_suggestion": true
  }
]
""")])

    findings = analyze_results(results, mock_client)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert findings[0].diff_position == 5
    assert findings[0].has_suggestion is True
    assert findings[0].plan_text == SAMPLE_PLAN


def test_analyze_resolves_diff_position_from_query():
    query = ExtractedQuery(
        sql="SELECT 1", filename="src/app.py", line_number=10,
        diff_position=42,  # diff position differs from line number
        source="raw",
    )
    result = ExplainResult(query=query, plan_text=SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="""
[{"line_number": 10, "severity": "info", "summary": "ok", "suggestion": null, "has_suggestion": false}]
""")])

    findings = analyze_results([result], mock_client)
    assert findings[0].diff_position == 42  # resolved from ExtractedQuery


def test_analyze_none_diff_position_when_line_not_in_batch():
    result = make_result("SELECT 1", SAMPLE_PLAN, line=5)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="""
[{"line_number": 999, "severity": "info", "summary": "ok", "suggestion": null, "has_suggestion": false}]
""")])

    findings = analyze_results([result], mock_client)
    assert findings[0].diff_position is None  # line 999 not in batch


def test_analyze_skips_batch_on_malformed_json():
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text="this is not json")]
    )
    findings = analyze_results([result], mock_client)
    assert findings == []


def test_analyze_skips_batch_on_api_error():
    result = make_result("SELECT 1", SAMPLE_PLAN)
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")
    findings = analyze_results([result], mock_client)
    assert findings == []


def test_batching_splits_by_token_limit():
    # Create 3 results, two with large plans that together exceed the token budget
    big_plan = "x" * 32001  # ~8000 tokens at 4 chars/token
    results = [
        make_result("SELECT 1", big_plan, line=1),
        make_result("SELECT 2", big_plan, line=2),
        make_result("SELECT 3", "short plan", line=3),
    ]
    batches = _build_batches(results)
    assert len(batches) == 3  # each large plan is its own batch; short one is third


def test_batching_splits_by_query_count():
    results = [make_result(f"SELECT {i}", "short", line=i) for i in range(12)]
    batches = _build_batches(results)
    assert len(batches) == 2  # 10 + 2
    assert len(batches[0]) == 10
    assert len(batches[1]) == 2


def test_analyze_multiple_files_correct_filenames():
    """Findings must carry the correct filename even when multiple files are analyzed."""
    result_a = make_result("SELECT 1", SAMPLE_PLAN, line=1, filename="src/a.py")
    result_b = make_result("SELECT 2", SAMPLE_PLAN, line=2, filename="src/b.py")

    mock_client = MagicMock()
    # Return a finding for each call; Claude is called once per file
    mock_client.messages.create.side_effect = [
        MagicMock(content=[MagicMock(text='[{"line_number": 1, "severity": "info", "summary": "ok a", "suggestion": null, "has_suggestion": false}]')]),
        MagicMock(content=[MagicMock(text='[{"line_number": 2, "severity": "info", "summary": "ok b", "suggestion": null, "has_suggestion": false}]')]),
    ]

    findings = analyze_results([result_a, result_b], mock_client)
    assert len(findings) == 2
    filenames = {f.filename for f in findings}
    assert filenames == {"src/a.py", "src/b.py"}
