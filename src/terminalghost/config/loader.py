# terminalghost.config.loader
#
# Responsibility:
#   Load, validate, and expose the application configuration.
#   Configuration source priority (highest to lowest):
#     1. Environment variables (TG_* prefix)
#     2. User config file (~/.config/terminalghost/config.toml)
#     3. Built-in defaults (defined as dataclass field defaults below)
#   Returns a frozen Config dataclass so callers can rely on immutability.
#
# Key classes / functions to implement:
#
#   @dataclass(frozen=True)
#   class OllamaConfig:
#       """
#       Fields:
#           base_url (str): Ollama server URL, default "http://localhost:11434"
#           model (str): model tag, default "llama3"
#           timeout_seconds (int): request timeout, default 60
#           retries (int): retry count on connection error, default 2
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class ClaudeConfig:
#       """
#       Fields:
#           api_key (str): Anthropic API key (prefer env var ANTHROPIC_API_KEY)
#           model (str): Claude model ID, default "claude-opus-4-8"
#           max_tokens (int): response token limit, default 1024
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class LLMConfig:
#       """
#       Fields:
#           backend (str): "ollama" | "claude" | "openai"
#           allow_inline_context (bool): allow "?? <text>", default True
#           ollama (OllamaConfig)
#           claude (ClaudeConfig)
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class CaptureConfig:
#       """
#       Fields:
#           redact_passwords (bool): default True
#           blocked_commands (list[str]): default ["gpg", "pass", "secret-tool"]
#           max_output_bytes (int): default 4096
#           use_pty (bool): default False
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class ContextConfig:
#       """
#       Fields:
#           tree_depth (int): default 3
#           tree_ignore (list[str]): default [".git", "__pycache__", ...]
#           token_budget (int): default 3000
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class GeneralConfig:
#       """
#       Fields:
#           history_size (int): default 200
#           db_path (str): default "~/.local/share/terminalghost/history.db"
#           socket_path (str): default "/tmp/terminalghost.sock"
#           pid_file (str): default "/tmp/terminalghost.pid"
#       """
#       pass  # TODO: implement
#
#   @dataclass(frozen=True)
#   class Config:
#       """
#       Top-level config object. All sections are nested dataclasses.
#       Fields:
#           general (GeneralConfig)
#           capture (CaptureConfig)
#           context (ContextConfig)
#           llm (LLMConfig)
#       """
#       pass  # TODO: implement
#
#   def load_config(path: str | None = None) -> Config:
#       """
#       Load and return the application Config.
#
#       Resolution order:
#         1. If `path` is given, read from that file.
#         2. Otherwise try ~/.config/terminalghost/config.toml.
#         3. If neither exists, use all defaults.
#
#       After reading the TOML file, overlay any TG_* environment variables.
#
#       Args:
#           path (str | None): optional explicit config file path
#       Returns:
#           Config: validated, frozen config object
#       Raises:
#           ConfigError: if the TOML is malformed or a value fails validation
#       """
#       pass  # TODO: implement
#
#   class ConfigError(Exception):
#       """Raised for config validation failures."""
#       pass  # TODO: implement
#
#   def _overlay_env_vars(raw: dict) -> dict:
#       """
#       Look for environment variables of the form TG_SECTION__KEY
#       (double underscore as separator) and merge them into the raw dict.
#       Examples:
#         TG_LLM__BACKEND=claude        → raw["llm"]["backend"] = "claude"
#         TG_GENERAL__HISTORY_SIZE=500  → raw["general"]["history_size"] = 500
#
#       Type coercion: parse "true"/"false" as bool; numeric strings as int.
#
#       Args:
#           raw (dict): parsed TOML dict (may be empty)
#       Returns:
#           dict: merged dict with env vars applied
#       """
#       pass  # TODO: implement
#
#   def _validate(raw: dict) -> None:
#       """
#       Validate the raw config dict before constructing Config.
#       Raise ConfigError with a descriptive message for:
#         - llm.backend not in ("ollama", "claude", "openai")
#         - history_size < 1 or > 10000
#         - tree_depth < 1 or > 10
#         - token_budget < 100
#         - socket_path too long (> 104 chars on macOS)
#       """
#       pass  # TODO: implement
#
# Edge cases:
#   - Config file not found: silently use defaults (not an error).
#   - Config file has extra unknown keys: ignore them (forward-compat).
#   - ~ in db_path / socket_path: expand with os.path.expanduser().
#   - Config file is world-readable and contains api_key: warn at load time
#     that the file permissions should be 0600, but do not refuse to load.
#   - Env var type mismatch (TG_GENERAL__HISTORY_SIZE=abc): raise ConfigError.
#
# Imports needed:
#   import os, tomllib, logging
#   from dataclasses import dataclass
#   from typing import Any

# TODO: implement all config dataclasses, load_config, and helpers
