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


# -- history filters -----------------------------------------------------------------


def _insert_filter_fixture(db):
    now = time.time()
    db.insert_command(CommandEvent(
        session_id=1, ts=now - 7200, cwd="/proj-a", cmd="docker build .",
        exit_code=1, duration_ms=10,
    ))
    db.insert_command(CommandEvent(
        session_id=1, ts=now - 60, cwd="/proj-b", cmd="docker run app",
        exit_code=0, duration_ms=10,
    ))
    db.insert_command(CommandEvent(
        session_id=1, ts=now - 30, cwd="/proj-a", cmd="make test",
        exit_code=2, duration_ms=10,
    ))
    return now


def test_filter_failed_only(db):
    _insert_filter_fixture(db)
    failed = db.get_recent_commands(failed_only=True)
    assert [e.cmd for e in failed] == ["make test", "docker build ."]


def test_filter_by_cwd(db):
    _insert_filter_fixture(db)
    in_a = db.get_recent_commands(cwd="/proj-a")
    assert [e.cmd for e in in_a] == ["make test", "docker build ."]
    assert db.get_recent_commands(cwd="/nowhere") == []


def test_filter_since(db):
    now = _insert_filter_fixture(db)
    recent = db.get_recent_commands(since=now - 120)
    assert [e.cmd for e in recent] == ["make test", "docker run app"]


def test_filter_grep_case_insensitive_substring(db):
    _insert_filter_fixture(db)
    hits = db.get_recent_commands(grep="DOCKER")
    assert [e.cmd for e in hits] == ["docker run app", "docker build ."]


def test_filter_grep_escapes_like_wildcards(db):
    db.insert_command(make_event(cmd="echo 100%"))
    db.insert_command(make_event(cmd="echo hundred"))
    hits = db.get_recent_commands(grep="100%")
    assert [e.cmd for e in hits] == ["echo 100%"]
    # a bare % must not act as match-everything
    assert db.get_recent_commands(grep="%hundred%") == []


def test_filters_combine(db):
    _insert_filter_fixture(db)
    hits = db.get_recent_commands(failed_only=True, cwd="/proj-a", grep="docker")
    assert [e.cmd for e in hits] == ["docker build ."]


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


# -- saved fixes -----------------------------------------------------------------------


def test_save_and_get_fix(db):
    db.save_fix("jest-cache", ["npx jest --clearCache", "npm test"],
                note="jest cache corruption", cwd="/proj")
    fix = db.get_fix("jest-cache")
    assert fix.commands == ["npx jest --clearCache", "npm test"]
    assert fix.note == "jest cache corruption"
    assert fix.cwd == "/proj"
    assert fix.created_ts > 0
    assert fix.last_used_ts is None


def test_save_fix_overwrites(db):
    db.save_fix("f", ["old command"])
    db.save_fix("f", ["new command"])
    assert db.get_fix("f").commands == ["new command"]
    assert len(db.list_fixes()) == 1


def test_save_fix_validates(db):
    with pytest.raises(ValueError):
        db.save_fix("", ["cmd"])
    with pytest.raises(ValueError):
        db.save_fix("name", ["  ", ""])


def test_list_fixes_grep_and_order(db):
    db.save_fix("alpha", ["docker compose up"])
    db.save_fix("beta", ["make build"])
    db.touch_fix("alpha")  # most recently used first
    names = [f.name for f in db.list_fixes()]
    assert names[0] == "alpha"
    hits = db.list_fixes(grep="docker")
    assert [f.name for f in hits] == ["alpha"]


def test_delete_fix(db):
    db.save_fix("gone", ["true"])
    assert db.delete_fix("gone") is True
    assert db.delete_fix("gone") is False
    assert db.get_fix("gone") is None


def test_get_fix_missing(db):
    assert db.get_fix("nope") is None


# -- migrations ----------------------------------------------------------------------------


def _make_v1_db(path, monkeypatch):
    """Create a database stamped at schema version 1 (pre-fixes)."""
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 1)
    monkeypatch.setattr(schema, "get_migrations", lambda: {})
    db1 = Database(path, history_size=10)
    db1.open()
    db1.start_session(shell_pid=1, shell="test")
    db1.insert_command(make_event(cmd="before-migration"))
    db1.close()
    monkeypatch.undo()


def test_migration_applies_and_preserves_content(tmp_path, monkeypatch):
    path = str(tmp_path / "history.db")
    _make_v1_db(path, monkeypatch)

    # pretend the code moved one further, to v3, with an ALTER migration
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 3)
    real = schema.get_migrations()
    monkeypatch.setattr(
        schema,
        "get_migrations",
        lambda: {**real, 3: ["ALTER TABLE commands ADD COLUMN tag TEXT"]},
    )

    db2 = Database(path, history_size=10)
    db2.open()
    try:
        conn = db2._require_conn()
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 3
        columns = [r[1] for r in conn.execute("PRAGMA table_info(commands)")]
        assert "tag" in columns
        # content preserved
        assert db2.get_recent_commands()[0].cmd == "before-migration"
    finally:
        db2.close()


def test_v1_database_gains_fixes_table(tmp_path, monkeypatch):
    """The real v1 → v2 upgrade: an old DB gets the fixes table."""
    path = str(tmp_path / "history.db")
    _make_v1_db(path, monkeypatch)

    db2 = Database(path, history_size=10)
    db2.open()
    try:
        conn = db2._require_conn()
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 2
        db2.save_fix("hello", ["echo hi"])  # table exists and works
        assert db2.get_fix("hello").commands == ["echo hi"]
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
