import base64
import respx
import httpx
from sql_reviewer.diff_parser import (
    fetch_changed_files,
    parse_patch_positions,
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
    assert (
        result[2] == 3
    )  # "first add" at file line 2, position 3 (@@ header is pos 1, first context is pos 2)
    assert result[12] == 7  # "second add" at file line 12, position 7


def test_parse_patch_positions_no_additions():
    patch = "@@ -1,2 +1,2 @@\n unchanged\n unchanged"
    assert parse_patch_positions(patch) == {}


@respx.mock
def test_fetch_changed_files_returns_changed_lines(paginated):
    file_content = "line1\nline2\nSELECT * FROM users\nline4\n"
    encoded = base64.b64encode(file_content.encode()).decode()
    patch = "@@ -1,3 +1,4 @@\n line1\n line2\n+SELECT * FROM users\n line4"

    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        side_effect=paginated(
            [{"filename": "src/app.py", "status": "modified", "patch": patch}]
        )
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "feature-branch"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/src/app.py").mock(
        return_value=httpx.Response(
            200, json={"content": encoded + "\n", "encoding": "base64"}
        )
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
def test_fetch_changed_files_filters_by_pattern(paginated):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        side_effect=paginated(
            [
                {
                    "filename": "README.md",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n+text",
                },
                {
                    "filename": "src/app.py",
                    "status": "modified",
                    "patch": "@@ -1 +1,2 @@\n unchanged\n+new",
                },
            ]
        )
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "main"}})
    )
    file_content = "unchanged\nnew\n"
    encoded = base64.b64encode(file_content.encode()).decode()
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/src/app.py").mock(
        return_value=httpx.Response(
            200, json={"content": encoded, "encoding": "base64"}
        )
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])
    assert len(files) == 1
    assert files[0].filename == "src/app.py"


@respx.mock
def test_fetch_changed_files_skips_file_without_patch(paginated):
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        side_effect=paginated(
            [{"filename": "src/big.py", "status": "modified"}]
        )  # no patch key
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "main"}})
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])
    assert files == []


@respx.mock
def test_fetch_changed_files_skips_removed_file(paginated):
    patch = "@@ -1,3 +1,4 @@\n line1\n line2\n+SELECT * FROM users\n line4"

    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        side_effect=paginated(
            [{"filename": "src/app.py", "status": "removed", "patch": patch}]
        )
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "feature-branch"}})
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])
    assert files == []


@respx.mock
def test_fetch_changed_files_url_encodes_path(paginated):
    """A filename with a space is URL-encoded when fetching file contents."""
    filename = "src/my module.py"
    encoded_filename = "src/my%20module.py"
    file_content = "line1\nSELECT * FROM users\n"
    encoded_content = base64.b64encode(file_content.encode()).decode()
    patch = "@@ -1,1 +1,2 @@\n line1\n+SELECT * FROM users"

    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}/files").mock(
        side_effect=paginated(
            [{"filename": filename, "status": "modified", "patch": patch}]
        )
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/pulls/{PR_NUMBER}").mock(
        return_value=httpx.Response(200, json={"head": {"ref": "feature-branch"}})
    )
    respx.get(f"{BASE_URL}/repos/{REPO}/contents/{encoded_filename}").mock(
        return_value=httpx.Response(
            200, json={"content": encoded_content, "encoding": "base64"}
        )
    )

    files = fetch_changed_files(REPO, PR_NUMBER, TOKEN, ["src/**/*.py"])

    assert len(files) == 1
    assert files[0].filename == filename
    assert files[0].full_content == file_content
