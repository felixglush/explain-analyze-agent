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

# Use try/except so that reloads (e.g. during tests) do not overwrite
# any mock that a test has already patched onto this module's namespace.
try:
    fetch_changed_files  # type: ignore[used-before-def]
except NameError:
    from sql_reviewer.diff_parser import fetch_changed_files  # noqa: F401

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


def _run_schema_setup(
    config_path: Path,
    schema_file: str | None,
    setup_command: str | None,
    database_url: str,
) -> None:
    if schema_file:
        schema_path = config_path.parent / schema_file
        if not schema_path.exists():
            logger.error("schema_file not found: %s", schema_path)
            sys.exit(1)
        cmd = f"psql {database_url} -f {schema_path}"
        logger.info("Applying schema: %s", cmd)
    else:
        cmd = setup_command

    if not cmd:
        logger.error("No schema_file or setup_command configured")
        sys.exit(1)

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

    # 2. Fetch PR diff
    logger.info("Fetching PR #%d diff from %s", pr_number, repo)
    try:
        changed_files = fetch_changed_files(
            repo, pr_number, token, config.file_patterns
        )
    except Exception as e:
        logger.error("Failed to fetch PR diff: %s", e)
        sys.exit(1)

    if not changed_files:
        logger.info(
            "No matching Python files changed in PR #%d — nothing to review", pr_number
        )
        sys.exit(0)

    # 3. Run schema setup
    _run_schema_setup(
        config_path, config.schema_file, config.setup_command, database_url
    )

    # 4. Extract SQL queries
    anthropic_client = anthropic.Anthropic(api_key=anthropic_api_key)
    queries = extract_queries(changed_files, anthropic_client)
    logger.info(
        "Extracted %d SQL quer%s", len(queries), "y" if len(queries) == 1 else "ies"
    )

    if not queries:
        logger.info("No SQL queries found in changed files — nothing to review")
        sys.exit(0)

    # 5. Run EXPLAIN ANALYZE
    try:
        explain_results = explain_queries(queries, database_url)
    except Exception as e:
        logger.error("Failed to run EXPLAIN ANALYZE: %s", e)
        sys.exit(1)
    logger.info(
        "%d/%d queries explained successfully", len(explain_results), len(queries)
    )

    if not explain_results:
        logger.error(
            "All %d quer%s failed EXPLAIN ANALYZE — aborting",
            len(queries),
            "y" if len(queries) == 1 else "ies",
        )
        sys.exit(1)

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
