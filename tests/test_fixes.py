"""Tests for the saved-fixes library CLI (save / fixes / apply <name>)."""

from __future__ import annotations

import dataclasses

from terminalghost.cli import client, fixes
from terminalghost.config.loader import Config
from terminalghost.storage.db import Database


def _cfg(tmp_path) -> Config:
    return dataclasses.replace(
        Config(),
        general=dataclasses.replace(
            Config().general, db_path=str(tmp_path / "history.db")
        ),
        ui=dataclasses.replace(Config().ui, color="never"),
    )


def _saved(cfg, name):
    db = Database(cfg.general.db_path)
    db.open()
    try:
        return db.get_fix(name)
    finally:
        db.close()


def test_save_pins_last_suggestion(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(
        client, "_suggested_commands", lambda c: ["npx jest --clearCache", "npm test"]
    )
    rc = fixes.cmd_save(cfg, "jest-cache", note="cache corruption")
    out = capsys.readouterr().out
    assert rc == 0
    assert "Saved jest-cache" in out and "2 steps" in out
    fix = _saved(cfg, "jest-cache")
    assert fix.commands == ["npx jest --clearCache", "npm test"]
    assert fix.note == "cache corruption"


def test_save_without_suggestion(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(client, "_suggested_commands", lambda c: [])
    rc = fixes.cmd_save(cfg, "nothing")
    assert rc == 1
    assert "Nothing to save" in capsys.readouterr().out


def test_fixes_lists_and_greps(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(client, "_suggested_commands", lambda c: ["docker compose up"])
    fixes.cmd_save(cfg, "compose-up")
    monkeypatch.setattr(client, "_suggested_commands", lambda c: ["make build"])
    fixes.cmd_save(cfg, "rebuild")
    capsys.readouterr()

    assert fixes.cmd_fixes(cfg) == 0
    out = capsys.readouterr().out
    assert "compose-up" in out and "rebuild" in out

    assert fixes.cmd_fixes(cfg, grep="docker") == 0
    out = capsys.readouterr().out
    assert "compose-up" in out and "rebuild" not in out


def test_fixes_delete(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(client, "_suggested_commands", lambda c: ["true"])
    fixes.cmd_save(cfg, "temp")
    assert fixes.cmd_fixes(cfg, delete="temp") == 0
    assert _saved(cfg, "temp") is None
    assert fixes.cmd_fixes(cfg, delete="temp") == 1  # already gone


def test_fixes_empty_list(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    assert fixes.cmd_fixes(cfg) == 0
    assert "No saved fixes" in capsys.readouterr().out


def test_apply_saved_fix_by_name(tmp_path, capsys, monkeypatch):
    cfg = _cfg(tmp_path)
    db = Database(cfg.general.db_path)
    db.open()
    db.save_fix("greet", ["echo hello"])
    db.close()

    executed = []
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: True, raising=False)
    monkeypatch.setattr("builtins.input", lambda p="": "y")
    monkeypatch.setattr(client, "_cmd_exec", lambda c, cmd: executed.append(cmd) or 0)
    rc = client._cmd_apply(cfg, name="greet")
    assert rc == 0
    assert executed == ["echo hello"]
    assert _saved(cfg, "greet").last_used_ts is not None  # marked used


def test_apply_unknown_fix(tmp_path, capsys):
    cfg = _cfg(tmp_path)
    rc = client._cmd_apply(cfg, name="ghost-fix")
    assert rc == 1
    assert "No saved fix" in capsys.readouterr().out


def test_apply_saved_multi_step(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    db = Database(cfg.general.db_path)
    db.open()
    db.save_fix("two", ["step one", "step two"])
    db.close()

    executed = []
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: True, raising=False)
    answers = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda p="": next(answers))
    monkeypatch.setattr(client, "_cmd_exec", lambda c, cmd: executed.append(cmd) or 0)
    rc = client._cmd_apply(cfg, name="two")
    assert rc == 0
    assert executed == ["step one", "step two"]
