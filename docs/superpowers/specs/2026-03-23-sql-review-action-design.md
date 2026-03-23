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
    ├─ 2. Run setup_command from .sql-reviewer.yml
    └─ 3. Run sql_reviewer
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
setup_command: "psql $DATABASE_URL -f db/setup.sql"
file_patterns:
  - "src/**/*.py"
  - "app/**/*.py"
postgres_version: "16"
```

- `setup_command`: shell command to apply schema to the Postgres container
- `file_patterns`: glob patterns for files to scan
- `postgres_version`: Postgres Docker image version for the service container

### `diff_parser.py`

- Fetches the PR file list via `GET /repos/{owner}/{repo}/pulls/{pr_number}/files`
- Filters to files matching configured `file_patterns`
- Fetches full file content from the PR branch for each matched file
- Returns a mapping of `{filename: {line_number: line_content}}` for changed lines only (using diff hunk position data for accurate inline comment placement)

### `sql_extractor.py`

Two extraction paths:

**Raw SQL detection:**
- Uses `sqlglot` to detect SQL strings in Python source
- Matches: string literals with SQL keywords (`SELECT`, `INSERT`, `UPDATE`, `DELETE`), `text()` calls, `execute()` calls
- Only extracts from changed lines to avoid reviewing unmodified code
- Records the source file and line number for each extracted query

**ORM → SQL inference:**
- For files containing `sqlalchemy` imports, sends changed code blocks to Claude
- Prompt: *"Extract the SQL queries this Python code would generate. Return each as a standalone SQL statement with no parameters if possible. For each query, note the approximate line number in the source."*
- Claude returns inferred SQL statements, which are tagged with their source location

### `explainer.py`

**Parameter substitution:**
Replaces placeholders before execution:

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

**EXPLAIN ANALYZE execution:**
- Connects via `psycopg2` using `DATABASE_URL`
- Wraps each query: `BEGIN; EXPLAIN ANALYZE <query>; ROLLBACK;` to prevent writes from persisting
- Statement timeout: 5 seconds per query
- Captures plan output as plain text (not JSON)
- On failure (syntax error, missing table, timeout): logs the error, skips the query, continues

### `analyzer.py`

For each query with a successful execution plan, sends a prompt to Claude containing:
- The original SQL query
- A few lines of surrounding source code context
- The full `EXPLAIN ANALYZE` output

Claude is asked to identify and return structured findings:
- **Severity**: `info` | `warning` | `critical`
- **Summary**: one-line description of the issue
- **Suggestion**: recommended fix, with example SQL where applicable

Queries from the same file are batched into a single API call to minimize latency and cost.

### `commenter.py`

- Uses the GitHub Pull Request Review API: `POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews`
- Creates a single review submission with multiple inline comments (one per finding)
- Each comment is placed at the line where the query was found in the diff
- Comment body format:
  - Severity badge (`⚠️ warning` / `🔴 critical` / `ℹ️ info`)
  - Summary of the issue
  - Suggestion (with SQL example if provided)
  - Collapsible `<details>` block with raw `EXPLAIN ANALYZE` output
- If no issues found: posts a single comment — "SQL Review: no issues found in N queries analyzed"
- Review type: `COMMENT` (not `APPROVE` or `REQUEST_CHANGES`) — suggestions only, never blocking

---

## GitHub Actions Workflow

### Trigger

```yaml
on:
  pull_request:
    types: [opened, synchronize]
  workflow_dispatch:
```

Runs automatically on new PRs and when commits are pushed to an existing PR. `workflow_dispatch` allows manual re-runs for testing against existing PRs.

### Job structure

```yaml
jobs:
  sql-review:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:${{ inputs.postgres_version || '16' }}
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
      - name: Apply schema
        run: ${{ setup_command from .sql-reviewer.yml }}
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/sql_review
      - name: Run SQL review
        run: python -m sql_reviewer
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/sql_review
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
          REPO: ${{ github.repository }}
```

### Secrets required (consuming repo)

| Secret | Source |
|---|---|
| `ANTHROPIC_API_KEY` | Added manually to repo secrets |
| `GITHUB_TOKEN` | Provided automatically by GitHub Actions |
| `DATABASE_URL` | Constructed inline in the workflow |

---

## Project Structure

```
explain-analyze-agent/
├── sql_reviewer/
│   ├── __init__.py
│   ├── main.py              # orchestrates the pipeline
│   ├── config.py            # load .sql-reviewer.yml
│   ├── diff_parser.py       # PR diff fetching and line mapping
│   ├── sql_extractor.py     # raw SQL detection + ORM→SQL via Claude
│   ├── explainer.py         # param substitution + EXPLAIN ANALYZE
│   ├── analyzer.py          # Claude analysis of execution plans
│   └── commenter.py         # GitHub inline review comments
├── workflow-template.yml    # example workflow for consuming repos
├── pyproject.toml           # package config and dependencies
├── tests/
└── README.md
```

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
