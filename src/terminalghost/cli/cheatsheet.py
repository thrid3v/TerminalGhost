# terminalghost.cli.cheatsheet
#
# `terminalghost cheatsheet` — a quick, scannable reference of what you can do.
# Surfaced from `init` so the features are discoverable.

from __future__ import annotations

# (command, what it does)
_ROWS = [
    ("qq", "ask about your last failure / recent commands"),
    ("qq <question>", "ask anything, with your shell context"),
    ("qq fix", "reply with just the corrected command"),
    ("qq explain", "a fuller explanation"),
    ("tgr <cmd>", "run a command so its output is captured for the next qq"),
    ("tga", "run the command TerminalGhost last suggested"),
    ("cmd | tg explain", "explain piped output or a file"),
    ("terminalghost dashboard", "full-screen view of captured commands + status"),
    ("terminalghost use <backend>", "switch LLM, e.g. use ollama:mistral or use claude"),
    ("terminalghost theme <name>", "dark | light | high-contrast"),
    ("terminalghost log --grep <text>", "search history; also --failed, --cwd, --since 2h"),
    ("terminalghost clear [--last N]", "forget captured history (all or the last N)"),
    ("terminalghost export / import", "carry history between machines as a JSON snapshot"),
    ("terminalghost redact-check <cmd>", "preview what would be stored (nothing is saved)"),
    ("terminalghost doctor", "diagnose the install and print fixes"),
]


def cmd_cheatsheet(config) -> int:
    from rich.table import Table

    from terminalghost import __version__
    from terminalghost.ui import console_for, render_banner

    console = console_for(config)
    console.print(render_banner(__version__, backend=config.llm.backend))
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0))
    table.add_column(style="tg.key", no_wrap=True)
    table.add_column(style="tg.muted")
    for cmd, desc in _ROWS:
        table.add_row(cmd, desc)
    console.print(table)
    console.print(
        "\n[tg.muted]On PowerShell 7 use [tg.key]qq[/]; in bash [tg.key]??[/] can glob, "
        "so [tg.key]tg[/] is the safe alias.[/]"
    )
    return 0
