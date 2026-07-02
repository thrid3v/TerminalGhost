# terminalghost.cli.logview
#
# `terminalghost log` — show the most recent captured commands as a table —
# and `terminalghost clear` — wipe captured history. Both open a second
# SQLite connection (WAL allows concurrent access while the daemon runs).

from __future__ import annotations

import os
import re
import sys
import time

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")
_SINCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text: str) -> float:
    """Parse a duration like 90s / 15m / 2h / 3d into a minimum epoch ts.

    Raises ValueError for anything else.
    """
    match = _SINCE_RE.match(text.strip().lower())
    if not match:
        raise ValueError(
            f"invalid duration {text!r}: use <N>s, <N>m, <N>h, or <N>d (e.g. 2h)"
        )
    return time.time() - int(match.group(1)) * _SINCE_UNITS[match.group(2)]


def cmd_log(
    config,
    limit: int,
    *,
    failed: bool = False,
    cwd: str | None = None,
    since: str | None = None,
    grep: str | None = None,
) -> int:
    from rich.table import Table

    from terminalghost.storage.db import Database
    from terminalghost.ui import console_for

    console = console_for(config)
    limit = max(1, min(limit, config.general.history_size))

    since_ts: float | None = None
    if since is not None:
        try:
            since_ts = parse_since(since)
        except ValueError as exc:
            console.print(f"[tg.error]{exc}[/]")
            return 2
    if cwd is not None:
        # "." (the bare --cwd form) means "this directory".
        cwd = os.path.abspath(os.path.expanduser(cwd))

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    try:
        db.open()
        events = db.get_recent_commands(
            limit=limit, failed_only=failed, cwd=cwd, since=since_ts, grep=grep,
        )
    except Exception as exc:  # noqa: BLE001 — show a friendly message, not a traceback
        console.print(f"[tg.error]Could not read history:[/] {exc}")
        return 1
    finally:
        db.close()

    if not events:
        filtered = failed or cwd is not None or since is not None or grep is not None
        console.print(
            "[tg.muted]No matching commands.[/]" if filtered
            else "[tg.muted]No commands captured yet.[/]"
        )
        return 0

    table = Table(title=None, expand=True, border_style="tg.muted", header_style="tg.header")
    table.add_column("time", style="tg.muted", no_wrap=True)
    table.add_column("exit", justify="right", no_wrap=True)
    table.add_column("ms", justify="right", style="tg.muted", no_wrap=True)
    table.add_column("dir", style="tg.muted", no_wrap=True, max_width=24)
    table.add_column("command", style="tg.cmd", overflow="fold")

    from rich.markup import escape

    for ev in reversed(events):  # oldest first, newest at the bottom
        when = time.strftime("%H:%M:%S", time.localtime(ev.ts))
        exit_style = "tg.ok" if ev.exit_code == 0 else "tg.fail"
        # Escape so brackets in real commands (pip install "pkg[extra]") don't
        # get parsed as Rich markup, then make redactions visible at a glance.
        cmd_text = escape(ev.cmd).replace(
            "<redacted>", "[tg.warn]<redacted>[/tg.warn]"
        )
        table.add_row(
            when,
            f"[{exit_style}]{ev.exit_code}[/]",
            str(ev.duration_ms),
            _short_dir(ev.cwd),
            cmd_text,
        )

    console.print(table)
    return 0


def _short_dir(path: str) -> str:
    """Trailing path component(s), enough to recognize without the full path."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else (parts[-1] or path)


def cmd_clear(config, last: int | None, assume_yes: bool) -> int:
    """Delete captured command history (all of it, or just the last N).

    A tool that records shell commands needs a one-command "forget" — e.g.
    right after a secret was typed. VACUUMs so the data leaves the file.
    """
    from terminalghost.storage.db import Database
    from terminalghost.ui import console_for

    console = console_for(config)
    scope = f"the last {last} command(s)" if last is not None else "ALL captured history"
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print(
                "[tg.warn]Refusing to clear without confirmation.[/] "
                "Re-run with [tg.key]--yes[/]."
            )
            return 1
        try:
            answer = input(f"Delete {scope}? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer not in ("y", "yes"):
            console.print("[tg.muted]Nothing deleted.[/]")
            return 0

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    try:
        db.open()
        deleted = db.clear_commands(last=last)
    except Exception as exc:  # noqa: BLE001 — friendly message, not a traceback
        console.print(f"[tg.error]Could not clear history:[/] {exc}")
        return 1
    finally:
        db.close()

    if deleted == 0:
        console.print("[tg.muted]History was already empty.[/]")
    else:
        console.print(f"[tg.success]✓[/] Deleted {deleted} command(s).")
    return 0
