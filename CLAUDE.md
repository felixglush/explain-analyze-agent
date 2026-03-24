When working with Python, invoke the relevant /astral:<skill> for uv, ty, and ruff to ensure best practices are followed.

## Project
This is a GitHub Action (`sql-reviewer`) that reviews SQL queries in PRs by running EXPLAIN ANALYZE against a real PostgreSQL database and posting findings as review comments via the Anthropic API.

## Commands
- **Run tests**: `uv run pytest --cov --cov-report=term-missing` (requires `DATABASE_URL` env var pointing to a live Postgres instance)
- **Lint**: `uv run ruff check .`
- **Format**: `uv run ruff format .`
- **Install deps**: `uv sync --extra dev`

## Testing conventions
- Integration tests hit a real PostgreSQL database — do not mock the database connection
- HTTP calls to the GitHub API are mocked with `respx`; other dependencies use `pytest-mock`
- Coverage minimum is 80% (`fail_under = 80` in pyproject.toml)

## Style
- Line length: 110 (configured in `[tool.ruff]`)
- Active ruff rules: E, F, I (isort), UP (pyupgrade)
