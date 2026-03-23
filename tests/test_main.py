import os
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path


def test_main_exits_1_on_missing_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO", "owner/repo")
    monkeypatch.setenv("PR_NUMBER", "1")
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")

    from sql_reviewer.main import main
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

    with patch("sql_reviewer.main.fetch_changed_files", return_value=[]), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        from sql_reviewer import main as main_mod
        import importlib; importlib.reload(main_mod)
        from sql_reviewer.main import main
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
