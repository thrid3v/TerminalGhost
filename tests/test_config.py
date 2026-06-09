# tests.test_config
#
# Tests for: terminalghost.config.loader
#
# Test cases to implement:
#
#   load_config():
#     - No file, no env vars → returns Config with all defaults
#     - Valid TOML file → fields populated from file
#     - TOML file with unknown keys → loaded without error (forward-compat)
#     - Malformed TOML → raises ConfigError
#     - File not found at explicit path → raises ConfigError (explicit path
#       differs from default path: explicit means the user intended a file)
#
#   _overlay_env_vars():
#     - TG_LLM__BACKEND=claude → raw["llm"]["backend"] == "claude"
#     - TG_GENERAL__HISTORY_SIZE=500 → raw["general"]["history_size"] == 500
#     - TG_GENERAL__HISTORY_SIZE=abc → raises ConfigError (type coercion fail)
#     - Unknown env var (not TG_ prefix) → ignored
#
#   _validate():
#     - llm.backend="unknown" → raises ConfigError
#     - history_size=0 → raises ConfigError
#     - tree_depth=15 → raises ConfigError
#     - socket_path of 110 chars → raises ConfigError (macOS limit)
#
#   ~ expansion:
#     - db_path containing "~" → expanded to absolute path in returned Config
#
#   Config immutability:
#     - Attempting to set a field on a frozen Config → raises FrozenInstanceError

import pytest
import os

# TODO: write test functions
