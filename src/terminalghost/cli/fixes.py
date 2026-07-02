# terminalghost.cli.fixes
#
# The saved-fixes library: `terminalghost save <name>` pins the last ??
# suggestion under a name; `terminalghost fixes` lists them; `terminalghost
# apply <name>` (alias `tga <name>`) replays one. Over time this becomes a
# personal, searchable runbook — stickier than one-off Q&A.

from __future__ import annotations

import time


def _open_db(config):
    from terminalghost.storage.db import Database

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    db.open()
    return db


def cmd_save(config, name: str, note: str = "") -> int:
    """Save the last suggested fix (from the daemon) under `name`."""
    import os

    from rich.markup import escape

    from terminalghost.cli.client import _suggested_commands
    from terminalghost.ui import console_for

    console = console_for(config)
    commands = _suggested_commands(config)
    if not commands:
        console.print(
            "[tg.muted]Nothing to save yet — ask a [tg.key]??[/] first, then "
            "[tg.key]terminalghost save <name>[/] while the suggestion is fresh.[/]"
        )
        return 1
    try:
        db = _open_db(config)
    except Exception as exc:  # noqa: BLE001 — friendly message, not a traceback
        console.print(f"[tg.error]Could not open the database:[/] {exc}")
        return 1
    try:
        db.save_fix(name, commands, note=note, cwd=os.getcwd())
    except ValueError as exc:
        console.print(f"[tg.error]{exc}[/]")
        return 2
    finally:
        db.close()
    steps = f" ({len(commands)} steps)" if len(commands) > 1 else ""
    console.print(
        f"[tg.success]✓[/] Saved [tg.key]{escape(name)}[/]{steps} — run it anytime "
        f"with [tg.key]tga {escape(name)}[/]"
    )
    return 0


def cmd_fixes(config, grep: str | None = None, delete: str | None = None) -> int:
    """List saved fixes (or delete one with --delete)."""
    from rich.markup import escape
    from rich.table import Table

    from terminalghost.ui import console_for

    console = console_for(config)
    try:
        db = _open_db(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[tg.error]Could not open the database:[/] {exc}")
        return 1
    try:
        if delete is not None:
            if db.delete_fix(delete):
                console.print(f"[tg.success]✓[/] Deleted fix [tg.key]{escape(delete)}[/]")
                return 0
            console.print(f"[tg.muted]No saved fix named {escape(delete)!s}.[/]")
            return 1
        fixes = db.list_fixes(grep=grep)
    finally:
        db.close()

    if not fixes:
        console.print(
            "[tg.muted]No saved fixes"
            + (" match that" if grep else "")
            + " yet. After a good answer: [tg.key]terminalghost save <name>[/][/]"
        )
        return 0
    table = Table(expand=True, border_style="tg.muted", header_style="tg.header")
    table.add_column("name", style="tg.key", no_wrap=True)
    table.add_column("commands", style="tg.cmd", overflow="fold")
    table.add_column("note", style="tg.muted", overflow="fold")
    table.add_column("last used", style="tg.muted", no_wrap=True)
    for fix in fixes:
        when = (
            time.strftime("%Y-%m-%d", time.localtime(fix.last_used_ts))
            if fix.last_used_ts else "never"
        )
        table.add_row(
            escape(fix.name),
            escape("\n".join(fix.commands)),
            escape(fix.note),
            when,
        )
    console.print(table)
    return 0


def load_fix_commands(config, name: str) -> list[str] | None:
    """Commands for a saved fix (marking it used), or None if unknown."""
    try:
        db = _open_db(config)
    except Exception:  # noqa: BLE001
        return None
    try:
        fix = db.get_fix(name)
        if fix is None:
            return None
        db.touch_fix(name)
        return fix.commands
    finally:
        db.close()
