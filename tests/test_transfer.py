"""Tests for terminalghost.cli.transfer (export / import snapshots)."""

from __future__ import annotations

import dataclasses
import json
import time

from terminalghost.cli import transfer
from terminalghost.config.loader import Config
from terminalghost.storage.db import CommandEvent, Database


def _config(tmp_path, name="history.db") -> Config:
    return dataclasses.replace(
        Config(),
        general=dataclasses.replace(Config().general, db_path=str(tmp_path / name)),
        ui=dataclasses.replace(Config().ui, color="never"),
    )


def _seed(cfg: Config, commands: list[str]) -> None:
    db = Database(cfg.general.db_path)
    db.open()
    sid = db.start_session(1, "test")
    for i, cmd in enumerate(commands):
        db.insert_command(CommandEvent(
            session_id=sid, ts=time.time() - len(commands) + i, cwd="/proj",
            cmd=cmd, exit_code=0, duration_ms=1,
        ))
    db.close()


def _stored_cmds(cfg: Config) -> list[str]:
    db = Database(cfg.general.db_path)
    db.open()
    try:
        return [e.cmd for e in reversed(db.get_recent_commands(limit=200))]
    finally:
        db.close()


def test_export_import_roundtrip(tmp_path, capsys):
    src = _config(tmp_path, "src.db")
    _seed(src, ["make build", "git status", "pytest -q"])
    snap = tmp_path / "snap.json"

    assert transfer.cmd_export(src, str(snap)) == 0
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["terminalghost_export"] == transfer.EXPORT_FORMAT
    assert [c["cmd"] for c in data["commands"]] == ["make build", "git status", "pytest -q"]

    dst = _config(tmp_path, "dst.db")
    assert transfer.cmd_import(dst, str(snap)) == 0
    assert "Imported 3" in capsys.readouterr().out
    assert _stored_cmds(dst) == ["make build", "git status", "pytest -q"]


def test_export_to_stdout(tmp_path, capsys):
    cfg = _config(tmp_path)
    _seed(cfg, ["ls -la"])
    assert transfer.cmd_export(cfg, None) == 0
    out = capsys.readouterr().out
    data = json.loads(out)  # stdout is pure JSON, pipeable
    assert data["commands"][0]["cmd"] == "ls -la"


def test_import_rejects_non_snapshot(tmp_path, capsys):
    cfg = _config(tmp_path)
    bogus = tmp_path / "bogus.json"
    bogus.write_text(json.dumps({"something": "else"}), encoding="utf-8")
    assert transfer.cmd_import(cfg, str(bogus)) == 1
    # Rich wraps at the console width and tmp-path lengths vary per platform,
    # so normalize newlines before matching (this bit ubuntu CI).
    out = capsys.readouterr().out.replace("\n", " ")
    assert "Not a terminalghost export snapshot" in out


def test_import_accepts_utf8_bom(tmp_path, capsys):
    # PowerShell's Set-Content / Notepad prepend a BOM on Windows.
    cfg = _config(tmp_path)
    snap = tmp_path / "snap.json"
    body = json.dumps({
        "terminalghost_export": 1,
        "commands": [{"cmd": "ls", "cwd": "/p", "ts": time.time(),
                      "exit_code": 0, "duration_ms": 1}],
    })
    snap.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert transfer.cmd_import(cfg, str(snap)) == 0
    assert "Imported 1" in capsys.readouterr().out


def test_import_rejects_missing_file(tmp_path, capsys):
    cfg = _config(tmp_path)
    assert transfer.cmd_import(cfg, str(tmp_path / "nope.json")) == 1
    assert "Could not read snapshot" in capsys.readouterr().out


def test_import_skips_malformed_entries(tmp_path, capsys):
    cfg = _config(tmp_path)
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({
        "terminalghost_export": 1,
        "commands": [
            {"cmd": "good one", "cwd": "/p", "ts": time.time(), "exit_code": 0,
             "duration_ms": 1},
            {"cmd": "", "cwd": "/p", "ts": time.time(), "exit_code": 0},  # empty cmd
            "not-a-dict",
        ],
    }), encoding="utf-8")
    assert transfer.cmd_import(cfg, str(snap)) == 0
    out = capsys.readouterr().out
    assert "Imported 1" in out and "Skipped 2" in out
    assert _stored_cmds(cfg) == ["good one"]


def test_import_redacts_secrets(tmp_path, capsys):
    cfg = _config(tmp_path)
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({
        "terminalghost_export": 1,
        "commands": [
            {"cmd": "export API_KEY=sk-livekey123456789", "cwd": "/p",
             "ts": time.time(), "exit_code": 0, "duration_ms": 1},
        ],
    }), encoding="utf-8")
    assert transfer.cmd_import(cfg, str(snap)) == 0
    stored = _stored_cmds(cfg)[0]
    assert "sk-livekey123456789" not in stored
    assert "<redacted>" in stored
