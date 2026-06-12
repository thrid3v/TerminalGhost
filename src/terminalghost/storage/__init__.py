# terminalghost.storage — SQLite rolling buffer
#
# Responsibility:
#   Re-exports the public API of the storage layer.
#
# Submodules:
#   schema  — DDL strings and migration helpers (no DB connection)
#   db      — Database connection wrapper, CRUD, rolling-buffer management
#
# What calls this:
#   daemon.process opens the DB on start and passes the handle to capture + trigger.
#   context.assembler reads from the DB to assemble the LLM prompt.

from terminalghost.storage.db import CommandEvent, Database
from terminalghost.storage.schema import SCHEMA_VERSION

__all__ = ["CommandEvent", "Database", "SCHEMA_VERSION"]
