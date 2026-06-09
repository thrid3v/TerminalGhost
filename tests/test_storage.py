# tests.test_storage
#
# Tests for: terminalghost.storage.db, terminalghost.storage.schema
#
# All tests should use Database(":memory:") to avoid touching the filesystem.
#
# Test cases to implement:
#
#   Database.open():
#     - Creates all tables on first open
#     - Stores schema_version = SCHEMA_VERSION
#     - Idempotent: opening the same in-memory DB twice does not error
#
#   Database.insert_command():
#     - Inserted row is retrievable via get_recent_commands()
#     - Returns a positive integer row ID
#     - Truncates cmd/output to max lengths
#
#   Database.get_recent_commands():
#     - Returns at most `limit` rows
#     - Ordered newest-first
#     - Returns empty list when no commands exist
#
#   Database.get_last_error():
#     - Returns None when all commands have exit_code == 0
#     - Returns the most recent command with exit_code != 0
#     - Ignores later successful commands (last error, not last command)
#
#   Rolling buffer (_prune):
#     - Insert history_size + 10 rows; verify only history_size remain
#     - Oldest rows are the ones deleted, not newest
#
#   Schema migration:
#     - A DB at version N-1 is migrated to version N on open
#     - Content is preserved across migration
#
#   start_session / end_session:
#     - start creates a row; end_session sets end_ts

import pytest

# TODO: write test functions
