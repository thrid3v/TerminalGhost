"""Tests for the dashboard data layer and a Textual app smoke test."""

from __future__ import annotations

import dataclasses
import time

from terminalghost.cli import dashboard
from terminalghost.config.loader import Config
from terminalghost.storage.db import CommandEvent, Database


def _cfg(db_path: str) -> Config:
    base = Config()
    return dataclasses.replace(
        base, general=dataclasses.replace(base.general, db_path=db_path)
    )


def _seed(db_path: str, cmd: str = "make build", exit_code: int = 2) -> None:
    db = Database(db_path)
    db.open()
    sid = db.start_session(1, "zsh")
    db.insert_command(
        CommandEvent(session_id=sid, ts=time.time(), cwd="/home/u/proj",
                     cmd=cmd, exit_code=exit_code, duration_ms=9, output="boom")
    )
    db.close()


def test_load_recent_empty(tmp_path):
    assert dashboard.load_recent(_cfg(str(tmp_path / "none.db"))) == []


def test_load_recent_returns_events(tmp_path):
    p = str(tmp_path / "h.db")
    _seed(p, cmd="make build")
    events = dashboard.load_recent(_cfg(p))
    assert len(events) == 1
    assert events[0].cmd == "make build"


def test_daemon_status_shape():
    st = dashboard.daemon_status(Config())
    assert {"running", "reachable", "host", "port", "backend", "model"} <= set(st)


def test_status_markup():
    st = {"running": True, "pid": 1234, "reachable": True, "host": "127.0.0.1",
          "port": 48632, "backend": "ollama", "model": "llama3"}
    out = dashboard.status_markup(st)
    assert "running" in out and "ollama" in out and "llama3" in out


def test_short_dir():
    assert dashboard._short_dir("/a/b/c") == "b/c"


async def test_app_smoke(tmp_path):
    p = str(tmp_path / "h.db")
    _seed(p, cmd="pytest -k foo")
    app = dashboard.DashboardApp(_cfg(p))
    async with app.run_test() as pilot:
        table = app.query_one("#commands")
        assert table.row_count == 1
        await pilot.press("q")
