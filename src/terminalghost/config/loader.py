# terminalghost.config.loader
#
# Load, validate, and expose the application configuration.
# Source priority (highest to lowest):
#   1. Environment variables (TG_SECTION__KEY, double underscore separator)
#   2. User config file (~/.config/terminalghost/config.toml)
#   3. Built-in defaults (dataclass field defaults below)
#
# NOTE: the transport is TCP loopback (host/port), not a Unix domain socket,
# so the daemon runs on Windows as well as Linux/macOS.

from __future__ import annotations

import dataclasses
import logging
import os
import stat
import tomllib
from dataclasses import dataclass, field, fields

log = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = os.path.join("~", ".config", "terminalghost", "config.toml")

VALID_BACKENDS = ("ollama", "claude", "openai")
VALID_COLOR_MODES = ("auto", "always", "never")
VALID_THEMES = ("dark", "light", "high-contrast")


class ConfigError(Exception):
    """Raised for config validation failures."""


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://localhost:11434"
    model: str = "llama3"
    timeout_seconds: int = 60
    retries: int = 2


@dataclass(frozen=True)
class ClaudeConfig:
    api_key: str = ""  # prefer ANTHROPIC_API_KEY env var
    model: str = "claude-opus-4-8"
    max_tokens: int = 1024


@dataclass(frozen=True)
class OpenAIConfig:
    api_key: str = ""  # prefer OPENAI_API_KEY env var
    model: str = "gpt-4o-mini"
    # OpenAI-compatible endpoint — point this at Groq, LM Studio, llama.cpp, etc.
    base_url: str = "https://api.openai.com/v1"
    max_tokens: int = 1024


@dataclass(frozen=True)
class LLMConfig:
    backend: str = "ollama"
    allow_inline_context: bool = True
    # Seconds a previous ?? answer stays in context for follow-up questions
    # (0 disables conversational follow-ups).
    followup_seconds: int = 300
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    openai: OpenAIConfig = field(default_factory=OpenAIConfig)


@dataclass(frozen=True)
class CaptureConfig:
    redact_passwords: bool = True
    blocked_commands: list[str] = field(
        default_factory=lambda: ["gpg", "pass", "secret-tool"]
    )
    max_output_bytes: int = 4096
    use_pty: bool = False
    # Store command output when a hook sends it (opt-in; needs TG_CAPTURE_OUTPUT=1
    # in the POSIX hooks). Acts as a privacy gate: when false, any output a hook
    # sends is dropped before storage.
    capture_output: bool = False
    # Keep only the last N lines of captured output (before max_output_bytes).
    output_max_lines: int = 40


@dataclass(frozen=True)
class ContextConfig:
    tree_depth: int = 3
    tree_ignore: list[str] = field(
        default_factory=lambda: [".git", "__pycache__", "node_modules", ".venv"]
    )
    token_budget: int = 3000
    # Fold project facts (git branch/dirty state, manifest snippets like
    # package.json deps) into the prompt automatically.
    project_context: bool = True


@dataclass(frozen=True)
class GeneralConfig:
    history_size: int = 200
    db_path: str = "~/.local/share/terminalghost/history.db"
    # TCP loopback transport (cross-platform; replaces the Unix socket design)
    host: str = "127.0.0.1"
    port: int = 48632
    pid_file: str = "~/.local/share/terminalghost/terminalghost.pid"


@dataclass(frozen=True)
class UIConfig:
    # Color policy for terminal output: "auto" (color iff TTY), "always", "never".
    color: str = "auto"
    # Palette: "dark" | "light" | "high-contrast".
    theme: str = "dark"
    # Show the brand banner on init/start.
    banner: bool = True
    # Render streamed ?? answers as live markdown (vs. plain streamed text).
    markdown: bool = True
    # Ring the terminal bell (and send a desktop notification where available)
    # when an answer took longer than this many seconds. 0 disables it.
    notify_after_seconds: int = 20


@dataclass(frozen=True)
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ui: UIConfig = field(default_factory=UIConfig)


def load_config(path: str | None = None) -> Config:
    """Load and return the application Config.

    Resolution order:
      1. If `path` is given, read from that file (missing file is an error —
         an explicit path means the user intended a specific file).
      2. Otherwise try ~/.config/terminalghost/config.toml (missing → defaults).
    After reading TOML, overlay TG_* environment variables, validate, build.
    """
    raw: dict = {}
    resolved: str | None = None
    if path is not None:
        resolved = os.path.expanduser(path)
        if not os.path.isfile(resolved):
            raise ConfigError(f"config file not found: {path}")
    else:
        candidate = os.path.expanduser(DEFAULT_CONFIG_PATH)
        if os.path.isfile(candidate):
            resolved = candidate

    if resolved is not None:
        try:
            with open(resolved, "rb") as fh:
                raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"malformed TOML in {resolved}: {exc}") from exc
        _warn_if_world_readable(resolved, raw)

    raw = _overlay_env_vars(raw)
    _validate(raw)
    return _build_config(raw)


def _warn_if_world_readable(path: str, raw: dict) -> None:
    """Warn (don't fail) if a config file containing an api_key is world-readable."""
    if os.name != "posix":
        return  # POSIX permission bits are not meaningful on Windows
    has_key = any(
        isinstance(section, dict)
        and any(isinstance(v, dict) and v.get("api_key") for v in section.values())
        for section in raw.values()
    )
    if not has_key:
        return
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        log.warning(
            "%s contains an api_key and is group/world-readable; "
            "consider: chmod 600 %s",
            path,
            path,
        )


def _coerce(value: str):
    """Coerce an env var string: 'true'/'false' → bool, numeric → int, else str."""
    low = value.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    try:
        return int(value)
    except ValueError:
        return value


def _overlay_env_vars(raw: dict) -> dict:
    """Merge TG_SECTION__KEY env vars into the raw dict.

    Examples:
      TG_LLM__BACKEND=claude          → raw["llm"]["backend"] = "claude"
      TG_GENERAL__HISTORY_SIZE=500    → raw["general"]["history_size"] = 500
      TG_LLM__OLLAMA__MODEL=mistral   → raw["llm"]["ollama"]["model"] = "mistral"

    Non-coercible values are left as strings; _validate / _build_config raise
    ConfigError if the target field expects another type.
    """
    for name, value in os.environ.items():
        if not name.startswith("TG_") or "__" not in name:
            continue
        parts = [p.lower() for p in name[3:].split("__")]
        if not all(parts):
            continue  # malformed like TG___X
        node = raw
        for part in parts[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                existing = {}
                node[part] = existing
            node = existing
        node[parts[-1]] = _coerce(value)
    return raw


def _get(raw: dict, *keys):
    node = raw
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate(raw: dict) -> None:
    """Validate the raw config dict before constructing Config."""
    backend = _get(raw, "llm", "backend")
    if backend is not None and backend not in VALID_BACKENDS:
        raise ConfigError(
            f"llm.backend must be one of {VALID_BACKENDS}, got {backend!r}"
        )

    history_size = _get(raw, "general", "history_size")
    if history_size is not None:
        if not _is_int(history_size):
            raise ConfigError(
                f"general.history_size must be an integer, got {history_size!r}"
            )
        if not 1 <= history_size <= 10000:
            raise ConfigError(
                f"general.history_size must be in [1, 10000], got {history_size}"
            )

    tree_depth = _get(raw, "context", "tree_depth")
    if tree_depth is not None:
        if not _is_int(tree_depth):
            raise ConfigError(f"context.tree_depth must be an integer, got {tree_depth!r}")
        if not 1 <= tree_depth <= 10:
            raise ConfigError(f"context.tree_depth must be in [1, 10], got {tree_depth}")

    token_budget = _get(raw, "context", "token_budget")
    if token_budget is not None:
        if not _is_int(token_budget):
            raise ConfigError(
                f"context.token_budget must be an integer, got {token_budget!r}"
            )
        if token_budget < 100:
            raise ConfigError(f"context.token_budget must be >= 100, got {token_budget}")

    port = _get(raw, "general", "port")
    if port is not None:
        if not _is_int(port):
            raise ConfigError(f"general.port must be an integer, got {port!r}")
        # 0 = "pick a free port" (the daemon writes the chosen one to a runtime file).
        if port != 0 and not 1024 <= port <= 65535:
            raise ConfigError(f"general.port must be 0 or in [1024, 65535], got {port}")

    color = _get(raw, "ui", "color")
    if color is not None and color not in VALID_COLOR_MODES:
        raise ConfigError(
            f"ui.color must be one of {VALID_COLOR_MODES}, got {color!r}"
        )

    theme = _get(raw, "ui", "theme")
    if theme is not None and theme not in VALID_THEMES:
        raise ConfigError(f"ui.theme must be one of {VALID_THEMES}, got {theme!r}")


def _make(cls, section) -> object:
    """Build one section dataclass from a raw dict.

    Unknown keys are ignored (forward-compat). Values are type-checked against
    the field's default value type; mismatches raise ConfigError.
    """
    if section is None:
        section = {}
    if not isinstance(section, dict):
        raise ConfigError(f"config section for {cls.__name__} must be a table")
    known = {f.name for f in fields(cls)}
    defaults = cls()
    kwargs = {}
    for name, value in section.items():
        if name not in known:
            continue
        default = getattr(defaults, name)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                raise ConfigError(f"{cls.__name__}.{name} must be a boolean, got {value!r}")
        elif isinstance(default, int):
            if not _is_int(value):
                raise ConfigError(f"{cls.__name__}.{name} must be an integer, got {value!r}")
        elif isinstance(default, str):
            if not isinstance(value, str):
                raise ConfigError(f"{cls.__name__}.{name} must be a string, got {value!r}")
        elif isinstance(default, list):
            if not isinstance(value, list):
                raise ConfigError(f"{cls.__name__}.{name} must be a list, got {value!r}")
        kwargs[name] = value
    return cls(**kwargs)


def _build_config(raw: dict) -> Config:
    general: GeneralConfig = _make(GeneralConfig, raw.get("general"))  # type: ignore[assignment]
    # Expand ~ in filesystem paths once, at load time.
    general = dataclasses.replace(
        general,
        db_path=os.path.expanduser(general.db_path),
        pid_file=os.path.expanduser(general.pid_file),
    )
    capture: CaptureConfig = _make(CaptureConfig, raw.get("capture"))  # type: ignore[assignment]
    context: ContextConfig = _make(ContextConfig, raw.get("context"))  # type: ignore[assignment]

    llm_raw = raw.get("llm") or {}
    if not isinstance(llm_raw, dict):
        raise ConfigError("llm section must be a table")
    llm_flat = {k: v for k, v in llm_raw.items() if not isinstance(v, dict)}
    llm: LLMConfig = _make(LLMConfig, llm_flat)  # type: ignore[assignment]
    llm = dataclasses.replace(
        llm,
        ollama=_make(OllamaConfig, llm_raw.get("ollama")),  # type: ignore[arg-type]
        claude=_make(ClaudeConfig, llm_raw.get("claude")),  # type: ignore[arg-type]
        openai=_make(OpenAIConfig, llm_raw.get("openai")),  # type: ignore[arg-type]
    )

    ui: UIConfig = _make(UIConfig, raw.get("ui"))  # type: ignore[assignment]

    return Config(general=general, capture=capture, context=context, llm=llm, ui=ui)
