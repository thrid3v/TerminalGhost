# terminalghost.storage.schema
#
# All SQLite DDL strings and schema migration metadata. No database
# connection is opened here — this module is pure SQL strings consumed
# by db.py.

SCHEMA_VERSION = 1

CREATE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
)
"""

CREATE_SESSIONS_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    shell_pid INTEGER NOT NULL,
    shell     TEXT    NOT NULL,
    start_ts  REAL    NOT NULL,
    end_ts    REAL
)
"""

CREATE_COMMANDS_TABLE = """
CREATE TABLE IF NOT EXISTS commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER NOT NULL REFERENCES sessions(id),
    ts          REAL    NOT NULL,
    cwd         TEXT    NOT NULL,
    cmd         TEXT    NOT NULL,
    exit_code   INTEGER NOT NULL,
    duration_ms INTEGER NOT NULL,
    output      TEXT
)
"""

INDEXES = [
    # get_recent_commands / _prune order by id (monotonic), but ts is indexed
    # for any time-window queries; exit_code is indexed for get_last_error.
    "CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands(ts)",
    "CREATE INDEX IF NOT EXISTS idx_commands_exit_code ON commands(exit_code)",
]


def get_migrations() -> dict[int, list[str]]:
    """Map schema version N to the SQL that migrates from N-1 to N.

    Version 1 is the initial schema (created via the CREATE statements above),
    so there are no migrations yet. Example for a future version 2:
        {2: ["ALTER TABLE commands ADD COLUMN tag TEXT"]}
    """
    return {}
