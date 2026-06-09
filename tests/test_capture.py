# tests.test_capture
#
# Tests for: terminalghost.capture.pty_capture, terminalghost.capture.shell_hooks
#
# Test cases to implement:
#
#   PTYCapture._detect_trigger:
#     - "??"             → True
#     - "??  "           → True  (trailing whitespace)
#     - "?? why"         → True  (inline context)
#     - "echo ??"        → False (part of a larger command)
#     - "? ?"            → False (wrong token)
#     - ""               → False
#
#   PTYCapture._redact_sensitive:
#     - "Enter password:" → b"<redacted>"
#     - "Password: "      → b"<redacted>"
#     - "ls -la"          → unchanged
#     - sudo prompt        → b"<redacted>"
#
#   HookReceiver._parse_payload:
#     - Valid JSON → CommandEvent with correct fields
#     - Missing "cmd" field → ValueError
#     - exit code out of range (256) → ValueError
#     - "cmd" is 5000 chars → ValueError (too long) or truncation, per design
#     - Non-JSON string → ValueError (json.JSONDecodeError)
#
#   HookReceiver socket integration (async):
#     - Client connects, sends one valid JSON line, receiver fires on_event
#     - Client sends partial payload across two writes → buffered correctly
#     - Client disconnects mid-payload → partial buffer discarded cleanly

import pytest

# TODO: write test functions — all should be stubs with `pass` or
#       `pytest.skip("not implemented")` until the source module is written
