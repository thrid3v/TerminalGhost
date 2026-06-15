# terminalghost.ui.theme
#
# The brand: a single Rich Theme plus the named styles used everywhere. The
# palette is a soft "ghost glow" — cyan primary with a violet accent — so the
# tool reads as one coherent thing across banner, answers, status, and errors.

from __future__ import annotations

from rich.theme import Theme

# The ghost mascot shown before answers and in the banner. CLI output is forced
# to UTF-8 (see daemon.process._force_utf8_io), so the emoji renders on modern
# terminals and degrades to a replacement char (never a crash) on legacy ones.
GHOST_GLYPH = "👻"

# Palettes share the same `tg.*` style names so every surface (banner, answers,
# doctor, dashboard) restyles by swapping the theme. Status uses ✓/✗/! glyphs as
# well as color, so the high-contrast theme stays readable for colorblind users.
_PALETTES: dict[str, dict[str, str]] = {
    "dark": {
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
    },
    # Tuned for light backgrounds — no bright_white, darker accents.
    "light": {
        "tg.brand": "bold blue",
        "tg.accent": "magenta",
        "tg.glow": "blue",
        "tg.muted": "grey42",
        "tg.success": "bold green4",
        "tg.error": "bold red3",
        "tg.warn": "dark_orange3",
        "tg.ok": "green4",
        "tg.fail": "bold red3",
        "tg.key": "bold blue",
        "tg.cmd": "bold black",
        "tg.footer": "blue",
        "tg.header": "bold blue",
    },
    # High luminance + bold everywhere; cyan/yellow (colorblind-safe) for good/bad.
    "high-contrast": {
        "tg.brand": "bold bright_white",
        "tg.accent": "bold bright_cyan",
        "tg.glow": "bold bright_cyan",
        "tg.muted": "white",
        "tg.success": "bold bright_cyan",
        "tg.error": "bold bright_yellow",
        "tg.warn": "bold bright_yellow",
        "tg.ok": "bold bright_cyan",
        "tg.fail": "bold bright_yellow",
        "tg.key": "bold bright_cyan",
        "tg.cmd": "bold bright_white",
        "tg.footer": "bright_white",
        "tg.header": "bold bright_white",
    },
}

THEMES: dict[str, Theme] = {name: Theme(styles) for name, styles in _PALETTES.items()}
THEME_NAMES = tuple(THEMES)

# Back-compat default used by tests and any caller that doesn't pass a theme.
TG_THEME = THEMES["dark"]


def get_theme(name: str) -> Theme:
    """Return the named theme, falling back to the dark default."""
    return THEMES.get(name, THEMES["dark"])

# ASCII ghost mascot for the banner — safe on every console encoding.
GHOST_ART = r""" .-.
(o o)
|=#=|
 '-'"""
