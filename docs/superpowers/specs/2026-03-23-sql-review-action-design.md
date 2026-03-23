# SQL Review GitHub Action — Design Spec

**Date:** 2026-03-23

## Overview

A Python tool that runs as a GitHub Actions step after a PR is opened or updated. It extracts SQL queries from changed Python files (raw strings and SQLAlchemy ORM code), runs `EXPLAIN ANALYZE` on each query against a Postgres service container, sends the execution plans to Claude for analysis, and posts inline review comments with improvement suggestions.

Engineers open PRs as normal. The tool handles everything automatically — no @mention, no extra steps.

---

## Architecture

Single Python package (`sql_reviewer/`) with five internal modules, invoked as one step in a GitHub Actions workflow.

```
PR opened/updated
    │
    ▼
GitHub Actions workflow
    │
    ├─ 1. Spin up Postgres service container (schema only)
    ├─ 2. Run setup_command (read from .sql-reviewer.yml by main.py)
    └─ 3. Run python -m sql_reviewer
            │
            ├── config.py        — load .sql-reviewer.yml
            ├── diff_parser.py   — fetch PR diff via GitHub API, map changed lines
            ├── sql_extractor.py — detect raw SQL strings; Claude infers SQL from ORM code
            ├── explainer.py     — substitute dummy params, run EXPLAIN ANALYZE via psycopg2
            ├── analyzer.py      — send query + plan to Claude, get structured suggestions
            └── commenter.py     — post inline review comments via GitHub API
```

---

## Components

### `config.py`

Loads `.sql-reviewer.yml` from the consuming repo root.

```yaml
# Option A — simple (recommended): point to a SQL schema file
schema_file: db/model_ddl.sql

# Option B — override for complex setups (e.g. migration-only projects):
# setup_command: "python manage.py migrate"

file_patterns:
  - "src/**/*.py"
  - "app/**/*.py"
```

- `schema_file`: path to a SQL file containing the full current schema (CREATE TABLE, CREATE INDEX, etc.). `main.py` runs `psql $DATABASE_URL -f <schema_file>` before the pipeline. This is the recommended option — most projects maintain a `model_ddl.sql` or `schema.sql` that reflects the current state.
- `setup_command`: alternative to `schema_file` for projects without a single schema file (e.g. migration-only setups). Executed as a subprocess inheriting the full runner environment, so `PATH` and all secrets are available alongside `DATABASE_URL`. Exactly one of `schema_file` or `setup_command` must be set; `main.py` exits 1 if neither or both are present.
- `file_patterns`: glob patterns for files to scan.

### `diff_parser.py`

- Fetches the PR file list via `GET /repos/{owner}/{repo}/pulls/{pr_number}/files`
- Filters to files matching configured `file_patterns`
- Fetches full file content from the PR branch for each matched file
- For inline comment placement, records the **diff hunk position** (the line's 1-based offset within the unified diff, as required by the GitHub Pull Request Review API's `position` field) alongside each changed line
- Returns a list of `ChangedFile` objects:
  ```python
  @dataclass
  class ChangedLine:
      line_number: int      # absolute line number in the file
      diff_position: int    # position within the diff hunk (for GitHub API)
      content: str

  @dataclass
  class ChangedFile:
      filename: str
      full_content: str
      changed_lines: list[ChangedLine]
  ```

### `sql_extractor.py`

Two extraction paths:

**Raw SQL detection:**
- Uses `sqlglot` to detect SQL strings in Python source
- Matches: string literals with SQL keywords (`SELECT`, `INSERT`, `UPDATE`, `DELETE`), `text()` calls, `execute()` calls
- Only extracts from changed lines to avoid reviewing unmodified code
- Records the source file and `diff_position` for each extracted query
- Known limitation: Python string literals containing SQL keywords in non-SQL contexts (log messages, docstrings, test fixture strings) may be false-positively extracted. These will typically fail `EXPLAIN ANALYZE` and be skipped by the error handler in `explainer.py`.

**ORM → SQL inference:**
- For files containing `sqlalchemy` imports, sends changed code blocks to Claude
- Claude is prompted to return a JSON array:
  ```json
  [
    {
      "sql": "SELECT id, name FROM users WHERE active = true",
      "line_number": 42
    }
  ]
  ```
- The `line_number` is used to look up the corresponding `diff_position` from the `ChangedFile` data. If Claude returns a `line_number` that does not appear in `changed_lines` (e.g., it anchored the query to an unchanged line earlier in the block), `sql_extractor.py` uses the nearest changed line's `diff_position` as a fallback. If no changed line exists within 10 lines in either direction, the query is assigned `diff_position = None` and will be skipped by `commenter.py` (no comment posted for that query — the finding is still logged).
- Claude response is parsed as JSON; malformed responses are logged and skipped

Returns a list of `ExtractedQuery` objects:
```python
@dataclass
class ExtractedQuery:
    sql: str
    filename: str
    line_number: int
    diff_position: int | None  # None if no nearby changed line found (ORM path)
    source: Literal["raw", "orm"]
```

### `explainer.py`

**Parameter substitution:**
Replaces placeholders before execution. Substitution is best-effort; if a dummy value causes a type error at execution time, the query is logged and skipped (handled by the existing error handler below).

| Placeholder style | Example |
|---|---|
| `$1`, `$2` | PostgreSQL positional |
| `:param_name` | SQLAlchemy named |
| `%s`, `%(name)s` | psycopg2 style |

Heuristics for dummy values based on column/parameter name:
- Contains `id`, `count`, `num` → `1`
- Contains `date`, `time`, `created`, `updated` → `'2024-01-01'`
- Contains `is_`, `has_`, `active`, `enabled` → `true`
- Default → `'placeholder'`

**Output:** Returns a list of `ExplainResult` objects:
```python
@dataclass
class ExplainResult:
    query: ExtractedQuery   # the original extracted query
    plan_text: str          # full EXPLAIN ANALYZE output as plain text
```
Queries that fail execution are logged and excluded from the returned list.

**EXPLAIN ANALYZE execution:**
- Connects via `psycopg2` using `DATABASE_URL`
- Wraps each query: `BEGIN; EXPLAIN ANALYZE <query>; ROLLBACK;` to prevent writes from persisting
- Statement timeout: 5 seconds per query
- Captures plan output as plain text (not JSON)
- On failure (syntax error, missing table, type mismatch, timeout): logs the error, skips the query, continues

**Known limitation:** With a schema-only database and no rows, Postgres row estimates will be 0 or 1 for all queries. Cost-based comparisons are not meaningful in this context. The analysis prompt (see `analyzer.py`) instructs Claude to focus on structural issues — missing indexes, sequential scans on indexed columns, unanchored `LIKE` patterns, unnecessary sorts — rather than absolute cost values.

### `analyzer.py`

For each query with a successful execution plan, Claude receives:
- The original SQL query
- 5 lines before and after the query's `line_number` from `full_content` (capped at file boundaries)
- The full `EXPLAIN ANALYZE` output
- An instruction to focus on structural issues, not cost estimates (see known limitation above)

Note: the ~8,000 token batch budget covers plan output only. Context lines add roughly 500–1,000 tokens per query and are not counted against the batch limit. Batches may therefore exceed 8,000 tokens in practice; this is acceptable given Claude's context window.

**Batching:** Queries from the same file are grouped into a single Claude API call, up to a maximum of 10 queries or ~8,000 tokens of plan output per batch (whichever is reached first). Token count is estimated at 4 characters per token (no tokenizer dependency required). Queries exceeding this per-file limit are split into additional batches.

**Response format:** Claude is prompted to return a JSON array:
```json
[
  {
    "line_number": 42,
    "severity": "warning",
    "summary": "Sequential scan on users table — consider an index on active",
    "suggestion": "CREATE INDEX idx_users_active ON users (active) WHERE active = true;",
    "has_suggestion": true
  }
]
```

Response is parsed as JSON. On malformed response or API error: log the error, skip the batch, continue.

Returns a list of `Finding` objects. `analyzer.py` resolves each `Finding.line_number` back to a `diff_position` before returning, by matching against the `ExtractedQuery` objects in the batch (keyed on `line_number`). If `line_number` from Claude does not match any query in the batch, `diff_position` is set to `None` and the finding is skipped in `commenter.py`.

```python
@dataclass
class Finding:
    filename: str
    line_number: int          # from Claude's JSON response
    diff_position: int | None # resolved from ExtractedQuery; None = skip
    severity: Literal["info", "warning", "critical"]
    summary: str
    suggestion: str | None    # None when has_suggestion is false
    has_suggestion: bool
    plan_text: str            # raw EXPLAIN ANALYZE output (for comment details block)
```

### `commenter.py`

- Cleans up comments from previous runs before posting: lists all existing PR review comments via `GET /repos/{owner}/{repo}/pulls/{pr_number}/comments`, filters to those whose body contains the `<!-- sql-reviewer -->` marker, and deletes each via `DELETE /repos/{owner}/{repo}/pulls/comments/{comment_id}`. (Note: the GitHub dismiss endpoint does not work for `COMMENT`-type reviews, so individual comment deletion is used instead.)
- Uses the GitHub Pull Request Review API: `POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews`
- Creates a single review submission with multiple inline comments (one per finding)
- Each comment uses the `diff_position` field to place it on the correct line
- Comment body format:
  ```
  <!-- sql-reviewer -->
  ⚠️ **warning** — Sequential scan on users table

  Consider an index on `active`:
  ```sql
  CREATE INDEX idx_users_active ON users (active) WHERE active = true;
  ```

  <details>
  <summary>EXPLAIN ANALYZE output</summary>

  ```
  Seq Scan on users  (cost=0.00..1.00 rows=1 width=...)
  ...
  ```
  </details>
  ```
- If issues found: submits a single review via `POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews` with type `COMMENT` and all inline comments in one request
- If no issues found: posts a plain PR comment via `POST /repos/{owner}/{repo}/issues/{pr_number}/comments` with body `<!-- sql-reviewer --> SQL Review: no issues found in N queries analyzed`. Uses the issues comments endpoint (not the reviews API) because the reviews API requires at least one inline comment.
- Review type: `COMMENT` (not `APPROVE` or `REQUEST_CHANGES`) — suggestions only, never blocking

---

## GitHub Actions Workflow

### Trigger

```yaml
on:
  pull_request:
    types: [opened]
  workflow_dispatch:
    inputs:
      pr_number:
        description: "PR number to review"
        required: true
        type: string
```

Runs automatically when a PR is first opened. Does not re-run on subsequent commits. `workflow_dispatch` allows manual re-runs against an existing PR by specifying its number.

### Permissions

```yaml
permissions:
  pull-requests: write   # required to post and delete inline review comments
  contents: read         # required to fetch file content from the PR branch (private repos)
```

Without `pull-requests: write`, comment posting will receive a 403. Without `contents: read`, fetching file content via the GitHub API will fail on private repositories.

### Job structure

```yaml
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
        run: pip install .
      - name: Run SQL review
        run: python -m sql_reviewer
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/sql_review
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number || inputs.pr_number }}
          REPO: ${{ github.repository }}
```

`main.py` reads `.sql-reviewer.yml`, runs `setup_command` as a subprocess (with `DATABASE_URL` in the environment) to apply the schema, then runs the pipeline. This means the workflow YAML itself never needs to reference `setup_command` directly — it is fully encapsulated in the Python tool.

**`main.py` orchestration order:**
1. Load config from `.sql-reviewer.yml`
2. Run `setup_command` as a subprocess (exit 1 on non-zero)
3. `diff_parser`: fetch PR diff and changed file contents
4. `sql_extractor`: extract raw SQL and infer ORM SQL (returns `list[ExtractedQuery]`)
5. `explainer`: run EXPLAIN ANALYZE on each query (returns `list[ExplainResult]`, skipping failures)
6. `analyzer`: send queries + plans to Claude in batches (returns `list[Finding]` with `diff_position` resolved)
7. `commenter`: delete previous sql-reviewer comments, post new inline comments

### Secrets required (consuming repo)

| Secret | Source |
|---|---|
| `ANTHROPIC_API_KEY` | Added manually to repo secrets |
| `GITHUB_TOKEN` | Provided automatically by GitHub Actions |
| `DATABASE_URL` | Constructed inline in the workflow |

---

## Pipeline-Level Error Handling

| Failure | Behavior |
|---|---|
| `.sql-reviewer.yml` missing or invalid | Exit 1 with clear error message |
| Neither `schema_file` nor `setup_command` set (or both set) | Exit 1 with clear error message |
| `schema_file` path does not exist | Exit 1 with clear error message |
| `schema_file` / `setup_command` exits non-zero | Exit 1 — schema setup failed, cannot continue |
| GitHub API call fails (diff fetch, comment post) | Exit 1 with error details |
| PR not found or inaccessible (bad `pr_number`, wrong repo) | Exit 1 with error details |
| No Python files changed matching `file_patterns` | Exit 0 silently (nothing to review) |
| Claude API call fails or returns malformed JSON | Log warning, skip affected queries/batch, continue |
| Individual query fails EXPLAIN ANALYZE | Log warning, skip that query, continue |

The workflow step exits 0 in all non-fatal cases so that SQL review failures never block a PR merge. Fatal exits (Exit 1) are reserved for configuration/setup problems that indicate the tool is misconfigured.

---

## Project Structure

```
explain-analyze-agent/
├── sql_reviewer/
│   ├── __init__.py          # re-exports public dataclasses: ChangedFile, ChangedLine, ExtractedQuery, ExplainResult, Finding
│   ├── __main__.py          # enables `python -m sql_reviewer`; calls main()
│   ├── main.py              # orchestrates the pipeline
│   ├── config.py            # load .sql-reviewer.yml
│   ├── diff_parser.py       # PR diff fetching and line mapping
│   ├── sql_extractor.py     # raw SQL detection + ORM→SQL via Claude
│   ├── explainer.py         # param substitution + EXPLAIN ANALYZE
│   ├── analyzer.py          # Claude analysis of execution plans
│   └── commenter.py         # GitHub inline review comments
├── workflow-template.yml    # complete copy-paste workflow for consuming repos; mirrors the inline workflow YAML in this spec, with comments explaining each env var and secret
├── pyproject.toml           # package config and dependencies
├── tests/
│   ├── test_diff_parser.py  # unit tests with mocked GitHub API responses
│   ├── test_sql_extractor.py
│   ├── test_explainer.py    # integration tests against a real Postgres instance
│   ├── test_analyzer.py     # unit tests with mocked Claude API responses
│   └── test_commenter.py    # unit tests with mocked GitHub API responses
└── README.md
```

**Testing strategy:** Unit tests mock the GitHub and Claude APIs using `pytest` fixtures. `test_explainer.py` runs against a real Postgres instance (expected to be available via `DATABASE_URL` in the test environment — provided by a Postgres service in CI, or a local instance for local test runs).

### Dependencies

| Package | Purpose |
|---|---|
| `psycopg2-binary` | Postgres connection |
| `anthropic` | Claude API (extraction + analysis) |
| `httpx` | GitHub API calls |
| `sqlglot` | SQL string detection and parsing |
| `PyYAML` | `.sql-reviewer.yml` config loading |

---

## Out of Scope (v1)

- Local CLI mode
- Seed data (schema-only Postgres setup)
- Non-Python file scanning
- Support for database engines other than PostgreSQL
- Reusable action packaging (`action.yml` / Dockerfile)
- Hard blocking of PRs (`REQUEST_CHANGES` review type)
