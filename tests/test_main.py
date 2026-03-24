import pytest
from unittest.mock import patch
from sql_reviewer.main import main
from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import ExplainResult
from sql_reviewer.analyzer import Finding


def test_main_exits_1_on_missing_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1


def test_main_exits_0_when_no_files_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sql-reviewer.yml").write_text(
        "schema_file: schema.sql\nfile_patterns:\n  - 'src/**/*.py'\n"
    )
    (tmp_path / "schema.sql").write_text("-- empty schema")
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

    # Schema setup runs after the file-match check, so it's never reached here.
    with patch("sql_reviewer.main.fetch_changed_files", return_value=[]):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0


def test_main_exits_0_on_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".sql-reviewer.yml").write_text(
        "schema_file: schema.sql\nfile_patterns:\n  - 'src/**/*.py'\n"
    )
    (tmp_path / "schema.sql").write_text("-- schema")

    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

    changed_file = ChangedFile(
        filename="src/app.py",
        full_content="SELECT * FROM users\n",
        changed_lines=[ChangedLine(line_number=1, diff_position=1, content="SELECT * FROM users")],
    )
    extracted_query = ExtractedQuery(
        sql="SELECT * FROM users",
        filename="src/app.py",
        line_number=1,
        diff_position=1,
        source="raw",
    )
    explain_result = ExplainResult(
        query=extracted_query,
        plan_text="Seq Scan on users  (cost=0.00..1.00 rows=1 width=36)",
    )

    with patch("sql_reviewer.main.fetch_changed_files", return_value=[changed_file]), \
         patch("sql_reviewer.main.extract_queries", return_value=[extracted_query]), \
         patch("sql_reviewer.main.explain_queries", return_value=[explain_result]), \
         patch("sql_reviewer.main.analyze_results", return_value=[]), \
         patch("sql_reviewer.main.post_findings", return_value=None), \
         patch("sql_reviewer.main._run_schema_setup", return_value=None):
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
