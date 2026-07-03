# terminalghost.ui.theme
#
# The brand: a single Rich Theme plus the named styles used everywhere.
#
# Identity — "Séance": a ghost that haunts your shell, summoned by ??, that
# speaks and then fades. The palette is spectral rather than the usual
# hacker-terminal cyan: moonlit violet for the ghost's voice, ectoplasm mint
# for anything alive/ok (and the glowing rail beside a runnable fix), candle
# amber for caution, wither rose for failure. Every surface restyles by
# swapping one Theme, so the tool reads as one coherent presence.

from __future__ import annotations

from rich.theme import Theme

# The ghost mark shown before answers and in the banner. CLI output is forced
# to UTF-8 (see daemon.process._force_utf8_io), so the emoji renders on modern
# terminals and degrades to a replacement char (never a crash) on legacy ones.
GHOST_GLYPH = "👻"

# The glowing left rail drawn beside a runnable command (answers / action bar).
RAIL_GLYPH = "▎"

# Palettes share the same `tg.*` style names so every surface (banner, answers,
# doctor, dashboard) restyles by swapping the theme. Status pairs a glyph with
# color (✓ / ✗ / ○) so it stays readable for colorblind users. All three
# palettes MUST define the same keys — a test enforces parity.
_PALETTES: dict[str, dict[str, str]] = {
    # Spectral: moonlit violet + ectoplasm mint on near-black.
    "dark": {
        "tg.brand": "bold #A594FF",     # violet — the "Ghost" wordmark, keys to type
        "tg.accent": "#5FE3B3",         # ectoplasm mint — presence / alive
        "tg.glow": "#7E6FE0",           # soft violet — panel borders
        "tg.rail": "#5FE3B3",           # the glowing fix-rail (mint)
        "tg.muted": "#828AA0",          # fog — secondary text
        "tg.subtle": "#5C6479",         # deep fog — subtitles, footers, metadata
        "tg.eyebrow": "#5FA98C",        # dim mint — section labels
        "tg.success": "bold #5FE3B3",
        "tg.error": "bold #FF7A93",     # wither rose
        "tg.warn": "#F2B25C",           # candle amber
        "tg.ok": "#5FE3B3",
        "tg.fail": "bold #FF7A93",
        "tg.key": "bold #A594FF",
        "tg.cmd": "bold #F2F2F8",       # voice — the runnable text you'd type
        "tg.footer": "#5C6479",
        "tg.header": "bold #C9BEFF",    # moonlight
        # Markdown internals, so streamed answers match the palette.
        "markdown.code": "#5FE3B3",
        "markdown.h1": "bold #C9BEFF",
        "markdown.h2": "bold #C9BEFF",
        "markdown.h3": "bold #C9BEFF",
        "markdown.item.bullet": "bold #A594FF",
        "markdown.link": "#5FE3B3",
        "markdown.link_url": "#5FA98C",
    },
    # Tuned for light backgrounds — deeper, saturated versions so the spectral
    # hues survive on white without glowing pastels washing out.
    "light": {
        "tg.brand": "bold #6D5DD3",
        "tg.accent": "#0E8A62",
        "tg.glow": "#6D5DD3",
        "tg.rail": "#0E8A62",
        "tg.muted": "#5B6270",
        "tg.subtle": "#8A909C",
        "tg.eyebrow": "#3F7A66",
        "tg.success": "bold #0E8A62",
        "tg.error": "bold #C2255C",
        "tg.warn": "#B7791F",
        "tg.ok": "#0E8A62",
        "tg.fail": "bold #C2255C",
        "tg.key": "bold #6D5DD3",
        "tg.cmd": "bold #14161C",
        "tg.footer": "#8A909C",
        "tg.header": "bold #5A4BC0",
        "markdown.code": "#0E8A62",
        "markdown.h1": "bold #5A4BC0",
        "markdown.h2": "bold #5A4BC0",
        "markdown.h3": "bold #5A4BC0",
        "markdown.item.bullet": "bold #6D5DD3",
        "markdown.link": "#0E8A62",
        "markdown.link_url": "#3F7A66",
    },
    # High luminance + bold; cyan/yellow (colorblind-safe) for good/bad.
    "high-contrast": {
        "tg.brand": "bold bright_white",
        "tg.accent": "bold bright_cyan",
        "tg.glow": "bold bright_cyan",
        "tg.rail": "bold bright_cyan",
        "tg.muted": "white",
        "tg.subtle": "white",
        "tg.eyebrow": "bold bright_cyan",
        "tg.success": "bold bright_cyan",
        "tg.error": "bold bright_yellow",
        "tg.warn": "bold bright_yellow",
        "tg.ok": "bold bright_cyan",
        "tg.fail": "bold bright_yellow",
        "tg.key": "bold bright_cyan",
        "tg.cmd": "bold bright_white",
        "tg.footer": "white",
        "tg.header": "bold bright_white",
        "markdown.code": "bold bright_cyan",
        "markdown.h1": "bold bright_white",
        "markdown.h2": "bold bright_white",
        "markdown.h3": "bold bright_white",
        "markdown.item.bullet": "bold bright_cyan",
        "markdown.link": "bold bright_cyan",
        "markdown.link_url": "bright_cyan",
    },
}

THEMES: dict[str, Theme] = {name: Theme(styles) for name, styles in _PALETTES.items()}
THEME_NAMES = tuple(THEMES)

# Back-compat default used by tests and any caller that doesn't pass a theme.
TG_THEME = THEMES["dark"]


def get_theme(name: str) -> Theme:
    """Return the named theme, falling back to the dark default."""
    return THEMES.get(name, THEMES["dark"])
