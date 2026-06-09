# terminalghost.storage.schema
#
# Responsibility:
#   Contains all SQLite DDL strings and schema migration logic.
#   No database connection is opened here — this module is pure SQL strings
#   and version metadata consumed by db.py.
#
# Schema version strategy:
#   A single `schema_version` table holds an integer version. On every
#   Database.open(), db.py compares the stored version to SCHEMA_VERSION
#   and runs any needed migration steps in order.
#
# Tables:
#
#   schema_version
#     version INTEGER NOT NULL
#
#   commands
#     id          INTEGER PRIMARY KEY AUTOINCREMENT
#     session_id  INTEGER NOT NULL REFERENCES sessions(id)
#     ts          REAL    NOT NULL   -- Unix timestamp float (from hook)
#     cwd         TEXT    NOT NULL   -- working directory at time of command
#     cmd         TEXT    NOT NULL   -- raw command text
#     exit_code   INTEGER NOT NULL   -- 0 = success, non-zero = error
#     duration_ms INTEGER NOT NULL   -- wall time in milliseconds
#     output      TEXT              -- truncated stdout+stderr snippet (nullable)
#
#   sessions
#     id         INTEGER PRIMARY KEY AUTOINCREMENT
#     shell_pid  INTEGER NOT NULL
#     shell      TEXT    NOT NULL   -- e.g. "zsh", "bash"
#     start_ts   REAL    NOT NULL
#     end_ts     REAL               -- NULL while session is active
#
# Key constants / functions to implement:
#
#   SCHEMA_VERSION: int
#       Current schema version. Increment whenever DDL changes.
#
#   CREATE_COMMANDS_TABLE: str
#       SQL CREATE TABLE IF NOT EXISTS statement for `commands`.
#
#   CREATE_SESSIONS_TABLE: str
#       SQL CREATE TABLE IF NOT EXISTS statement for `sessions`.
#
#   CREATE_VERSION_TABLE: str
#       SQL CREATE TABLE IF NOT EXISTS statement for `schema_version`.
#
#   INDEXES: list[str]
#       List of CREATE INDEX statements (index commands.ts for fast
#       ORDER BY ts DESC queries; index commands.exit_code for last-error
#       lookup).
#
#   def get_migrations() -> dict[int, list[str]]:
#       """
#       Return a dict mapping schema version numbers to lists of SQL
#       statements that migrate FROM (version - 1) TO version.
#       Example: {2: ["ALTER TABLE commands ADD COLUMN output TEXT"]}
#
#       Returns:
#           dict[int, list[str]]: migration map
#       """
#       pass  # TODO: implement
#
# Edge cases:
#   - SQLite does not support DROP COLUMN before version 3.35. Check the
#     runtime SQLite version before issuing such migrations.
#   - `output` is nullable to allow hook-only capture (hooks don't capture
#     output, only PTY does).
#   - TEXT columns have no enforced length limit in SQLite; the application
#     layer (db.py) is responsible for truncation before INSERT.

SCHEMA_VERSION = 1

# TODO: define DDL strings and get_migrations()
