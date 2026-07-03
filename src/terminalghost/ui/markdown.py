# terminalghost.ui.markdown
#
# Ghost-flavored Markdown rendering for streamed answers. The signature move:
# a fenced command block is the one thing the user will actually *run*, so it
# gets a glowing mint left-rail and a quiet language eyebrow — the payload
# glows, everything around it stays calm. Inline code is plain mint (no muddy
# grey chips). Works mid-stream because Markdown re-parses the buffer each
# chunk and uses our element class.

from __future__ import annotations

from rich.markdown import CodeBlock, Markdown
from rich.table import Table
from rich.text import Text

from terminalghost.ui.theme import RAIL_GLYPH


class GhostCodeBlock(CodeBlock):
    """A fenced code block rendered as a mint rail + language eyebrow.

    We render the code as clean near-white "voice" text rather than rainbow
    syntax highlighting: a fix is something you read and run, and legibility
    beats decoration here.
    """

    def __rich_console__(self, console, options):
        code = str(self.text).rstrip("\n")
        lang = (self.lexer_name or "").strip().lower()
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="tg.rail", no_wrap=True)
        grid.add_column(style="tg.cmd", overflow="fold")
        if lang and lang != "text":
            grid.add_row(" ", Text(lang, style="tg.eyebrow"))
        for line in code.splitlines() or [""]:
            grid.add_row(RAIL_GLYPH, line)
        yield grid


def ghost_markdown(source: str) -> Markdown:
    """A Markdown renderable using the ghost code-block treatment."""
    md = Markdown(source, code_theme="github-dark")
    # Copy so we never mutate Rich's shared class-level element registry.
    md.elements = dict(md.elements)
    md.elements["code_block"] = GhostCodeBlock
    md.elements["fence"] = GhostCodeBlock
    return md
