# terminalghost.cli.logview
#
# `terminalghost log` — show the most recent captured commands as a table.
# Read-only; opens a second SQLite connection (WAL allows concurrent readers
# while the daemon is running).

from __future__ import annotations

import time


def cmd_log(config, limit: int) -> int:
    from rich.table import Table

    from terminalghost.storage.db import Database
    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color)
    limit = max(1, min(limit, config.general.history_size))

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    try:
        db.open()
        events = db.get_recent_commands(limit=limit)
    except Exception as exc:  # noqa: BLE001 — show a friendly message, not a traceback
        console.print(f"[tg.error]Could not read history:[/] {exc}")
        return 1
    finally:
        db.close()

    if not events:
        console.print("[tg.muted]No commands captured yet.[/]")
        return 0

    table = Table(title=None, expand=True, border_style="tg.muted", header_style="tg.header")
    table.add_column("time", style="tg.muted", no_wrap=True)
    table.add_column("exit", justify="right", no_wrap=True)
    table.add_column("ms", justify="right", style="tg.muted", no_wrap=True)
    table.add_column("dir", style="tg.muted", no_wrap=True, max_width=24)
    table.add_column("command", style="tg.cmd", overflow="fold")

    for ev in reversed(events):  # oldest first, newest at the bottom
        when = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        exit_style = "tg.ok" if ev.exit_code == 0 else "tg.fail"
        table.add_row(
            when,
            f"[{exit_style}]{ev.exit_code}[/]",
            str(ev.duration_ms),
            _short_dir(ev.cwd),
            ev.cmd,
        )

    console.print(table)
    return 0


def _short_dir(path: str) -> str:
    """Trailing path component(s), enough to recognize without the full path."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else (parts[-1] or path)
