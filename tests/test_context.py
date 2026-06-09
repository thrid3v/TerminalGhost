# tests.test_context
#
# Tests for: terminalghost.context.assembler
#
# Use a real in-memory Database and a temp directory as cwd.
#
# Test cases to implement:
#
#   ContextAssembler.assemble():
#     - With no history → output contains only tree + inline context sections
#     - With one failing command → output contains "last error" section
#     - inline_context="" → no "User note" section in output
#     - inline_context="why" → "why" appears in output
#     - token_budget enforced: insert 300 commands; assembled prompt token
#       estimate <= token_budget
#
#   _format_directory_tree():
#     - Non-existent cwd → returns "(directory not found)" string
#     - Tree depth respected: files beyond depth do NOT appear
#     - Ignored directories (e.g. ".git") do NOT appear
#     - Cap at 200 entries: create 210 files; output contains "(truncated)"
#
#   _estimate_tokens():
#     - Empty string → 0
#     - 400-char string → ~100 tokens (within 20% of 100)
#
#   _format_command_history():
#     - Last error command is visually distinguished in output
#     - Commands ordered oldest-first in the formatted string

import pytest

# TODO: write test functions
