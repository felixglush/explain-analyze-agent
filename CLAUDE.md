# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

When working with Python, invoke the relevant /astral:<skill> for uv, ty, and ruff to ensure best practices are followed.

## Project

This is a GitHub Action (`sql-reviewer`) that reviews SQL queries in PRs by running EXPLAIN ANALYZE against a real PostgreSQL database and posting findings as review comments via the Anthropic API.

## Commands

- **Run tests**: `uv run pytest --cov --cov-report=term-missing` (requires `DATABASE_URL` env var)
- **Run a single test**: `uv run pytest tests/test_explainer.py::test_name -v`
- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format .`
- **Install deps**: `uv sync --extra dev`

## Architecture

Single package `sql_reviewer/` orchestrated by `main.py` in this order:

1. **`config.py`** — loads `.sql-reviewer.yml` from the consuming repo root; requires exactly one of `schema_file` or `setup_command`
2. **`diff_parser.py`** — fetches PR file list and full content via GitHub API; computes `diff_position` (1-based offset within the unified diff hunk, required by GitHub's inline comment API) for each changed line
3. **`sql_extractor.py`** — two paths: `sqlglot` detects raw SQL strings; Claude infers SQL from SQLAlchemy ORM code. Returns `ExtractedQuery` objects with `diff_position` for comment placement
4. **`explainer.py`** — substitutes dummy params, wraps each query in `BEGIN; EXPLAIN ANALYZE ...; ROLLBACK;` with a 5s statement timeout, connects via `psycopg2`. Skips individual failures rather than aborting
5. **`analyzer.py`** — batches queries per file (max 10 queries or ~8k tokens of plan output), sends to Claude with surrounding code context, returns `Finding` objects with `diff_position` resolved
6. **`commenter.py`** — deletes previous `<!-- sql-reviewer -->` comments, then posts a single `COMMENT`-type PR review with all inline findings. If no issues, posts a plain issue comment (reviews API requires at least one inline comment)

The package is invoked as `python -m sql_reviewer` via `__main__.py`. Required env vars: `REPO`, `PR_NUMBER`, `GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, `DATABASE_URL`.

Key design decisions:
- `diff_position` (not line number) is used throughout the pipeline because GitHub's review comment API requires it for inline placement. If `diff_position` cannot be resolved (e.g. Claude returns a line number not in `changed_lines`), the finding is skipped — never causes a crash.
- The Postgres instance is schema-only (no rows), so EXPLAIN ANALYZE row estimates are meaningless. Claude is explicitly instructed to focus on structural issues (missing indexes, sequential scans, unanchored LIKE) rather than cost values.
- Non-fatal failures (individual query EXPLAIN errors, Claude API errors, malformed JSON) are logged and skipped so the workflow step exits 0 and never blocks a PR merge. Only misconfiguration (missing config, schema setup failure, GitHub API failures) exits 1.

## Testing conventions

- Integration tests (`test_explainer.py`) hit a real PostgreSQL database — do not mock the DB connection
- GitHub API calls are mocked with `respx`; Claude API and other dependencies use `pytest-mock`
- Coverage minimum is 80% (`fail_under = 80` in pyproject.toml)
- `test_main.py` patches `fetch_changed_files` directly on the `sql_reviewer.main` module (not the source module) due to the try/except import guard in `main.py`

## Style

- Line length: 110 (configured in `[tool.ruff]`)
- Active ruff rules: E, F, I (isort), UP (pyupgrade)
