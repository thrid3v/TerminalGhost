# terminalghost.ui.theme
#
# The brand: a single Rich Theme plus the named styles used everywhere. The
# palette is a soft "ghost glow" — cyan primary with a violet accent — so the
# tool reads as one coherent thing across banner, answers, status, and errors.

from __future__ import annotations

from rich.theme import Theme

# The ghost marker shown before answers and in the banner. Box-drawing-safe;
# Rich substitutes a fallback on consoles that cannot encode it.
GHOST_GLYPH = "▟"

TG_THEME = Theme(
    {
        "tg.brand": "bold cyan",
        "tg.accent": "magenta",
        "tg.glow": "cyan",
        "tg.muted": "dim",
        "tg.success": "bold green",
        "tg.error": "bold red",
        "tg.warn": "yellow",
        "tg.ok": "green",
        "tg.fail": "bold red",
        "tg.key": "bold cyan",
        "tg.cmd": "bold bright_white",
        "tg.footer": "dim cyan",
        "tg.header": "bold cyan",
    }
)

# ASCII ghost mascot for the banner — safe on every console encoding.
GHOST_ART = r""" .-.
(o o)
|=#=|
 '-'"""
