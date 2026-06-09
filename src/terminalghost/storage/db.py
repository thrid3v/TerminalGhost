# terminalghost.storage.db
#
# Responsibility:
#   SQLite connection wrapper, CRUD operations, and rolling-buffer enforcement.
#   This is the single module that touches the database file. All other
#   modules interact with storage through this module's public API.
#
# Key classes / functions to implement:
#
#   @dataclass
#   class CommandEvent:
#       """
#       Immutable record of one shell command. Passed between the capture
#       layer and the storage layer. Also returned by read queries.
#
#       Fields:
#           id (int | None): database row ID; None before INSERT
#           session_id (int): FK to sessions table
#           ts (float): Unix timestamp
#           cwd (str): working directory
#           cmd (str): raw command text
#           exit_code (int): shell exit code
#           duration_ms (int): wall time in ms
#           output (str | None): truncated output snippet
#       """
#       pass  # TODO: implement as dataclass
#
#   class Database:
#       """
#       Thin wrapper around sqlite3.Connection. Manages schema migrations,
#       insert, query, and rolling-buffer pruning.
#
#       Args:
#           path (str): absolute path to the .db file. Use ":memory:" for tests.
#           history_size (int): max number of command rows to retain.
#
#       Thread safety:
#           sqlite3 connections are not thread-safe by default. Either open
#           one connection per thread (check_same_thread=False + WAL mode)
#           or use a threading.Lock around every operation. WAL journal mode
#           is strongly recommended to allow concurrent readers.
#       """
#
#       def open(self) -> None:
#           """
#           Open (or create) the SQLite file, enable WAL mode, run any
#           pending schema migrations, and record the current SCHEMA_VERSION.
#
#           Creates parent directories if they don't exist (e.g. first run).
#           """
#           pass  # TODO: implement
#
#       def close(self) -> None:
#           """
#           Flush WAL, checkpoint, and close the connection.
#           Safe to call multiple times (idempotent).
#           """
#           pass  # TODO: implement
#
#       def insert_command(self, event: CommandEvent) -> int:
#           """
#           Insert one CommandEvent row and return its new row ID.
#           Calls _prune() after every insert to enforce history_size.
#
#           Args:
#               event (CommandEvent): the event to persist
#           Returns:
#               int: the assigned row ID
#           """
#           pass  # TODO: implement
#
#       def get_recent_commands(self, limit: int = 50) -> list[CommandEvent]:
#           """
#           Return the `limit` most recent commands ordered newest-first.
#           Used by context.assembler to build the LLM prompt.
#
#           Args:
#               limit (int): max rows to return (capped at history_size)
#           Returns:
#               list[CommandEvent]: ordered newest → oldest
#           """
#           pass  # TODO: implement
#
#       def get_last_error(self) -> CommandEvent | None:
#           """
#           Return the most recent command whose exit_code != 0, or None
#           if all recent commands succeeded. Used by context.assembler to
#           highlight the failing command.
#
#           Returns:
#               CommandEvent | None
#           """
#           pass  # TODO: implement
#
#       def start_session(self, shell_pid: int, shell: str) -> int:
#           """
#           Insert a new sessions row and return its ID.
#           Called when the daemon accepts a new shell hook connection.
#
#           Args:
#               shell_pid (int): PID of the connecting shell process
#               shell (str): shell name, e.g. "zsh"
#           Returns:
#               int: new session_id
#           """
#           pass  # TODO: implement
#
#       def end_session(self, session_id: int) -> None:
#           """
#           Set end_ts = now on the given session row.
#           Called when the daemon detects a shell connection closed.
#           """
#           pass  # TODO: implement
#
#       def _prune(self) -> None:
#           """
#           Delete oldest rows so the total count stays <= history_size.
#           Uses a DELETE WHERE id NOT IN (SELECT id ... ORDER BY ts DESC LIMIT N)
#           pattern. Called internally after every insert — should be fast
#           because the id index makes the subquery O(log n).
#           """
#           pass  # TODO: implement
#
#       def _run_migrations(self, conn: "sqlite3.Connection") -> None:
#           """
#           Read current schema version from the DB, compare to
#           schema.SCHEMA_VERSION, and apply any needed migration SQL blocks
#           from schema.get_migrations() in order within a transaction.
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - DB file on a filesystem that doesn't support WAL (e.g. some NFS mounts):
#     fall back to DELETE journal mode gracefully.
#   - Concurrent writes from multiple shell sessions: WAL + write serialization
#     via a threading.Lock is the simplest safe approach.
#   - First-run: db_path parent directories may not exist; create them.
#   - Disk full during INSERT: catch sqlite3.OperationalError and log a
#     warning rather than crashing the daemon.
#   - ":memory:" path for tests: skip parent-dir creation logic.
#
# Imports needed:
#   import sqlite3, threading, os, logging
#   from dataclasses import dataclass, field
#   from terminalghost.storage.schema import (
#       SCHEMA_VERSION, CREATE_COMMANDS_TABLE, CREATE_SESSIONS_TABLE,
#       CREATE_VERSION_TABLE, INDEXES, get_migrations
#   )

# TODO: implement CommandEvent dataclass and Database class
