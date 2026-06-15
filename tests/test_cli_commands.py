"""Tests for the cli command helpers: init/doctor/autostart/logview."""

from __future__ import annotations

import dataclasses
import time

from terminalghost.cli import autostart, doctor, init, logview
from terminalghost.config.loader import Config
from terminalghost.storage.db import CommandEvent, Database


# -- init -------------------------------------------------------------------


def test_render_config_reflects_choices():
    text = init.render_config(backend="ollama", ollama_model="mistral", color="never")
    assert 'backend = "ollama"' in text
    assert 'model = "mistral"' in text
    assert 'color = "never"' in text


def test_render_config_claude():
    text = init.render_config(backend="claude", ollama_model="llama3")
    assert 'backend = "claude"' in text


# -- autostart text builders ------------------------------------------------


def test_systemd_unit_text():
    text = autostart.systemd_unit_text()
    assert "[Service]" in text
    assert "ExecStart=" in text
    assert "terminalghost.daemon.process" in text
    assert "run" in text


def test_launchd_plist_text():
    text = autostart.launchd_plist_text()
    assert autostart.LAUNCHD_LABEL in text
    assert "<key>RunAtLoad</key>" in text
    assert "terminalghost.daemon.process" in text


def test_run_argv_uses_run_subcommand():
    assert autostart._run_argv()[-1] == "run"


# -- doctor -----------------------------------------------------------------


def test_doctor_dir_writable(tmp_path):
    assert doctor._dir_writable(str(tmp_path / "sub")) is True


def test_doctor_port_closed():
    # An unbound high port should be closed.
    assert doctor._port_open("127.0.0.1", 1) is False


def test_gather_checks_returns_labeled_checks():
    checks = doctor.gather_checks(Config())
    labels = [c.label for c in checks]
    assert any("Daemon running" in label for label in labels)
    assert any("Data directory" in label for label in labels)
    assert all(isinstance(c, doctor.Check) for c in checks)


# -- logview ----------------------------------------------------------------


def test_short_dir():
    assert logview._short_dir("/home/u/projects/app") == "projects/app"
    assert logview._short_dir("C:\\Users\\me\\proj") == "me/proj"
    assert logview._short_dir("/") == "/"


def test_cmd_log_renders_recent_commands(tmp_path, capsys):
    db_path = str(tmp_path / "history.db")
    db = Database(db_path)
    db.open()
    sid = db.start_session(123, "zsh")  # commands FK-reference a session row
    db.insert_command(
        CommandEvent(session_id=sid, ts=time.time(), cwd="/proj", cmd="make build",
                     exit_code=1, duration_ms=42)
    )
    db.close()

    cfg = dataclasses.replace(
        Config(),
        general=dataclasses.replace(Config().general, db_path=db_path),
        ui=dataclasses.replace(Config().ui, color="never"),
    )
    rc = logview.cmd_log(cfg, 10)
    out = capsys.readouterr().out
    assert rc == 0
    assert "make build" in out


def test_cmd_log_empty(tmp_path, capsys):
    db_path = str(tmp_path / "empty.db")
    cfg = dataclasses.replace(
        Config(),
        general=dataclasses.replace(Config().general, db_path=db_path),
        ui=dataclasses.replace(Config().ui, color="never"),
    )
    rc = logview.cmd_log(cfg, 10)
    assert rc == 0
    assert "No commands captured" in capsys.readouterr().out
