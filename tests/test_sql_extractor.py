from unittest.mock import MagicMock

from sql_reviewer.diff_parser import ChangedFile, ChangedLine
from sql_reviewer.sql_extractor import extract_queries


def make_changed_file(filename: str, lines: dict[int, str], full_content: str = "") -> ChangedFile:
    """Helper: build a ChangedFile where keys are line numbers, values are content."""
    changed_lines = [
        ChangedLine(line_number=ln, diff_position=ln, content=content) for ln, content in lines.items()
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
    full_content = 'old = "SELECT id FROM orders"\nnew = "SELECT name FROM users"\n'
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


def test_orm_extraction_with_sqlalchemy(mocker):
    content = (
        "from sqlalchemy import select\n"
        "stmt = select(User).where(User.active == True)\n"
        "result = session.execute(stmt)\n"
    )
    cf = make_changed_file(
        "src/repo.py",
        {2: "stmt = select(User).where(User.active == True)"},
        full_content=content,
    )

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(
        content=[MagicMock(text='[{"sql": "SELECT * FROM users WHERE active = true", "line_number": 2}]')]
    )

    queries = extract_queries([cf], anthropic_client=mock_client)
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 1
    assert all("SELECT" in q.sql for q in orm)
    assert orm[0].diff_position == 2


def test_orm_extraction_malformed_json_skipped(mocker):
    content = "from sqlalchemy import select\nstmt = select(User)\n"
    cf = make_changed_file("src/repo.py", {2: "stmt = select(User)"}, full_content=content)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = MagicMock(content=[MagicMock(text="not valid json")])

    queries = extract_queries([cf], anthropic_client=mock_client)
    orm = [q for q in queries if q.source == "orm"]
    assert len(orm) == 0


def test_extracts_insert():
    content = "cursor.execute(\"INSERT INTO events (user_id, action) VALUES (1, 'click')\")\n"
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "INSERT" in raw[0].sql


def test_extracts_insert_returning():
    content = "sql = \"INSERT INTO users (email) VALUES ('a@b.com') RETURNING id\"\n"
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "RETURNING" in raw[0].sql


def test_extracts_update():
    content = 'q = "UPDATE users SET last_login = NOW() WHERE id = 1"\n'
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "UPDATE" in raw[0].sql


def test_extracts_cte():
    content = (
        'q = """\nWITH active AS (SELECT id FROM users WHERE active = true)\nSELECT * FROM active\n"""\n'
    )
    cf = make_changed_file("src/db.py", {1: content[:50]}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "WITH" in raw[0].sql


def test_extracts_merge():
    content = (
        'sql = """\n'
        "MERGE INTO inventory AS target\n"
        "USING staging AS source ON target.sku = source.sku\n"
        "WHEN MATCHED THEN UPDATE SET qty = source.qty\n"
        "WHEN NOT MATCHED THEN INSERT (sku, qty) VALUES (source.sku, source.qty)\n"
        '"""\n'
    )
    cf = make_changed_file("src/db.py", {1: content[:50]}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "MERGE" in raw[0].sql


def test_extracts_multiline_string():
    content = (
        'sql = """\n'
        "    SELECT u.id, u.email, o.total\n"
        "    FROM users u\n"
        "    JOIN orders o ON o.user_id = u.id\n"
        "    WHERE u.active = true\n"
        '"""\n'
    )
    cf = make_changed_file("src/db.py", {1: content[:50]}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "JOIN" in raw[0].sql


def test_extracts_parenthesized_select():
    """(SELECT ...) — substring keyword match fix ensures leading paren doesn't block detection."""
    content = 'q = "(SELECT id FROM users WHERE active = true)"\n'
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1


def test_skips_fstring_sql_fragments():
    """f-string SQL fragments must not be extracted as standalone queries."""
    content = 'q = f"SELECT * FROM {table} WHERE id = {id}"\n'
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    # The f-string literal fragments ("SELECT * FROM " etc.) must not be extracted
    assert len(raw) == 0


def test_extracts_text_wrapper():
    """sqlalchemy text('SELECT ...') — the string literal inside text() is a Constant and should be found."""
    content = 'stmt = text("SELECT id, name FROM products WHERE price > 100")\n'
    cf = make_changed_file("src/db.py", {1: content.strip()}, full_content=content)
    queries = extract_queries([cf], anthropic_client=None)
    raw = [q for q in queries if q.source == "raw"]
    assert len(raw) == 1
    assert "SELECT" in raw[0].sql


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
