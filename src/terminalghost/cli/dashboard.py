# terminalghost.cli.dashboard
#
# `terminalghost dashboard` — a full-screen Textual view of what TerminalGhost
# has captured: daemon status, recent commands, and the output of the selected
# one, with quick actions (copy / run / ask). The data layer (load_recent,
# daemon_status) is pure so it can be unit-tested without spinning up the UI.

from __future__ import annotations

import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Static


# -- data layer (pure) ------------------------------------------------------


def load_recent(config, limit: int = 200) -> list:
    """Recent CommandEvents (newest first), read-only. Empty on any error."""
    from terminalghost.storage.db import Database

    db = Database(
        config.general.db_path,
        history_size=config.general.history_size,
        max_output_bytes=config.capture.max_output_bytes,
    )
    try:
        db.open()
        return db.get_recent_commands(limit=limit)
    except Exception:  # noqa: BLE001 — a dashboard should never crash on read
        return []
    finally:
        db.close()


def daemon_status(config) -> dict:
    """Snapshot for the header: running/pid, backend·model, host:port, reachable."""
    import socket

    from terminalghost.cli.client import _answering_model
    from terminalghost.daemon.process import _daemon_pid
    from terminalghost.runtime import effective_port

    pid = _daemon_pid(config)
    port = effective_port(config)
    reachable = False
    try:
        with socket.create_connection((config.general.host, port), timeout=0.5):
            reachable = True
    except OSError:
        reachable = False
    return {
        "pid": pid,
        "running": pid is not None,
        "reachable": reachable,
        "host": config.general.host,
        "port": port,
        "backend": config.llm.backend,
        "model": _answering_model(config),
    }


def status_markup(st: dict) -> str:
    dot = "[green]●[/] running" if st["running"] else "[red]●[/] stopped"
    if st["running"]:
        dot += f" [dim](pid {st['pid']})[/]"
    return (
        f"👻 [bold cyan]TerminalGhost[/]   {dot}   "
        f"[magenta]{st['backend']}[/]·[cyan]{st['model']}[/]   "
        f"[dim]{st['host']}:{st['port']}[/]"
    )


def _short_dir(path: str) -> str:
    parts = path.replace("\\", "/").rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 1 else (parts[-1] or path)


# -- Textual app ------------------------------------------------------------


class DashboardApp(App):
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #commands { height: 1fr; }
    #detail { height: 12; border-top: solid $primary; padding: 0 1; overflow-y: auto; }
    DataTable > .datatable--cursor { background: $accent; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "copy", "Copy cmd"),
        Binding("a", "run", "Run cmd"),
        Binding("enter", "ask", "Ask about"),
    ]

    def __init__(self, config) -> None:
        super().__init__()
        self._config = config
        self._events: list = []

    def compose(self) -> ComposeResult:
        yield Static(id="status")
        yield DataTable(id="commands", cursor_type="row", zebra_stripes=True)
        yield Static("Select a command to see its captured output.", id="detail")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#commands", DataTable)
        table.add_columns("time", "exit", "ms", "dir", "command")
        self.refresh_data()
        self.set_interval(3.0, self.refresh_data)

    def refresh_data(self) -> None:
        self._events = load_recent(self._config)
        self.query_one("#status", Static).update(
            status_markup(daemon_status(self._config))
        )
        table = self.query_one("#commands", DataTable)
        prev = table.cursor_row
        table.clear()
        for ev in self._events:
            when = time.strftime("%H:%M:%S", time.localtime(ev.ts))
            exit_cell = (
                "[green]0[/]" if ev.exit_code == 0 else f"[red]{ev.exit_code}[/]"
            )
            table.add_row(when, exit_cell, str(ev.duration_ms),
                          _short_dir(ev.cwd), ev.cmd)
        if self._events:
            table.move_cursor(row=min(prev, len(self._events) - 1))

    def _selected(self):
        row = self.query_one("#commands", DataTable).cursor_row
        if 0 <= row < len(self._events):
            return self._events[row]
        return None

    def on_data_table_row_highlighted(self, _event) -> None:
        ev = self._selected()
        detail = self.query_one("#detail", Static)
        if ev is None:
            return
        body = ev.output or "(no captured output — run it with tgr to capture)"
        detail.update(f"[bold]$ {ev.cmd}[/]\n\n{body}")

    def action_refresh(self) -> None:
        self.refresh_data()

    def action_copy(self) -> None:
        ev = self._selected()
        if ev is not None:
            self.copy_to_clipboard(ev.cmd)
            self.notify("Copied command to clipboard")

    def action_run(self) -> None:
        ev = self._selected()
        if ev is None:
            return
        from terminalghost.cli.client import _cmd_exec

        with self.suspend():
            _cmd_exec(self._config, ev.cmd)

    def action_ask(self) -> None:
        ev = self._selected()
        if ev is None:
            return
        from terminalghost.cli.client import _cmd_ask

        with self.suspend():
            _cmd_ask(self._config, ev.cmd)


def cmd_dashboard(config) -> int:
    DashboardApp(config).run()
    return 0
