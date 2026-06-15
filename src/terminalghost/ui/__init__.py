# terminalghost.ui
#
# Terminal presentation layer (Rich): brand theme, color resolution, the
# startup banner, and themed status/error helpers. Kept free of business
# logic so the daemon/storage/llm layers never import Rich.

from __future__ import annotations

from terminalghost.ui.banner import render_banner
from terminalghost.ui.console import get_console, resolve_color
from terminalghost.ui.theme import GHOST_GLYPH, TG_THEME

__all__ = [
    "GHOST_GLYPH",
    "TG_THEME",
    "get_console",
    "render_banner",
    "resolve_color",
]
