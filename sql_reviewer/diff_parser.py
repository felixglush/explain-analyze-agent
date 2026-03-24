from __future__ import annotations
import base64
import fnmatch
import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx


GITHUB_API = "https://api.github.com"


@dataclass
class ChangedLine:
    line_number: int  # absolute line number in the file (1-based)
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

    for line in patch.splitlines():
        position += 1
        if line.startswith("@@"):
            m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", line)
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


def _matches_pattern(filename: str, pattern: str) -> bool:
    """Match filename against a glob pattern, supporting ** for multiple path segments."""
    if "**" in pattern:
        # Convert ** glob to regex: **/ matches zero or more path segments
        regex = re.escape(pattern)
        regex = regex.replace(r"\*\*/", "(.+/)?").replace(r"\*\*", ".*")
        regex = regex.replace(r"\*", "[^/]*")
        return bool(re.fullmatch(regex, filename))
    return fnmatch.fnmatch(filename, pattern)


def _matches_patterns(filename: str, patterns: list[str]) -> bool:
    return any(_matches_pattern(filename, p) for p in patterns)


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

    # Get list of changed files (paginated; GitHub caps at 30 by default)
    all_files: list[dict] = []
    page = 1
    while True:
        files_resp = httpx.get(
            f"{GITHUB_API}/repos/{repo}/pulls/{pr_number}/files",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        files_resp.raise_for_status()
        page_data = files_resp.json()
        if not page_data:
            break
        all_files.extend(page_data)
        page += 1

    results: list[ChangedFile] = []

    for file_info in all_files:
        filename = file_info["filename"]
        patch = file_info.get("patch")

        if file_info.get("status") == "removed":
            continue  # deleted files have no content to fetch
        if not patch:
            continue  # large files may omit patch; skip
        if not _matches_patterns(filename, file_patterns):
            continue

        # Fetch full file content (URL-encode path to handle spaces and special chars)
        content_resp = httpx.get(
            f"{GITHUB_API}/repos/{repo}/contents/{quote(filename, safe='/')}",
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

        results.append(
            ChangedFile(
                filename=filename,
                full_content=full_content,
                changed_lines=changed_lines,
            )
        )

    return results
