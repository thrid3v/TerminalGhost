# terminalghost.ui.banner
#
# The identity beat shown by `init`, `start`, `cheatsheet`, and `theme`. One
# soft rounded frame: a ghost mark, a weight-split wordmark (fog "Terminal",
# glowing "Ghost"), a one-line invitation, and a status line. Deliberately
# typographic rather than ASCII-art — the personality is in the weight split
# and the spectral palette, not in a janky face.

from __future__ import annotations

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from terminalghost.ui.theme import GHOST_GLYPH


def render_banner(version: str, *, backend: str | None = None) -> Panel:
    """Build the brand banner as a renderable Panel.

    `backend` (when given) is shown in the status line so users can see at a
    glance which LLM is wired up.
    """
    wordmark = Text()
    wordmark.append("Terminal", style="tg.muted")
    wordmark.append("Ghost", style="tg.brand")

    invite = Text("a ghost in your shell — summon it with ", style="tg.muted")
    invite.append("??", style="tg.key")

    status = Text()
    status.append("● ", style="tg.accent")
    parts = ["local"]
    if backend:
        parts.append(backend)
    parts.append(f"v{version}")
    status.append("  ·  ".join(parts), style="tg.subtle")

    lines = Table.grid()
    lines.add_column()
    lines.add_row(wordmark)
    lines.add_row(invite)
    lines.add_row(status)

    layout = Table.grid(padding=(0, 2))
    layout.add_column(justify="center", vertical="middle")
    layout.add_column()
    layout.add_row(Text(GHOST_GLYPH, style="tg.accent"), lines)

    return Panel(
        layout,
        border_style="tg.glow",
        box=box.ROUNDED,
        padding=(1, 3),
        expand=False,
    )
