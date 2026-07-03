# terminalghost.ui
#
# Terminal presentation layer (Rich): brand theme, color resolution, the
# startup banner, and themed status/error helpers. Kept free of business
# logic so the daemon/storage/llm layers never import Rich.

from __future__ import annotations

from terminalghost.ui.banner import render_banner
from terminalghost.ui.console import console_for, get_console, resolve_color
from terminalghost.ui.markdown import ghost_markdown
from terminalghost.ui.theme import GHOST_GLYPH, RAIL_GLYPH, THEME_NAMES, TG_THEME

__all__ = [
    "GHOST_GLYPH",
    "RAIL_GLYPH",
    "THEME_NAMES",
    "TG_THEME",
    "console_for",
    "get_console",
    "ghost_markdown",
    "render_banner",
    "resolve_color",
]
