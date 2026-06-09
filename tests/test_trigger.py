# tests.test_trigger
#
# Tests for: terminalghost.trigger.handler
#
# Test cases to implement:
#
#   TriggerHandler.is_trigger():
#     - "??"                → True
#     - "??  "              → True
#     - "?? extra context"  → True
#     - "echo ??"           → False
#     - "??something"       → False (no space after ??)
#     - "? ?"               → False
#     - ""                  → False
#     - "?? "               → True (space only after ??)
#
#   TriggerHandler.extract_inline_context():
#     - "??"              → ""
#     - "?? why fail"     → "why fail"
#     - "??  leading ws"  → "leading ws" (stripped)
#
#   TriggerHandler.handle() integration (async):
#     - Non-trigger command → on_event not called (pipeline not run)
#     - Trigger command + backend.is_available()=False → prints error, no LLM call
#     - Trigger command + backend available → assembler.assemble called,
#       stream_query called, output written
#     - Ctrl-C during streaming → no traceback, clean return
#     - Two rapid triggers → second waits for first (Lock test)

import pytest
import asyncio

# TODO: write test functions
