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

Copy `workflow-template.yml` to `.github/workflows/sql-review.yml` in your repo. That's the only workflow file you'll ever need — all Postgres setup, Python installation, and environment wiring is handled by the reusable workflow in this repo. You never need to edit it again.

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
