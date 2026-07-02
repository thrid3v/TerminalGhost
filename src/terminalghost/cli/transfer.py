# terminalghost.cli.transfer
#
# `terminalghost export` / `import` — carry captured history between machines
# as a plain JSON snapshot (laptop → remote box, old machine → new machine).
# Deliberately not a sync protocol: one file, explicit, inspectable.
#
# Exports contain whatever the capture pipeline stored — i.e. already-redacted
# command text — so a snapshot is exactly as sensitive as the local database.
# Imports re-run command redaction anyway (snapshots may come from an older
# build or an untrusted source).

from __future__ import annotations

import json
import sys
import time

EXPORT_FORMAT = 1


def _open_db(config):
    from terminalghost.storage.db import Database

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    db.open()
    return db


def cmd_export(config, out: str | None) -> int:
    """Dump captured history as JSON to `out` (or stdout for piping)."""
    from terminalghost.ui import console_for

    console = console_for(config, stderr=True)
    try:
        db = _open_db(config)
    except Exception as exc:  # noqa: BLE001 — friendly message, not a traceback
        console.print(f"[tg.error]Could not open history:[/] {exc}")
        return 1
    try:
        # newest-first from the DB; export oldest-first so re-import preserves order
        events = list(reversed(db.get_recent_commands(limit=config.general.history_size)))
    finally:
        db.close()

    snapshot = {
        "terminalghost_export": EXPORT_FORMAT,
        "exported_at": time.time(),
        "commands": [
            {
                "ts": e.ts,
                "cwd": e.cwd,
                "cmd": e.cmd,
                "exit_code": e.exit_code,
                "duration_ms": e.duration_ms,
                "output": e.output,
            }
            for e in events
        ],
    }
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        console.print(
            f"[tg.success]✓[/] Exported {len(events)} command(s) to [tg.key]{out}[/]"
        )
    else:
        # Raw JSON on stdout so `terminalghost export > snap.json` just works;
        # the confirmation goes to stderr.
        sys.stdout.write(text + "\n")
        console.print(f"[tg.muted]exported {len(events)} command(s)[/]")
    return 0


def cmd_import(config, file: str) -> int:
    """Load a snapshot produced by `export` into the local history buffer."""
    from terminalghost.ui import console_for

    console = console_for(config)
    try:
        # utf-8-sig: tolerate the BOM that PowerShell/Notepad prepend on Windows.
        with open(file, encoding="utf-8-sig") as fh:
            snapshot = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        console.print(f"[tg.error]Could not read snapshot:[/] {exc}")
        return 1
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("terminalghost_export") != EXPORT_FORMAT
        or not isinstance(snapshot.get("commands"), list)
    ):
        from rich.markup import escape

        # Phrase first, path second: the path's length varies by platform and
        # Rich wraps at the console width — leading with the fixed text keeps
        # the message scannable however long the path is.
        console.print(
            f"[tg.error]Not a terminalghost export snapshot:[/] {escape(file)}"
        )
        return 1

    try:
        db = _open_db(config)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[tg.error]Could not open history:[/] {exc}")
        return 1
    imported = skipped = 0
    try:
        session_id = db.start_session(0, "import")
        for row in snapshot["commands"]:
            event = _row_to_event(row, session_id, config)
            if event is None:
                skipped += 1
                continue
            db.insert_command(event)
            imported += 1
    finally:
        db.close()

    console.print(f"[tg.success]✓[/] Imported {imported} command(s).")
    if skipped:
        console.print(f"[tg.warn]Skipped {skipped} malformed entr(y/ies).[/]")
    if imported > config.general.history_size:
        console.print(
            "[tg.muted]Note: the rolling buffer keeps only the newest "
            f"{config.general.history_size} commands.[/]"
        )
    return 0


def _row_to_event(row, session_id: int, config):
    """Validate one snapshot entry into a CommandEvent (None if malformed)."""
    from terminalghost.daemon.process import _redact_command
    from terminalghost.storage.db import CommandEvent

    if not isinstance(row, dict):
        return None
    cmd = row.get("cmd")
    cwd = row.get("cwd")
    ts = row.get("ts")
    exit_code = row.get("exit_code")
    if (
        not isinstance(cmd, str) or not cmd
        or not isinstance(cwd, str) or not cwd
        or isinstance(ts, bool) or not isinstance(ts, (int, float))
        or isinstance(exit_code, bool) or not isinstance(exit_code, int)
    ):
        return None
    duration = row.get("duration_ms")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        duration = 0
    output = row.get("output")
    if output is not None and not isinstance(output, str):
        output = None
    if config.capture.redact_passwords:
        cmd = _redact_command(cmd)
    return CommandEvent(
        session_id=session_id,
        ts=float(ts),
        cwd=cwd,
        cmd=cmd,
        exit_code=exit_code,
        duration_ms=duration,
        output=output,
    )
