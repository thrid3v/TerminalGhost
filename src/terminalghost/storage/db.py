# terminalghost.storage.db
#
# SQLite connection wrapper, CRUD operations, and rolling-buffer enforcement.
# This is the single module that touches the database file.
#
# Thread safety: one connection with check_same_thread=False, WAL journal
# mode where supported, and a threading.Lock serializing every operation.
# Ordering and pruning use the monotonic AUTOINCREMENT id (not ts, which can
# tie when two commands land in the same second).

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

from terminalghost.storage import schema

log = logging.getLogger(__name__)

# Application-layer truncation limits (TEXT columns are unbounded in SQLite).
MAX_CMD_CHARS = 4096


@dataclass
class CommandEvent:
    """Record of one shell command, passed between capture and storage."""

    session_id: int
    ts: float
    cwd: str
    cmd: str
    exit_code: int
    duration_ms: int
    output: str | None = None
    id: int | None = None  # database row ID; None before INSERT
    # Where the event came from: None/"hook" (ambient) or "run" (explicit
    # `terminalghost exec`). Not persisted; governs the output privacy gate.
    source: str | None = None


@dataclass
class Fix:
    """A named, reusable fix saved from a ?? suggestion (personal runbook)."""

    name: str
    commands: list[str]
    note: str = ""
    cwd: str = ""
    created_ts: float = 0.0
    last_used_ts: float | None = None
    id: int | None = None


_COLUMNS = "id, session_id, ts, cwd, cmd, exit_code, duration_ms, output"
_FIX_COLUMNS = "id, name, commands, note, cwd, created_ts, last_used_ts"


def _row_to_fix(row: tuple) -> Fix:
    (row_id, name, commands, note, cwd, created_ts, last_used_ts) = row
    return Fix(
        name=name,
        commands=commands.splitlines(),
        note=note,
        cwd=cwd,
        created_ts=created_ts,
        last_used_ts=last_used_ts,
        id=row_id,
    )


def _row_to_event(row: sqlite3.Row | tuple) -> CommandEvent:
    (row_id, session_id, ts, cwd, cmd, exit_code, duration_ms, output) = row
    return CommandEvent(
        session_id=session_id,
        ts=ts,
        cwd=cwd,
        cmd=cmd,
        exit_code=exit_code,
        duration_ms=duration_ms,
        output=output,
        id=row_id,
    )


class Database:
    """Thin wrapper around sqlite3.Connection with rolling-buffer pruning."""

    def __init__(
        self,
        path: str,
        history_size: int = 200,
        max_output_bytes: int = 4096,
    ) -> None:
        self._path = path
        self._history_size = history_size
        self._max_output_bytes = max_output_bytes
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Open (or create) the SQLite file, enable WAL, run migrations."""
        if self._conn is not None:
            return  # idempotent
        if self._path != ":memory:":
            parent = os.path.dirname(os.path.abspath(self._path))
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            # e.g. some NFS mounts don't support WAL; DELETE mode still works
            log.warning("WAL journal mode unavailable; falling back to DELETE")
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA foreign_keys=ON")
        with conn:
            conn.execute(schema.CREATE_VERSION_TABLE)
            conn.execute(schema.CREATE_SESSIONS_TABLE)
            conn.execute(schema.CREATE_COMMANDS_TABLE)
            conn.execute(schema.CREATE_FIXES_TABLE)
            for index_sql in schema.INDEXES:
                conn.execute(index_sql)
            self._run_migrations(conn)
        self._conn = conn

    def close(self) -> None:
        """Checkpoint WAL and close the connection. Idempotent."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.OperationalError:
                pass  # not in WAL mode
            self._conn.close()
            self._conn = None

    # -- commands ------------------------------------------------------------

    def insert_command(self, event: CommandEvent) -> int:
        """Insert one CommandEvent and return its row ID (-1 on write failure)."""
        conn = self._require_conn()
        cmd = event.cmd[:MAX_CMD_CHARS]
        output = event.output[: self._max_output_bytes] if event.output else event.output
        with self._lock:
            try:
                with conn:
                    cursor = conn.execute(
                        "INSERT INTO commands"
                        " (session_id, ts, cwd, cmd, exit_code, duration_ms, output)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.session_id,
                            event.ts,
                            event.cwd,
                            cmd,
                            event.exit_code,
                            event.duration_ms,
                            output,
                        ),
                    )
                    row_id = cursor.lastrowid
                    self._prune(conn)
                return int(row_id)
            except sqlite3.OperationalError as exc:
                # e.g. disk full — log rather than crashing the daemon
                log.warning("failed to persist command event: %s", exc)
                return -1

    def get_recent_commands(
        self,
        limit: int = 50,
        *,
        failed_only: bool = False,
        cwd: str | None = None,
        since: float | None = None,
        grep: str | None = None,
    ) -> list[CommandEvent]:
        """Return the `limit` most recent commands, newest first.

        Optional filters (ANDed together): `failed_only` keeps non-zero exits,
        `cwd` matches the exact working directory, `since` is a minimum epoch
        timestamp, and `grep` is a case-insensitive substring match on the
        command text.
        """
        conn = self._require_conn()
        limit = max(0, min(limit, self._history_size))
        where: list[str] = []
        params: list = []
        if failed_only:
            where.append("exit_code != 0")
        if cwd is not None:
            where.append("cwd = ?")
            params.append(cwd)
        if since is not None:
            where.append("ts >= ?")
            params.append(since)
        if grep is not None:
            # Escape LIKE wildcards so the needle is a literal substring.
            needle = grep.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            where.append("cmd LIKE ? ESCAPE '\\'")
            params.append(f"%{needle}%")
        query = f"SELECT {_COLUMNS} FROM commands"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def get_last_error(self, cwd: str | None = None) -> CommandEvent | None:
        """Return the most recent failing command, or None.

        When `cwd` is given, only failures in that directory are considered —
        this scopes a `??` to the project you're standing in and avoids
        surfacing an unrelated error from another terminal.
        """
        conn = self._require_conn()
        query = f"SELECT {_COLUMNS} FROM commands WHERE exit_code != 0"
        params: tuple = ()
        if cwd is not None:
            query += " AND cwd = ?"
            params = (cwd,)
        query += " ORDER BY id DESC LIMIT 1"
        with self._lock:
            row = conn.execute(query, params).fetchone()
        return _row_to_event(row) if row else None

    def get_recent_errors(self, cwd: str | None = None, limit: int = 5) -> list[CommandEvent]:
        """The `limit` most recent failing commands, newest first.

        Used to cluster a `??` around the errors related to the one being asked
        about (same tool), instead of only the single latest failure.
        """
        conn = self._require_conn()
        limit = max(0, min(limit, self._history_size))
        query = f"SELECT {_COLUMNS} FROM commands WHERE exit_code != 0"
        params: list = []
        if cwd is not None:
            query += " AND cwd = ?"
            params.append(cwd)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def clear_commands(self, last: int | None = None) -> int:
        """Delete captured commands and return how many rows were removed.

        With `last` given, only the N most recent commands are deleted (the
        "I just typed a secret" case); otherwise everything goes. VACUUMs
        afterwards so the deleted text actually leaves the database file.
        """
        conn = self._require_conn()
        with self._lock:
            with conn:
                if last is None:
                    cursor = conn.execute("DELETE FROM commands")
                else:
                    cursor = conn.execute(
                        "DELETE FROM commands WHERE id IN"
                        " (SELECT id FROM commands ORDER BY id DESC LIMIT ?)",
                        (max(0, last),),
                    )
                deleted = cursor.rowcount
            try:
                conn.execute("VACUUM")
            except sqlite3.OperationalError:
                pass  # e.g. another connection holds the file — data is gone anyway
        return deleted

    # -- saved fixes -----------------------------------------------------------

    def save_fix(self, name: str, commands: list[str], note: str = "",
                 cwd: str = "") -> None:
        """Save (or overwrite) a named fix. Raises ValueError on empty input."""
        name = name.strip()
        commands = [c.strip() for c in commands if c.strip()]
        if not name:
            raise ValueError("fix name must not be empty")
        if not commands:
            raise ValueError("fix must contain at least one command")
        conn = self._require_conn()
        with self._lock, conn:
            conn.execute(
                "INSERT INTO fixes (name, commands, note, cwd, created_ts)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(name) DO UPDATE SET"
                "  commands=excluded.commands, note=excluded.note,"
                "  cwd=excluded.cwd, created_ts=excluded.created_ts,"
                "  last_used_ts=NULL",
                (name, "\n".join(commands), note, cwd, time.time()),
            )

    def get_fix(self, name: str) -> Fix | None:
        conn = self._require_conn()
        with self._lock:
            row = conn.execute(
                f"SELECT {_FIX_COLUMNS} FROM fixes WHERE name = ?", (name.strip(),)
            ).fetchone()
        return _row_to_fix(row) if row else None

    def list_fixes(self, grep: str | None = None) -> list[Fix]:
        """All saved fixes, most recently used/created first."""
        conn = self._require_conn()
        query = f"SELECT {_FIX_COLUMNS} FROM fixes"
        params: tuple = ()
        if grep:
            needle = grep.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query += " WHERE name LIKE ? ESCAPE '\\' OR commands LIKE ? ESCAPE '\\'"
            params = (f"%{needle}%", f"%{needle}%")
        query += " ORDER BY COALESCE(last_used_ts, created_ts) DESC"
        with self._lock:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_fix(row) for row in rows]

    def delete_fix(self, name: str) -> bool:
        conn = self._require_conn()
        with self._lock, conn:
            cursor = conn.execute("DELETE FROM fixes WHERE name = ?", (name.strip(),))
            return cursor.rowcount > 0

    def touch_fix(self, name: str) -> None:
        """Record that a fix was just used (for most-recently-used ordering)."""
        conn = self._require_conn()
        with self._lock, conn:
            conn.execute(
                "UPDATE fixes SET last_used_ts = ? WHERE name = ?",
                (time.time(), name.strip()),
            )

    # -- sessions ------------------------------------------------------------

    def start_session(self, shell_pid: int, shell: str) -> int:
        """Insert a new sessions row and return its ID."""
        conn = self._require_conn()
        with self._lock, conn:
            cursor = conn.execute(
                "INSERT INTO sessions (shell_pid, shell, start_ts) VALUES (?, ?, ?)",
                (shell_pid, shell, time.time()),
            )
            return int(cursor.lastrowid)

    def end_session(self, session_id: int) -> None:
        """Set end_ts = now on the given session row."""
        conn = self._require_conn()
        with self._lock, conn:
            conn.execute(
                "UPDATE sessions SET end_ts = ? WHERE id = ?",
                (time.time(), session_id),
            )

    # -- internals -----------------------------------------------------------

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not open; call open() first")
        return self._conn

    def _prune(self, conn: sqlite3.Connection) -> None:
        """Delete oldest rows so the total count stays <= history_size.

        Called inside insert_command's transaction (lock already held).
        """
        conn.execute(
            "DELETE FROM commands WHERE id NOT IN"
            " (SELECT id FROM commands ORDER BY id DESC LIMIT ?)",
            (self._history_size,),
        )

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        """Bring the schema_version up to schema.SCHEMA_VERSION."""
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            # Fresh database: tables were just created at the current schema.
            conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)",
                (schema.SCHEMA_VERSION,),
            )
            return
        current = int(row[0])
        if current > schema.SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {current} is newer than this build"
                f" ({schema.SCHEMA_VERSION}); upgrade terminalghost"
            )
        if current == schema.SCHEMA_VERSION:
            return
        migrations = schema.get_migrations()
        for version in range(current + 1, schema.SCHEMA_VERSION + 1):
            for statement in migrations.get(version, []):
                conn.execute(statement)
            log.info("migrated database schema to version %d", version)
        conn.execute("UPDATE schema_version SET version = ?", (schema.SCHEMA_VERSION,))
