# SQL Review GitHub Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python tool that automatically runs EXPLAIN ANALYZE on SQL queries found in a PR's changed Python files and posts inline review comments with Claude's improvement suggestions.

**Architecture:** Single Python package (`sql_reviewer/`) with seven focused modules — config, diff_parser, sql_extractor, explainer, analyzer, commenter, and main — each with one clear responsibility. The pipeline runs as a GitHub Actions step triggered on PR open.

**Tech Stack:** Python 3.12, psycopg2-binary (Postgres), anthropic SDK (Claude), httpx (GitHub API), sqlglot (SQL detection), PyYAML (config), pytest + respx + pytest-mock (testing)

---

## File Map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Package config, all dependencies |
| `sql_reviewer/__init__.py` | Re-exports all public dataclasses |
| `sql_reviewer/__main__.py` | Entry point for `python -m sql_reviewer` |
| `sql_reviewer/main.py` | Pipeline orchestration, schema setup, exit codes |
| `sql_reviewer/config.py` | Load and validate `.sql-reviewer.yml` |
| `sql_reviewer/diff_parser.py` | Fetch PR diff, parse hunk positions, fetch file content |
| `sql_reviewer/sql_extractor.py` | Extract raw SQL strings + ORM→SQL via Claude |
| `sql_reviewer/explainer.py` | Param substitution + EXPLAIN ANALYZE via psycopg2 |
| `sql_reviewer/analyzer.py` | Send query+plan to Claude, return structured findings |
| `sql_reviewer/commenter.py` | Delete old comments, post inline review via GitHub API |
| `tests/fixtures/sample_schema.sql` | Sample Postgres schema for local e2e testing |
| `tests/fixtures/.sql-reviewer.yml` | Sample config pointing at the sample schema |
| `workflow-template.yml` | Copy-paste workflow for consuming repos |
| `tests/conftest.py` | Shared pytest fixtures |
| `tests/test_config.py` | Config loading/validation unit tests |
| `tests/test_diff_parser.py` | Diff parsing unit tests (mocked GitHub API) |
| `tests/test_sql_extractor.py` | SQL extraction unit tests (mocked Claude) |
| `tests/test_explainer.py` | EXPLAIN ANALYZE integration tests (real Postgres) |
| `tests/test_analyzer.py` | Analysis unit tests (mocked Claude) |
| `tests/test_commenter.py` | Comment posting unit tests (mocked GitHub API) |

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `sql_reviewer/__init__.py`
- Create: `sql_reviewer/__main__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "sql-reviewer"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "psycopg2-binary>=2.9",
    "anthropic>=0.25",
    "httpx>=0.27",
    "sqlglot>=23",
    "PyYAML>=6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-mock>=3.14",
    "respx>=0.21",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["sql_reviewer*"]
```

- [ ] **Step 2: Create `sql_reviewer/__init__.py`** (empty for now — populated in later tasks)

```python
# Public dataclasses re-exported here as modules are implemented.
```

- [ ] **Step 3: Create `sql_reviewer/__main__.py`**

```python
from sql_reviewer.main import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Create `tests/conftest.py`** (empty for now — fixtures added per task)

```python
import pytest
```

- [ ] **Step 5: Install the package in editable mode**

```bash
pip install -e ".[dev]"
```

Expected: installation completes without errors.

- [ ] **Step 6: Verify pytest runs**

```bash
pytest tests/ -v
```

Expected: `no tests ran` (exit 0).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml sql_reviewer/ tests/
git commit -m "feat: scaffold project structure"
```

---

## Task 2: `config.py` — load and validate `.sql-reviewer.yml`

**Files:**
- Create: `sql_reviewer/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
from pathlib import Path
from sql_reviewer.config import load_config, ConfigError


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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `config.py` doesn't exist yet.

- [ ] **Step 3: Implement `sql_reviewer/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


class ConfigError(Exception):
    pass


@dataclass
class Config:
    schema_file: str | None
    setup_command: str | None
    file_patterns: list[str]


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    schema_file = raw.get("schema_file")
    setup_command = raw.get("setup_command")
    file_patterns = raw.get("file_patterns")

    if not file_patterns:
        raise ConfigError("Config must include 'file_patterns'")

    has_schema = bool(schema_file)
    has_command = bool(setup_command)
    if has_schema == has_command:  # both True or both False
        raise ConfigError(
            "Config must set exactly one of 'schema_file' or 'setup_command'"
        )

    return Config(
        schema_file=schema_file,
        setup_command=setup_command,
        file_patterns=file_patterns,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Update `sql_reviewer/__init__.py`**

```python
from sql_reviewer.config import Config, ConfigError

__all__ = ["Config", "ConfigError"]
```

- [ ] **Step 6: Commit**

```bash
git add sql_reviewer/config.py sql_reviewer/__init__.py tests/test_config.py
git commit -m "feat: add config loading with schema_file/setup_command validation"
```

---

## Task 3: `diff_parser.py` — fetch PR diff and map line positions

**Files:**
- Create: `sql_reviewer/diff_parser.py`
- Create: `tests/test_diff_parser.py`

**Background:** The GitHub PR files API returns a `patch` field — a unified diff string. Each line in the patch has a 1-based `position` (counting from the start of the whole patch including `@@` headers). We need to map absolute file line numbers → diff positions for the GitHub review comment API.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_diff_parser.py
import base64
import pytest
import respx
import httpx
from sql_reviewer.diff_parser import (
    fetch_changed_files,
    parse_patch_positions,
    ChangedLine,
    ChangedFile,
)

REPO = "owner/repo"
PR_NUMBER = 42
TOKEN = "ghtoken"
BASE_URL = "https://api.github.com"


def test_parse_patch_positions_single_hunk():
    patch = "@@ -10,3 +10,4 @@\n unchanged\n unchanged\n+added line\n unchanged"
    result = parse_patch_positions(patch)
    # position 1 = @@ header, position 2 = first unchanged, position 3 = second unchanged
    # position 4 = added line (file line 12)
    assert result == {12: 4}


def test_parse_patch_positions_multiple_hunks():
    patch = (
        "@@ -1,2 +1,3 @@\n unchanged\n+first add\n unchanged\n"
        "@@ -10,2 +11,3 @@\n unchanged\n+second add\n unchanged"
    )
    result = parse_patch_positions(patch)
    assert result[2] == 2   # "first add" at file line 2, position 2
    assert result[12] == 7  # "second add" at file line 12, position 7


def test_parse_patch_positions_no_additions():
    patch = "@@ -1,2 +1,2 @@\n unchanged\n unchanged"
    assert parse_patch_positions(patch) == {}


@respx.mock
def test_fetch_changed_files_returns_changed_lines():
    file_content = "line1\nline2\nSELECT * FROM users\nline4\n"
    encoded = base64.b64encode(file_content.encode()).decode()
    patch = "@@ -1,3 +1,4 @@\n line1\n line2\n+SELECT * FROM users\n line4"

    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        return_value=httpx.Response(200, json=[
            {"filename": "src/app.py", "status": "modified", "patch": patch}
        ])
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "feature-branch"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/src/app.py").mock(
        return_value=httpx.Response(200, json={"content": encoded + "\n", "encoding": "base64"})
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])

    assert len(files) == 1
    assert files[0].filename == "src/app.py"
    assert files[0].full_content == file_content
    changed_line_numbers = [cl.line_number for cl in files[0].changed_lines]
    assert 3 in changed_line_numbers
    line = next(cl for cl in files[0].changed_lines if cl.line_number == 3)
    assert line.diff_position == 4
    assert "SELECT" in line.content


@respx.mock
def test_fetch_changed_files_filters_by_pattern():
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        return_value=httpx.Response(200, json=[
            {"filename": "README.md", "status": "modified", "patch": "@@ -1 +1 @@\n+text"},
            {"filename": "src/app.py", "status": "modified", "patch": "@@ -1 +1,2 @@\n unchanged\n+new"},
        ])
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "main"}})
    )
    file_content = "unchanged\nnew\n"
    encoded = base64.b64encode(file_content.encode()).decode()
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/src/app.py").mock(
        return_value=httpx.Response(200, json={"content": encoded, "encoding": "base64"})
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])
    assert len(files) == 1
    assert files[0].filename == "src/app.py"


@respx.mock
def test_fetch_changed_files_skips_file_without_patch():
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        return_value=httpx.Response(200, json=[
            {"filename": "src/big.py", "status": "modified"}  # no patch key
        ])
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "main"}})
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])
    assert files == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_diff_parser.py -v
```

Expected: `ImportError` — `diff_parser.py` doesn't exist yet.

- [ ] **Step 3: Implement `sql_reviewer/diff_parser.py`**

```python
from __future__ import annotations
import base64
import fnmatch
import re
from dataclasses import dataclass, field

import httpx


GITHUB_API = "https://api.github.com"


@dataclass
class ChangedLine:
    line_number: int    # absolute line number in the file (1-based)
    diff_position: int  # position within the diff patch (for GitHub review API)
    content: str


@dataclass
class ChangedFile:
    filename: str
    full_content: str
    changed_lines: list[ChangedLine] = field(default_factory=list)


def parse_patch_positions(patch: str) -> dict[int, int]:
    """Return {file_line_number: diff_position} for all added (+) lines."""
    result: dict[int, int] = {}
    current_new_line = 0
    position = 0

    for line in patch.split("\n"):
        position += 1
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                current_new_line = int(m.group(1)) - 1
        elif line.startswith("+"):
            current_new_line += 1
            result[current_new_line] = position
        elif line.startswith(" "):
            current_new_line += 1
        # "-" lines: don't advance new line counter

    return result


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _matches_patterns(filename: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(filename, p) for p in patterns)


def fetch_changed_files(
    repo: str,
    pr_number: int,
    token: str,
    file_patterns: list[str],
) -> list[ChangedFile]:
    headers = _headers(token)

    # Get PR head branch for fetching file content
    pr_resp = httpx.get(f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}", headers=headers)
    pr_resp.raise_for_status()
    head_ref = pr_resp.json()["head"]["ref"]

    # Get list of changed files
    files_resp = httpx.get(
        f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
        headers=headers,
    )
    files_resp.raise_for_status()

    results: list[ChangedFile] = []

    for file_info in files_resp.json():
        filename = file_info["filename"]
        patch = file_info.get("patch")

        if not patch:
            continue  # large files may omit patch; skip
        if not _matches_patterns(filename, file_patterns):
            continue

        # Fetch full file content
        content_resp = httpx.get(
            f"{GITHUB_API}/repos/{repo}/contents/{filename}",
            headers=headers,
            params={"ref": head_ref},
        )
        content_resp.raise_for_status()
        encoded = content_resp.json()["content"].replace("\n", "")
        full_content = base64.b64decode(encoded).decode("utf-8", errors="replace")

        # Parse diff positions for added lines
        line_to_position = parse_patch_positions(patch)

        changed_lines = [
            ChangedLine(
                line_number=line_num,
                diff_position=pos,
                content=full_content.splitlines()[line_num - 1]
                if line_num <= len(full_content.splitlines())
                else "",
            )
            for line_num, pos in sorted(line_to_position.items())
        ]

        results.append(ChangedFile(
            filename=filename,
            full_content=full_content,
            changed_lines=changed_lines,
        ))

    return results
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_diff_parser.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Update `sql_reviewer/__init__.py`**

```python
from sql_reviewer.config import Config, ConfigError
from sql_reviewer.diff_parser import ChangedFile, ChangedLine

__all__ = ["Config", "ConfigError", "ChangedFile", "ChangedLine"]
```

- [ ] **Step 6: Commit**

```bash
git add sql_reviewer/diff_parser.py sql_reviewer/__init__.py tests/test_diff_parser.py
git commit -m "feat: add diff parser with hunk position mapping"
```

---

## Task 4: `sql_extractor.py` — raw SQL extraction

**Files:**
- Create: `sql_reviewer/sql_extractor.py`
- Create: `tests/test_sql_extractor.py`

**Background:** Uses `sqlglot` to find SQL string literals in Python source. Only looks at lines that appear in `ChangedFile.changed_lines`. Assigns `diff_position` directly from the `ChangedLine`.

- [ ] **Step 1: Write failing tests for raw SQL extraction**

```python
# tests/test_sql_extractor.py
import pytest
from unittest.mock import MagicMock
from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import extract_queries, ExtractedQuery


def make_changed_file(filename: str, lines: dict[int, str], full_content: str = "") -> ChangedFile:
    """Helper: build a ChangedFile where keys are line numbers, values are content."""
    changed_lines = [
        ChangedLine(line_number=ln, diff_position=ln, content=content)
        for ln, content in lines.items()
    ]
    return ChangedFile(filename=filename, full_content=full_content, changed_lines=changed_lines)


def test_extracts_raw_select_string():
    content = 'query = "SELECT id, name FROM users WHERE active = true"\n'
    cf = make_changed_file("src/app.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "SELECT" in raw[0].sql
    assert raw[0].filename == "src/app.py"
    assert raw[0].diff_position == 1


def test_extracts_execute_call():
    content = 'cursor.execute("DELETE FROM sessions WHERE expires_at < NOW()")\n'
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "DELETE" in raw[0].sql


def test_skips_non_sql_strings():
    content = 'msg = "Hello, world! SELECT is a fine word"\n'
    cf = make_changed_file("src/app.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    # Should not extract non-SQL context strings that aren't valid SQL
    # (sqlglot parse will fail or produce no statements)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 0


def test_only_extracts_changed_lines():
    full_content = (
        'old = "SELECT id FROM orders"\n'
        'new = "SELECT name FROM users"\n'
    )
    # Only line 2 is "changed"
    cf = make_changed_file(
        "src/app.py",
        {2: 'new = "SELECT name FROM users"'},
        full_content=full_content,
    )
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "users" in raw[0].sql


def test_no_sqlalchemy_import_skips_orm(mocker):
    content = "result = session.query(User).filter(User.active == True).all()\n"
    cf = make_changed_file("src/app.py", {1: content.strip()}, full_content=content)
    # No sqlalchemy import in the file → ORM path not triggered
    queries = extract_queries([cf], anthropic_client=MagicMock())
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sql_extractor.py -v
```

Expected: `ImportError` — `sql_extractor.py` doesn't exist yet.

- [ ] **Step 3: Implement raw SQL extraction in `sql_reviewer/sql_extractor.py`**

```python
from __future__ import annotations
import ast
import json
import logging
from dataclasses import dataclass
from typing import Literal

import sqlglot

from sql_reviewer.diff_parser import ChangedFile

logger = logging.getLogger(__name__)

SQL_KEYWORDS = {"select", "insert", "update", "delete", "with"}


@dataclass
class ExtractedQuery:
    sql: str
    filename: str
    line_number: int
    diff_position: int | None  # None if no nearby changed line (ORM path)
    source: Literal["raw", "orm"]
    source_context: str = ""   # 5 lines before/after the query for Claude's prompt


def _is_valid_sql(text: str) -> bool:
    """Return True if sqlglot can parse the text as a SQL statement."""
    try:
        statements = sqlglot.parse(text, dialect="postgres")
        return bool(statements and statements[0] is not None)
    except Exception:
        return False


def _extract_sql_strings(source_code: str) -> list[tuple[int, str]]:
    """
    Walk the AST of Python source and return (line_number, sql_string) for
    string literals that look like SQL (contain a SQL keyword and parse cleanly).
    Also handles text("...") and execute("...") call patterns.
    """
    results: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return results

    for node in ast.walk(tree):
        # String literals
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            val = node.value.strip()
            if any(kw in val.lower().split() for kw in SQL_KEYWORDS):
                if _is_valid_sql(val):
                    results.append((node.lineno, val))

    return results


def _extract_raw_queries(changed_file: ChangedFile) -> list[ExtractedQuery]:
    changed_line_numbers = {cl.line_number for cl in changed_file.changed_lines}
    line_to_position = {cl.line_number: cl.diff_position for cl in changed_file.changed_lines}

    all_sql = _extract_sql_strings(changed_file.full_content)
    queries = []
    for line_num, sql in all_sql:
        if line_num not in changed_line_numbers:
            continue
        lines = changed_file.full_content.splitlines()
        start = max(0, line_num - 1 - 5)
        end = min(len(lines), line_num + 5)
        context = "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))
        queries.append(ExtractedQuery(
            sql=sql,
            filename=changed_file.filename,
            line_number=line_num,
            diff_position=line_to_position.get(line_num),
            source="raw",
            source_context=context,
        ))
    return queries


def _find_nearest_diff_position(
    line_number: int,
    changed_file: ChangedFile,
    window: int = 10,
) -> int | None:
    """Find the diff_position of the nearest changed line within `window` lines."""
    line_to_position = {cl.line_number: cl.diff_position for cl in changed_file.changed_lines}
    if line_number in line_to_position:
        return line_to_position[line_number]
    for delta in range(1, window + 1):
        if line_number - delta in line_to_position:
            return line_to_position[line_number - delta]
        if line_number + delta in line_to_position:
            return line_to_position[line_number + delta]
    return None


def _extract_orm_queries(
    changed_file: ChangedFile,
    anthropic_client,
) -> list[ExtractedQuery]:
    """Send changed SQLAlchemy code to Claude and get back inferred SQL."""
    if "sqlalchemy" not in changed_file.full_content:
        return []

    changed_line_numbers = {cl.line_number for cl in changed_file.changed_lines}
    lines = changed_file.full_content.splitlines()
    changed_code = "\n".join(
        f"{i+1}: {lines[i]}"
        for i in range(len(lines))
        if (i + 1) in changed_line_numbers
    )

    prompt = (
        "The following Python code uses SQLAlchemy. "
        "Extract the SQL queries this code would generate. "
        "Return ONLY a JSON array with objects containing 'sql' (the SQL string) "
        "and 'line_number' (the approximate source line). "
        "Use PostgreSQL dialect. If no queries can be extracted, return [].\n\n"
        f"Code:\n{changed_code}"
    )

    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        items = json.loads(raw)
    except Exception as e:
        logger.warning("ORM extraction failed for %s: %s", changed_file.filename, e)
        return []

    queries = []
    for item in items:
        sql = item.get("sql", "").strip()
        line_number = item.get("line_number", 0)
        if not sql:
            continue
        diff_position = _find_nearest_diff_position(line_number, changed_file)
        file_lines = changed_file.full_content.splitlines()
        start = max(0, line_number - 1 - 5)
        end = min(len(file_lines), line_number + 5)
        context = "\n".join(f"{i+1}: {file_lines[i]}" for i in range(start, end))
        queries.append(ExtractedQuery(
            sql=sql,
            filename=changed_file.filename,
            line_number=line_number,
            diff_position=diff_position,
            source="orm",
            source_context=context,
        ))
    return queries


def extract_queries(
    changed_files: list[ChangedFile],
    anthropic_client,
) -> list[ExtractedQuery]:
    results = []
    for cf in changed_files:
        results.extend(_extract_raw_queries(cf))
        if anthropic_client is not None:
            results.extend(_extract_orm_queries(cf, anthropic_client))
    return results
```

- [ ] **Step 4: Run raw SQL tests to verify they pass**

```bash
pytest tests/test_sql_extractor.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Add ORM extraction tests**

Append to `tests/test_sql_extractor.py`:

```python
def test_orm_extraction_with_sqlalchemy(mocker):
    content = (
        "from sqlalchemy import select\n"
        "stmt = select(User).where(User.active == True)\n"
        "result = session.execute(stmt)\n"
    )
    cf = make_changed_file("src/repo.py", {2: "stmt = select(User).where(User.active == True)"}, full_content=content)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='[{"sql": "SELECT * FROM users WHERE active = true", "line_number": 2}]')]
    )

    queries = extract_queries([cf], anthropic_client=mock_client)
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 1
    assert "SELECT" in orm[q].sql for q in range(len(orm))
    assert orm[0].diff_position == 2


def test_orm_extraction_malformed_json_skipped(mocker):
    content = "from sqlalchemy import select\nstmt = select(User)\n"
    cf = make_changed_file("src/repo.py", {2: "stmt = select(User)"}, full_content=content)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text="not valid json")]
    )

    queries = extract_queries([cf], anthropic_client=mock_client)
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 0


def test_orm_line_number_no_nearby_changed_line():
    content = "from sqlalchemy import select\n" + "\n" * 20 + "stmt = select(User)\n"
    # Changed line is at 22, Claude returns line_number=1 (far from any changed line)
    cf = make_changed_file("src/repo.py", {22: "stmt = select(User)"}, full_content=content)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='[{"sql": "SELECT * FROM users", "line_number": 1}]')]
    )

    queries = extract_queries([cf], anthropic_client=mock_client)
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 1
    assert orm[0].diff_position is None  # no changed line within 10 lines of line 1
```

- [ ] **Step 6: Fix the generator expression syntax error in the test (note: the `assert orm[q].sql` line above has a bug — replace it)**

The correct assertion for test_orm_extraction_with_sqlalchemy step 5:
```python
    assert all("SELECT" in q.sql for q in orm)
    assert orm[0].diff_position == 2
```

- [ ] **Step 7: Run all extractor tests**

```bash
pytest tests/test_sql_extractor.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 8: Update `sql_reviewer/__init__.py`**

```python
from sql_reviewer.config import Config, ConfigError
from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery

__all__ = ["Config", "ConfigError", "ChangedFile", "ChangedLine", "ExtractedQuery"]
```

- [ ] **Step 9: Commit**

```bash
git add sql_reviewer/sql_extractor.py sql_reviewer/__init__.py tests/test_sql_extractor.py
git commit -m "feat: add SQL extractor (raw strings + ORM inference via Claude)"
```

---

## Task 5: `explainer.py` — parameter substitution + EXPLAIN ANALYZE

**Files:**
- Create: `sql_reviewer/explainer.py`
- Create: `tests/test_explainer.py`

**Background:** Integration tests require a real Postgres instance. Run these with `DATABASE_URL` set. In CI, the Postgres service container provides it. Locally, set up a test DB.

Queries are wrapped in `BEGIN; EXPLAIN ANALYZE ...; ROLLBACK;` to prevent writes.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_explainer.py
"""
Integration tests — require a real Postgres instance.
Set DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review before running.
"""
import os
import pytest
import psycopg2
from sql_reviewer.diff_parser import ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import explain_queries, substitute_params, ExplainResult

DB_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:test@localhost:5432/sql_review")


@pytest.fixture(scope="module")
def db_conn():
    try:
        conn = psycopg2.connect(DB_URL)
        conn.autocommit = True
        yield conn
        conn.close()
    except Exception:
        pytest.skip("Postgres not available — set DATABASE_URL to run integration tests")


@pytest.fixture(scope="module", autouse=True)
def create_test_table(db_conn):
    with db_conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS test_users (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                active BOOLEAN NOT NULL DEFAULT true,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
    yield
    with db_conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS test_users")


def make_query(sql: str, line: int = 1) -> ExtractedQuery:
    return ExtractedQuery(
        sql=sql,
        filename="src/app.py",
        line_number=line,
        diff_position=line,
        source="raw",
    )


# --- Unit tests for parameter substitution (no DB needed) ---

def test_substitute_positional_params():
    sql = "SELECT * FROM users WHERE id = $1 AND created_at > $2"
    result = substitute_params(sql)
    assert "$1" not in result
    assert "$2" not in result
    assert "1" in result  # id heuristic
    assert "2024-01-01" in result  # created_at heuristic


def test_substitute_named_params():
    sql = "SELECT * FROM users WHERE is_active = :is_active AND user_id = :user_id"
    result = substitute_params(sql)
    assert ":is_active" not in result
    assert ":user_id" not in result


def test_substitute_psycopg2_style():
    sql = "SELECT * FROM t WHERE x = %s AND y = %(name)s"
    result = substitute_params(sql)
    assert "%s" not in result
    assert "%(name)s" not in result


def test_substitute_default_placeholder():
    sql = "SELECT * FROM users WHERE email = $1"
    result = substitute_params(sql)
    assert "$1" not in result


# --- Integration tests (require Postgres) ---

def test_explain_simple_select(db_conn):
    query = make_query("SELECT * FROM test_users WHERE active = true")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert isinstance(results[0], ExplainResult)
    assert "Seq Scan" in results[0].plan_text or "Index" in results[0].plan_text
    assert results[0].query is query


def test_explain_skips_invalid_sql(db_conn):
    queries = [
        make_query("SELECT * FROM test_users WHERE active = true", line=1),
        make_query("this is not sql at all", line=2),
    ]
    results = explain_queries(queries, DB_URL)
    assert len(results) == 1  # invalid query skipped
    assert results[0].query.line_number == 1


def test_explain_with_parameterized_query(db_conn):
    query = make_query("SELECT * FROM test_users WHERE id = $1 AND active = $2")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1
    assert results[0].plan_text  # got a plan back


def test_explain_write_query_does_not_persist(db_conn):
    query = make_query("INSERT INTO test_users (name, active) VALUES ('test', true)")
    results = explain_queries([query], DB_URL)
    assert len(results) == 1  # INSERT EXPLAIN ANALYZE works
    # Verify the row was NOT actually inserted (transaction rolled back)
    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM test_users WHERE name = 'test'")
        count = cur.fetchone()[0]
    assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review pytest tests/test_explainer.py -v
```

Expected: `ImportError` — `explainer.py` doesn't exist yet.

- [ ] **Step 3: Implement `sql_reviewer/explainer.py`**

```python
from __future__ import annotations
import logging
import re
from dataclasses import dataclass

import psycopg2

from sql_reviewer.sql_extractor import ExtractedQuery

logger = logging.getLogger(__name__)

STATEMENT_TIMEOUT_MS = 5000

# Heuristics: (substring_match, dummy_value)
_PARAM_HEURISTICS = [
    ({"id", "count", "num"}, "1"),
    ({"date", "time", "created", "updated"}, "'2024-01-01'"),
    ({"is_", "has_", "active", "enabled"}, "true"),
]
_DEFAULT_DUMMY = "'placeholder'"


def _dummy_value(param_name: str) -> str:
    name = param_name.lower()
    for keywords, value in _PARAM_HEURISTICS:
        if any(kw in name for kw in keywords):
            return value
    return _DEFAULT_DUMMY


def substitute_params(sql: str) -> str:
    """Replace parameter placeholders with type-appropriate dummy values."""
    # PostgreSQL positional: $1, $2, ...
    def replace_positional(m: re.Match) -> str:
        return _DEFAULT_DUMMY  # positional params have no name

    sql = re.sub(r"\$\d+", replace_positional, sql)

    # SQLAlchemy named: :param_name
    def replace_named(m: re.Match) -> str:
        return _dummy_value(m.group(1))

    sql = re.sub(r":([a-zA-Z_]\w*)", replace_named, sql)

    # psycopg2 named: %(name)s
    def replace_psycopg2_named(m: re.Match) -> str:
        return _dummy_value(m.group(1))

    sql = re.sub(r"%\(([^)]+)\)s", replace_psycopg2_named, sql)

    # psycopg2 positional: %s
    sql = sql.replace("%s", _DEFAULT_DUMMY)

    return sql


@dataclass
class ExplainResult:
    query: ExtractedQuery
    plan_text: str


def explain_queries(queries: list[ExtractedQuery], database_url: str) -> list[ExplainResult]:
    results: list[ExplainResult] = []

    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
    except Exception as e:
        logger.error("Could not connect to database: %s", e)
        raise

    try:
        for query in queries:
            sql = substitute_params(query.sql)
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout = {STATEMENT_TIMEOUT_MS}")
                    cur.execute(f"EXPLAIN ANALYZE {sql}")
                    plan_rows = cur.fetchall()
                    plan_text = "\n".join(row[0] for row in plan_rows)
                conn.rollback()
                results.append(ExplainResult(query=query, plan_text=plan_text))
            except Exception as e:
                logger.warning(
                    "EXPLAIN ANALYZE failed for query in %s line %d: %s",
                    query.filename, query.line_number, e,
                )
                conn.rollback()
    finally:
        conn.close()

    return results
```

- [ ] **Step 4: Start a local Postgres instance (if not already running)**

```bash
docker run -d --name sql-review-test \
  -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=sql_review \
  -p 5432:5432 \
  postgres:16
```

Expected: container starts successfully.

- [ ] **Step 5: Run integration tests**

```bash
DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review pytest tests/test_explainer.py -v
```

Expected: all 8 tests PASS (4 unit + 4 integration).

- [ ] **Step 6: Update `sql_reviewer/__init__.py`**

```python
from sql_reviewer.config import Config, ConfigError
from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import ExplainResult

__all__ = ["Config", "ConfigError", "ChangedFile", "ChangedLine", "ExtractedQuery", "ExplainResult"]
```

- [ ] **Step 7: Commit**

```bash
git add sql_reviewer/explainer.py sql_reviewer/__init__.py tests/test_explainer.py
git commit -m "feat: add explainer with param substitution and EXPLAIN ANALYZE"
```

---

## Task 6: `analyzer.py` — Claude analysis of execution plans

**Files:**
- Create: `sql_reviewer/analyzer.py`
- Create: `tests/test_analyzer.py`

**Background:** Groups `ExplainResult` objects by file into batches (max 10 queries or ~8,000 tokens of plan output per batch, estimated at 4 chars/token). Sends each batch to Claude as a single API call. Claude returns a JSON array of findings. `analyzer.py` resolves each finding's `line_number` back to a `diff_position` from the originating `ExtractedQuery`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_analyzer.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_analyzer.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `sql_reviewer/analyzer.py`**

```python
from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Literal

from sql_reviewer.explainer import ExplainResult

logger = logging.getLogger(__name__)

MAX_QUERIES_PER_BATCH = 10
MAX_PLAN_TOKENS_PER_BATCH = 8000
CHARS_PER_TOKEN = 4
CONTEXT_LINES = 5


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


def _build_batches(results: list[ExplainResult]) -> list[list[ExplainResult]]:
    """Split results into batches respecting MAX_QUERIES and token budget."""
    batches: list[list[ExplainResult]] = []
    current_batch: list[ExplainResult] = []
    current_tokens = 0

    for result in results:
        plan_tokens = len(result.plan_text) // CHARS_PER_TOKEN
        if current_batch and (
            len(current_batch) >= MAX_QUERIES_PER_BATCH
            or current_tokens + plan_tokens > MAX_PLAN_TOKENS_PER_BATCH
        ):
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(result)
        current_tokens += plan_tokens

    if current_batch:
        batches.append(current_batch)

    return batches


def _build_prompt(batch: list[ExplainResult]) -> str:
    parts = [
        "Review these SQL queries and their EXPLAIN ANALYZE output. "
        "Focus on structural issues: missing indexes, sequential scans on large tables, "
        "inefficient join strategies, unanchored LIKE patterns, unnecessary sorts. "
        "Do NOT comment on cost values — the database has no rows so cost estimates are meaningless. "
        "Return ONLY a JSON array. Each object must have: "
        "line_number (int), severity ('info'|'warning'|'critical'), "
        "summary (string), suggestion (string or null), has_suggestion (bool). "
        "If a query looks fine, omit it from the array. Return [] if no issues found.\n"
    ]

    for i, result in enumerate(batch, 1):
        query = result.query
        parts.append(f"\n--- Query {i} (file: {query.filename}, line: {query.line_number}) ---")
        if query.source_context:
            parts.append(f"Source context:\n{query.source_context}")
        parts.append(f"SQL:\n{query.sql}")
        parts.append(f"\nEXPLAIN ANALYZE:\n{result.plan_text}")

    return "\n".join(parts)


def _analyze_batch(
    batch: list[ExplainResult],
    anthropic_client,
) -> list[Finding]:
    # Build a lookup: line_number → ExplainResult (for diff_position resolution)
    line_to_result = {r.query.line_number: r for r in batch}

    prompt = _build_prompt(batch)
    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        items = json.loads(raw)
    except Exception as e:
        logger.warning("Analyzer batch failed: %s", e)
        return []

    findings = []
    for item in items:
        line_number = item.get("line_number")
        source_result = line_to_result.get(line_number)
        diff_position = source_result.query.diff_position if source_result else None
        plan_text = source_result.plan_text if source_result else ""

        findings.append(Finding(
            filename=batch[0].query.filename,
            line_number=line_number,
            diff_position=diff_position,
            severity=item.get("severity", "info"),
            summary=item.get("summary", ""),
            suggestion=item.get("suggestion"),
            has_suggestion=item.get("has_suggestion", False),
            plan_text=plan_text,
        ))

    return findings


def analyze_results(
    results: list[ExplainResult],
    anthropic_client,
) -> list[Finding]:
    # Group by filename first, then split each file's results into batches.
    # This ensures _analyze_batch always receives results from a single file,
    # so Finding.filename is always correct.
    from collections import defaultdict
    by_file: dict[str, list[ExplainResult]] = defaultdict(list)
    for result in results:
        by_file[result.query.filename].append(result)

    findings = []
    for file_results in by_file.values():
        for batch in _build_batches(file_results):
            findings.extend(_analyze_batch(batch, anthropic_client))
    return findings
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_analyzer.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Update `sql_reviewer/__init__.py`**

```python
from sql_reviewer.config import Config, ConfigError
from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import ExtractedQuery
from sql_reviewer.explainer import ExplainResult
from sql_reviewer.analyzer import Finding

__all__ = [
    "Config", "ConfigError",
    "ChangedFile", "ChangedLine",
    "ExtractedQuery",
    "ExplainResult",
    "Finding",
]
```

- [ ] **Step 6: Commit**

```bash
git add sql_reviewer/analyzer.py sql_reviewer/__init__.py tests/test_analyzer.py
git commit -m "feat: add analyzer with Claude batching and Finding resolution"
```

---

## Task 7: `commenter.py` — post inline review comments

**Files:**
- Create: `sql_reviewer/commenter.py`
- Create: `tests/test_commenter.py`

**Background:** Two API paths — (1) delete any existing `<!-- sql-reviewer -->` inline comments from previous runs, (2a) post a PR review with inline comments if findings exist, (2b) post a plain issue comment if no findings. The reviews API requires at least one inline comment, so "no issues" uses the issues comments endpoint.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_commenter.py
import pytest
import respx
import httpx
from sql_reviewer.analyzer import Finding
from sql_reviewer.commenter import post_findings, _build_comment_body, MARKER

REPO = "owner/repo"
PR_NUMBER = 7
TOKEN = "ghtoken"
BASE = "https://api.github.com"


def make_finding(line: int = 5, diff_pos: int = 5, severity: str = "warning") -> Finding:
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_commenter.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `sql_reviewer/commenter.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_commenter.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add sql_reviewer/commenter.py tests/test_commenter.py
git commit -m "feat: add commenter with inline review posting and old comment cleanup"
```

---

## Task 8: `main.py` — pipeline orchestration

**Files:**
- Create: `sql_reviewer/main.py`

**Background:** Reads env vars (`REPO`, `PR_NUMBER`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_URL`), loads config, runs `schema_file` or `setup_command`, then runs the full pipeline. Exits 0 unless configuration/setup fails.

- [ ] **Step 1: Write a smoke test**

```python
# tests/test_main.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_main.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Implement `sql_reviewer/main.py`**

```python
from __future__ import annotations
import logging
import os
import subprocess
import sys
from pathlib import Path

import anthropic

from sql_reviewer.analyzer import analyze_results
from sql_reviewer.commenter import post_findings
from sql_reviewer.config import ConfigError, load_config
from sql_reviewer.diff_parser import fetch_changed_files
from sql_reviewer.explainer import explain_queries
from sql_reviewer.sql_extractor import extract_queries

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        logger.error("Required environment variable %s is not set", name)
        sys.exit(1)
    return value


def _run_schema_setup(config_path: Path, schema_file: str | None, setup_command: str | None) -> None:
    if schema_file:
        schema_path = config_path.parent / schema_file
        if not schema_path.exists():
            logger.error("schema_file not found: %s", schema_path)
            sys.exit(1)
        database_url = os.environ["DATABASE_URL"]
        cmd = f"psql {database_url} -f {schema_path}"
        logger.info("Applying schema: %s", cmd)
    else:
        cmd = setup_command

    result = subprocess.run(cmd, shell=True, env=os.environ.copy())
    if result.returncode != 0:
        logger.error("Schema setup failed (exit %d)", result.returncode)
        sys.exit(1)


def main() -> None:
    repo = _require_env("REPO")
    pr_number_str = _require_env("PR_NUMBER")
    token = _require_env("GITHUB_TOKEN")
    anthropic_api_key = _require_env("ANTHROPIC_API_KEY")
    database_url = _require_env("DATABASE_URL")

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        logger.error("PR_NUMBER must be an integer, got: %s", pr_number_str)
        sys.exit(1)

    # 1. Load config
    config_path = Path(".sql-reviewer.yml")
    try:
        config = load_config(config_path)
    except ConfigError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    # 2. Run schema setup
    _run_schema_setup(config_path, config.schema_file, config.setup_command)

    # 3. Fetch PR diff
    logger.info("Fetching PR #%d diff from %s", pr_number, repo)
    try:
        changed_files = fetch_changed_files(repo, pr_number, token, config.file_patterns)
    except Exception as e:
        logger.error("Failed to fetch PR diff: %s", e)
        sys.exit(1)

    if not changed_files:
        logger.info("No matching Python files changed in PR #%d — nothing to review", pr_number)
        sys.exit(0)

    # 4. Extract SQL queries
    anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
    queries = extract_queries(changed_files, anthropic_client)
    logger.info("Extracted %d SQL quer%s", len(queries), "y" if len(queries) == 1 else "ies")

    if not queries:
        logger.info("No SQL queries found in changed files — nothing to review")
        post_findings([], repo, pr_number, token, total_queries=0)
        sys.exit(0)

    # 5. Run EXPLAIN ANALYZE
    explain_results = explain_queries(queries, database_url)
    logger.info("%d/%d queries explained successfully", len(explain_results), len(queries))

    # 6. Analyze with Claude
    findings = analyze_results(explain_results, anthropic_client)
    logger.info("Found %d issue%s", len(findings), "" if len(findings) == 1 else "s")

    # 7. Post review comments
    try:
        post_findings(findings, repo, pr_number, token, total_queries=len(queries))
    except Exception as e:
        logger.error("Failed to post review comments: %s", e)
        sys.exit(1)

    sys.exit(0)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_main.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add sql_reviewer/main.py tests/test_main.py
git commit -m "feat: add main orchestrator with env var validation and pipeline wiring"
```

---

## Task 9: `workflow-template.yml`, `README.md`, and sample fixtures

**Files:**
- Create: `tests/fixtures/sample_schema.sql`
- Create: `tests/fixtures/.sql-reviewer.yml`
- Create: `workflow-template.yml`
- Create: `README.md`

- [ ] **Step 1: Create `tests/fixtures/sample_schema.sql`**

A realistic schema used for local end-to-end testing. The integration tests in `test_explainer.py` create their own tables via pytest fixtures and don't need this file — it exists solely to support running `python -m sql_reviewer` locally against a real PR.

```sql
-- tests/fixtures/sample_schema.sql
-- Sample schema for local end-to-end testing of sql_reviewer.
-- Load with: psql $DATABASE_URL -f tests/fixtures/sample_schema.sql

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email    ON users (email);
CREATE INDEX IF NOT EXISTS idx_users_active   ON users (is_active) WHERE is_active = true;

CREATE TABLE IF NOT EXISTS orders (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',
    total_cents INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders (user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders (status);

CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1,
    unit_cents INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order_id ON order_items (order_id);
```

- [ ] **Step 2: Create `tests/fixtures/.sql-reviewer.yml`**

```yaml
# tests/fixtures/.sql-reviewer.yml
# Copy to your project root and edit for local end-to-end testing.
schema_file: tests/fixtures/sample_schema.sql
file_patterns:
  - "src/**/*.py"
  - "app/**/*.py"
```

- [ ] **Step 3: Update `README.md` local testing section to reference the fixtures** (add after the "Running tests" section)

Add this to the README content below (see Step 5 for the full README).

- [ ] **Step 4: Create `workflow-template.yml`**

```yaml
# workflow-template.yml
# Copy this file to .github/workflows/sql-review.yml in your repo.
# Required secrets: ANTHROPIC_API_KEY (add in repo Settings → Secrets and variables → Actions)
# GITHUB_TOKEN is provided automatically by GitHub Actions.

name: SQL Review

on:
  pull_request:
    types: [opened]        # runs once when PR is first opened
  workflow_dispatch:
    inputs:
      pr_number:
        description: "PR number to review (for manual re-runs)"
        required: true
        type: string

permissions:
  pull-requests: write     # post and delete inline review comments
  contents: read           # fetch file content from the PR branch

jobs:
  sql-review:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_DB: sql_review
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install sql_reviewer
        run: pip install https://github.com/YOUR_ORG/explain-analyze-agent/archive/main.tar.gz

      - name: Run SQL review
        run: python -m sql_reviewer
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/sql_review
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number || inputs.pr_number }}
          REPO: ${{ github.repository }}
```

- [ ] **Step 5: Create `README.md`**

```markdown
# SQL Review Action

Automatically runs `EXPLAIN ANALYZE` on SQL queries in pull request diffs and posts inline review comments with improvement suggestions.

## What it does

1. Triggered when a PR is opened
2. Extracts SQL from changed Python files (raw strings and SQLAlchemy ORM queries)
3. Runs `EXPLAIN ANALYZE` against a Postgres schema loaded from your `schema_file`
4. Sends execution plans to Claude for structural analysis
5. Posts inline review comments on the affected lines

## Setup

**1. Add the workflow to your repo**

Copy `workflow-template.yml` to `.github/workflows/sql-review.yml` in your repo. Update the `pip install` URL to point to this repo.

**2. Add `.sql-reviewer.yml` to your repo root**

```yaml
# Point to your schema file (recommended):
schema_file: db/model_ddl.sql

# Or use a custom command (for migration-only setups):
# setup_command: "alembic upgrade head"

file_patterns:
  - "src/**/*.py"
  - "app/**/*.py"
```

**3. Add `ANTHROPIC_API_KEY` to your repo secrets**

Go to Settings → Secrets and variables → Actions → New repository secret.

## Running tests

```bash
pip install -e ".[dev]"

# Unit tests (no database required):
pytest tests/ -v -k "not test_explain"

# Integration tests (requires Postgres):
DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review pytest tests/ -v
```

## Local end-to-end testing

To run the tool locally against a real PR:

```bash
# 1. Start Postgres and load the sample schema
docker run -d --name sql-review-local \
  -e POSTGRES_PASSWORD=test -e POSTGRES_DB=sql_review \
  -p 5432:5432 postgres:16
psql postgresql://postgres:test@localhost:5432/sql_review -f tests/fixtures/sample_schema.sql

# 2. Copy the sample config to your project root and edit file_patterns
cp tests/fixtures/.sql-reviewer.yml .sql-reviewer.yml

# 3. Set env vars (or use a .env file with `export $(cat .env | xargs)`)
export ANTHROPIC_API_KEY=sk-ant-...
export GITHUB_TOKEN=github_pat_...   # needs pull-requests:write + contents:read
export REPO=owner/your-repo
export PR_NUMBER=123
export DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review

# 4. Run
python -m sql_reviewer
```

## Configuration reference

| Key | Required | Description |
|---|---|---|
| `schema_file` | One of these | Path to a SQL file with your full schema (`CREATE TABLE`, `CREATE INDEX`, etc.) |
| `setup_command` | One of these | Shell command to set up the schema (e.g. `alembic upgrade head`) |
| `file_patterns` | Yes | Glob patterns for Python files to scan |
```

- [ ] **Step 6: Run the full test suite one final time**

```bash
DATABASE_URL=postgresql://postgres:test@localhost:5432/sql_review pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add tests/fixtures/ workflow-template.yml README.md
git commit -m "feat: add workflow template, README, and sample fixtures for local testing"
```
