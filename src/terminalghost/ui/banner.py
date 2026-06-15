# terminalghost.ui.banner
#
# The startup banner shown by `init`, `start`, and `--help`-style moments.
# A rounded panel with the ASCII ghost mascot and a one-line identity, in the
# spirit of a CLI that greets you when it wakes up.

from __future__ import annotations

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from terminalghost.ui.theme import GHOST_GLYPH


def render_banner(version: str, *, backend: str | None = None) -> Panel:
    """Build the brand banner as a renderable Panel.

    `backend` (when given) is shown in the status line so users can see at a
    glance which LLM is wired up.
    """
    ghost = Text(f" {GHOST_GLYPH} ", style="tg.glow")

    title = Text()
    title.append("Terminal", style="tg.brand")
    title.append("Ghost", style="tg.accent")

    tagline = Text("ask your terminal anything — just type ", style="tg.muted")
    tagline.append("??", style="tg.key")

    status = Text(f"local-first · v{version}", style="tg.footer")
    if backend:
        status = Text(f"local-first · {backend} · v{version}", style="tg.footer")

    text_block = Table.grid(padding=0)
    text_block.add_row(title)
    text_block.add_row(tagline)
    text_block.add_row(status)

    layout = Table.grid(padding=(0, 2))
    layout.add_column(justify="center", vertical="middle")
    layout.add_column(justify="left")
    layout.add_row(Align.center(ghost, vertical="middle"), text_block)

    return Panel(
        layout,
        border_style="tg.glow",
        padding=(0, 2),
        expand=False,
    )
