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


def test_doctor_reachable_resolves_ephemeral_port(tmp_path):
    # With general.port == 0 the daemon writes its bound port to a runtime
    # file; doctor must probe that port, not the literal 0.
    import socket

    cfg = dataclasses.replace(
        Config(),
        general=dataclasses.replace(
            Config().general,
            port=0,
            pid_file=str(tmp_path / "terminalghost.pid"),
            db_path=str(tmp_path / "history.db"),
        ),
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    bound_port = listener.getsockname()[1]
    (tmp_path / "port").write_text(str(bound_port), encoding="ascii")
    try:
        checks = doctor.gather_checks(cfg)
    finally:
        listener.close()
    reachable = next(c for c in checks if c.label == "Daemon reachable")
    assert reachable.ok is True
    assert str(bound_port) in reachable.detail


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


# -- clear ------------------------------------------------------------------


def _history_config(tmp_path, n_commands: int) -> Config:
    db_path = str(tmp_path / "history.db")
    db = Database(db_path)
    db.open()
    sid = db.start_session(123, "zsh")
    for i in range(n_commands):
        db.insert_command(
            CommandEvent(session_id=sid, ts=time.time(), cwd="/proj",
                         cmd=f"cmd-{i}", exit_code=0, duration_ms=1)
        )
    db.close()
    return dataclasses.replace(
        Config(),
        general=dataclasses.replace(Config().general, db_path=db_path),
        ui=dataclasses.replace(Config().ui, color="never"),
    )


def test_cmd_clear_all_with_yes(tmp_path, capsys):
    cfg = _history_config(tmp_path, 3)
    rc = logview.cmd_clear(cfg, last=None, assume_yes=True)
    assert rc == 0
    assert "Deleted 3" in capsys.readouterr().out
    db = Database(cfg.general.db_path)
    db.open()
    try:
        assert db.get_recent_commands() == []
    finally:
        db.close()


def test_cmd_clear_last_n(tmp_path, capsys):
    cfg = _history_config(tmp_path, 5)
    rc = logview.cmd_clear(cfg, last=2, assume_yes=True)
    assert rc == 0
    assert "Deleted 2" in capsys.readouterr().out
    db = Database(cfg.general.db_path)
    db.open()
    try:
        assert [e.cmd for e in db.get_recent_commands()] == ["cmd-2", "cmd-1", "cmd-0"]
    finally:
        db.close()


def test_cmd_clear_refuses_without_tty(tmp_path, capsys, monkeypatch):
    cfg = _history_config(tmp_path, 2)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    rc = logview.cmd_clear(cfg, last=None, assume_yes=False)
    assert rc == 1
    assert "Refusing" in capsys.readouterr().out
    db = Database(cfg.general.db_path)
    db.open()
    try:
        assert len(db.get_recent_commands()) == 2  # nothing deleted
    finally:
        db.close()


def test_cmd_clear_declined(tmp_path, capsys, monkeypatch):
    cfg = _history_config(tmp_path, 2)
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    rc = logview.cmd_clear(cfg, last=None, assume_yes=False)
    assert rc == 0
    assert "Nothing deleted" in capsys.readouterr().out


def test_cmd_clear_empty(tmp_path, capsys):
    db_path = str(tmp_path / "empty.db")
    cfg = dataclasses.replace(
        Config(),
        general=dataclasses.replace(Config().general, db_path=db_path),
        ui=dataclasses.replace(Config().ui, color="never"),
    )
    rc = logview.cmd_clear(cfg, last=None, assume_yes=True)
    assert rc == 0
    assert "already empty" in capsys.readouterr().out
