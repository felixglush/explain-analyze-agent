from pathlib import Path

import pytest

from sql_reviewer.config import ConfigError, load_config


def test_schema_file_config(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text(
        "schema_file: db/model_ddl.sql\nfile_patterns:\n  - 'src/**/*.py'\n"
    )
    config = load_config(tmp_path / ".sql-reviewer.yml")
    assert config.schema_file == "db/model_ddl.sql"
    assert config.setup_command is None
    assert config.file_patterns == ["src/**/*.py"]


def test_setup_command_config(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text(
        "setup_command: python manage.py migrate\nfile_patterns:\n  - 'app/**/*.py'\n"
    )
    config = load_config(tmp_path / ".sql-reviewer.yml")
    assert config.setup_command == "python manage.py migrate"
    assert config.schema_file is None


def test_both_set_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text(
        "schema_file: db/schema.sql\nsetup_command: make migrate\nfile_patterns:\n  - '**/*.py'\n"
    )
    with pytest.raises(ConfigError, match="exactly one"):
        load_config(tmp_path / ".sql-reviewer.yml")


def test_neither_set_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text("file_patterns:\n  - '**/*.py'\n")
    with pytest.raises(ConfigError, match="exactly one"):
        load_config(tmp_path / ".sql-reviewer.yml")


def test_missing_file_raises():
    with pytest.raises(ConfigError, match="not found"):
        load_config(Path("/nonexistent/.sql-reviewer.yml"))


def test_missing_file_patterns_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text("schema_file: db/schema.sql\n")
    with pytest.raises(ConfigError, match="file_patterns"):
        load_config(tmp_path / ".sql-reviewer.yml")


def test_file_patterns_as_string_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text("file_patterns: 'src/**/*.py'\nschema_file: schema.sql\n")
    with pytest.raises(ConfigError, match="file_patterns"):
        load_config(tmp_path / ".sql-reviewer.yml")


def test_file_patterns_empty_list_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text("file_patterns: []\nschema_file: schema.sql\n")
    with pytest.raises(ConfigError, match="file_patterns"):
        load_config(tmp_path / ".sql-reviewer.yml")


def test_schema_file_empty_string_raises(tmp_path):
    (tmp_path / ".sql-reviewer.yml").write_text("schema_file: ''\nfile_patterns:\n  - 'src/**/*.py'\n")
    with pytest.raises(ConfigError):
        load_config(tmp_path / ".sql-reviewer.yml")
