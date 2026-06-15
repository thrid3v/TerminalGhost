# terminalghost.cli.settings
#
# Small helpers for the `theme` / `use` commands to persist a single config
# value while preserving the rest of the file (comments included). tomllib is
# read-only and we don't want a TOML-writer dependency, so this does a careful
# line-based update of `key = value` within `[section]`.

from __future__ import annotations

import os
import re

from terminalghost.config import loader


def default_config_path() -> str:
    return os.path.expanduser(loader.DEFAULT_CONFIG_PATH)


def _format(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace('"', '\\"') + '"'
    return str(value)


def update_toml_text(text: str, section: str, key: str, value) -> str:
    """Return `text` with `[section] key` set to `value` (added if missing)."""
    rendered = f"{key} = {_format(value)}"
    header = f"[{section}]"
    key_re = re.compile(r"^\s*" + re.escape(key) + r"\s*=")
    section_re = re.compile(r"^\s*\[")

    out: list[str] = []
    in_section = False
    found_section = False
    written = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == header:
            in_section, found_section = True, True
            out.append(line)
            continue
        if in_section and section_re.match(stripped) and stripped != header:
            if not written:  # leaving the section without seeing the key
                out.append(rendered)
                written = True
            in_section = False
            out.append(line)
            continue
        if in_section and key_re.match(line) and not written:
            out.append(rendered)
            written = True
            continue
        out.append(line)

    if not found_section:
        if out and out[-1].strip() != "":
            out.append("")
        out.extend([header, rendered])
    elif not written:  # section ran to EOF without the key
        out.append(rendered)
    return "\n".join(out) + "\n"


def set_value(config_path: str | None, section: str, key: str, value) -> str:
    """Persist one config value, returning the path written."""
    path = os.path.expanduser(config_path) if config_path else default_config_path()
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = "# TerminalGhost configuration\n"
    updated = update_toml_text(text, section, key, value)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return path


def cmd_use(config, config_path: str | None, target: str) -> int:
    """Switch LLM backend/model: `use ollama:mistral`, `use claude`, `use openai`."""
    from terminalghost.cli.client import _request
    from terminalghost.config.loader import VALID_BACKENDS
    from terminalghost.ui import console_for

    console = console_for(config)
    backend, _, model = target.partition(":")
    backend = backend.strip().lower()
    model = model.strip()
    if backend not in VALID_BACKENDS:
        console.print(
            f"[tg.error]Unknown backend {backend!r}.[/] Choose one of: "
            + ", ".join(VALID_BACKENDS)
        )
        return 1

    path = set_value(config_path, "llm", "backend", backend)
    if model:
        set_value(config_path, f"llm.{backend}", "model", model)

    label = f"{backend}:{model}" if model else backend
    # Ask the running daemon to hot-reload (works on Windows too — no SIGHUP).
    reloaded = _request(config, {"type": "reload"}, connect_timeout=2, read_timeout=5)
    console.print(f"[tg.success]✓[/] Now using [tg.key]{label}[/] ([tg.muted]{path}[/])")
    if reloaded.strip() != "ok":
        console.print(
            "[tg.muted]Daemon not running (or didn't confirm) — "
            "restart it to apply: terminalghost restart[/]"
        )
    return 0


def cmd_theme(config, config_path: str | None, name: str) -> int:
    from terminalghost import __version__
    from terminalghost.config.loader import VALID_THEMES
    from terminalghost.ui import get_console, render_banner

    if name not in VALID_THEMES:
        get_console(color=config.ui.color, stderr=True).print(
            f"[tg.error]Unknown theme {name!r}.[/] Choose one of: "
            + ", ".join(VALID_THEMES)
        )
        return 1
    path = set_value(config_path, "ui", "theme", name)
    # Preview with the newly chosen theme.
    console = get_console(color=config.ui.color, theme=name)
    console.print(render_banner(__version__, backend=config.llm.backend))
    console.print(f"[tg.success]✓[/] Theme set to [tg.key]{name}[/] ([tg.muted]{path}[/])")
    return 0
