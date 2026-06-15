# terminalghost.ui.console
#
# Console construction and color resolution. Everything that prints to the
# user goes through get_console(); the streaming `ask` client (which writes
# raw bytes, not via a Console) uses resolve_color() to decide on ANSI.

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, TextIO

from rich.console import Console

from terminalghost.ui.theme import get_theme

if TYPE_CHECKING:
    from terminalghost.config.loader import Config


def resolve_color(mode: str = "auto", stream: TextIO | None = None) -> bool:
    """Decide whether to emit ANSI color.

    Precedence: an explicit `never`/`always` mode is the user's stated intent
    and wins over everything (including NO_COLOR). Otherwise (`auto`) the
    NO_COLOR env var (https://no-color.org) forces off, and failing that color
    is on iff the stream is a TTY.
    """
    mode = (mode or "auto").lower()
    if mode == "never":
        return False
    if mode == "always":
        return True
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def get_console(
    *, color: str = "auto", theme: str = "dark", stderr: bool = False,
    record: bool = False,
) -> Console:
    """A themed Rich Console with our color + theme policy applied.

    `color` is the config.ui.color mode ("auto" | "always" | "never");
    `theme` is the config.ui.theme name ("dark" | "light" | "high-contrast").
    """
    stream = sys.stderr if stderr else sys.stdout
    enabled = resolve_color(color, stream)
    return Console(
        theme=get_theme(theme),
        stderr=stderr,
        highlight=False,
        no_color=not enabled,
        force_terminal=True if (color == "always") else None,
        emoji=False,
        record=record,
    )


def console_for(config: "Config", *, stderr: bool = False, record: bool = False) -> Console:
    """A console themed from `config` (color + theme) — the usual entry point."""
    return get_console(
        color=config.ui.color, theme=config.ui.theme, stderr=stderr, record=record
    )
