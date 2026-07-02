# tests.test_storage — tests for terminalghost.storage.db / schema

import time

import pytest

from terminalghost.storage import schema
from terminalghost.storage.db import MAX_CMD_CHARS, CommandEvent, Database


def make_event(cmd="ls", exit_code=0, output=None, ts=None, session_id=1):
    return CommandEvent(
        session_id=session_id,
        ts=ts if ts is not None else time.time(),
        cwd="/home/user",
        cmd=cmd,
        exit_code=exit_code,
        duration_ms=12,
        output=output,
    )


@pytest.fixture
def db():
    database = Database(":memory:", history_size=200)
    database.open()
    database.start_session(shell_pid=1, shell="test")  # session_id 1 for FK
    yield database
    database.close()


# -- open ------------------------------------------------------------------------


def test_open_creates_tables(db):
    conn = db._require_conn()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"commands", "sessions", "schema_version"} <= tables


def test_open_stores_schema_version(db):
    conn = db._require_conn()
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    assert row[0] == schema.SCHEMA_VERSION


def test_open_is_idempotent(db):
    db.open()  # second call must not raise
    assert db.get_recent_commands() == []


def test_close_is_idempotent(db):
    db.close()
    db.close()


# -- insert / read -----------------------------------------------------------------


def test_insert_and_retrieve(db):
    row_id = db.insert_command(make_event(cmd="make build", exit_code=1))
    assert isinstance(row_id, int) and row_id > 0
    recent = db.get_recent_commands()
    assert len(recent) == 1
    assert recent[0].cmd == "make build"
    assert recent[0].exit_code == 1
    assert recent[0].id == row_id


def test_insert_truncates_cmd_and_output(db):
    long_cmd = "x" * (MAX_CMD_CHARS + 100)
    long_output = "y" * 10000
    db.insert_command(make_event(cmd=long_cmd, output=long_output))
    event = db.get_recent_commands()[0]
    assert len(event.cmd) == MAX_CMD_CHARS
    assert len(event.output) == 4096  # default max_output_bytes


def test_get_recent_commands_limit_and_order(db):
    for i in range(10):
        db.insert_command(make_event(cmd=f"cmd-{i}"))
    recent = db.get_recent_commands(limit=3)
    assert [e.cmd for e in recent] == ["cmd-9", "cmd-8", "cmd-7"]


def test_get_recent_commands_empty(db):
    assert db.get_recent_commands() == []


# -- last error ---------------------------------------------------------------------


def test_get_last_error_none_when_all_succeed(db):
    db.insert_command(make_event(exit_code=0))
    db.insert_command(make_event(exit_code=0))
    assert db.get_last_error() is None


def test_get_last_error_returns_most_recent_failure(db):
    db.insert_command(make_event(cmd="fail-1", exit_code=1))
    db.insert_command(make_event(cmd="fail-2", exit_code=2))
    db.insert_command(make_event(cmd="ok", exit_code=0))  # later success ignored
    error = db.get_last_error()
    assert error is not None
    assert error.cmd == "fail-2"


# -- clear ---------------------------------------------------------------------------


def test_clear_commands_all(db):
    for i in range(5):
        db.insert_command(make_event(cmd=f"cmd-{i}"))
    assert db.clear_commands() == 5
    assert db.get_recent_commands() == []


def test_clear_commands_last_n(db):
    for i in range(5):
        db.insert_command(make_event(cmd=f"cmd-{i}"))
    assert db.clear_commands(last=2) == 2
    remaining = [e.cmd for e in db.get_recent_commands()]
    assert remaining == ["cmd-2", "cmd-1", "cmd-0"]


def test_clear_commands_last_more_than_present(db):
    db.insert_command(make_event(cmd="only"))
    assert db.clear_commands(last=10) == 1
    assert db.get_recent_commands() == []


def test_clear_commands_empty(db):
    assert db.clear_commands() == 0
    assert db.clear_commands(last=3) == 0


def test_clear_commands_shrinks_file(tmp_path):
    path = str(tmp_path / "history.db")
    db = Database(path, history_size=200)
    db.open()
    db.start_session(shell_pid=1, shell="test")
    try:
        for i in range(50):
            db.insert_command(make_event(cmd="x" * 2000, output="y" * 4000))
        conn = db._require_conn()
        before = conn.execute("PRAGMA page_count").fetchone()[0]
        db.clear_commands()
        after = conn.execute("PRAGMA page_count").fetchone()[0]
        assert after < before  # VACUUM reclaimed the deleted pages
    finally:
        db.close()


# -- rolling buffer -------------------------------------------------------------------


def test_prune_enforces_history_size():
    db = Database(":memory:", history_size=20)
    db.open()
    db.start_session(shell_pid=1, shell="test")
    try:
        for i in range(30):
            db.insert_command(make_event(cmd=f"cmd-{i}"))
        recent = db.get_recent_commands(limit=50)
        assert len(recent) == 20
        # oldest deleted, newest kept
        assert recent[0].cmd == "cmd-29"
        assert recent[-1].cmd == "cmd-10"
    finally:
        db.close()


# -- sessions ---------------------------------------------------------------------------


def test_start_and_end_session(db):
    session_id = db.start_session(shell_pid=4242, shell="zsh")
    assert session_id > 0
    conn = db._require_conn()
    row = conn.execute(
        "SELECT shell_pid, shell, end_ts FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert row[0] == 4242 and row[1] == "zsh" and row[2] is None
    db.end_session(session_id)
    row = conn.execute(
        "SELECT end_ts FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    assert row[0] is not None


# -- migrations ----------------------------------------------------------------------------


def test_migration_applies_and_preserves_content(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")

    # create a v1 database with one row
    db1 = Database(path, history_size=10)
    db1.open()
    db1.start_session(shell_pid=1, shell="test")
    db1.insert_command(make_event(cmd="before-migration"))
    db1.close()

    # pretend the code moved to schema v2 with one ALTER migration
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 2)
    monkeypatch.setattr(
        schema,
        "get_migrations",
        lambda: {2: ["ALTER TABLE commands ADD COLUMN tag TEXT"]},
    )

    db2 = Database(path, history_size=10)
    db2.open()
    try:
        conn = db2._require_conn()
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        columns = [r[1] for r in conn.execute("PRAGMA table_info(commands)")]
        assert "tag" in columns
        # content preserved
        assert db2.get_recent_commands()[0].cmd == "before-migration"
    finally:
        db2.close()


def test_newer_db_than_code_raises(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    db1 = Database(path)
    db1.open()
    db1.close()

    monkeypatch.setattr(schema, "SCHEMA_VERSION", 0)
    db2 = Database(path)
    with pytest.raises(RuntimeError, match="newer"):
        db2.open()
