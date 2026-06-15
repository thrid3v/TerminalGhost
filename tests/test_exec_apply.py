"""Tests for the exec capture wrapper, run-source gate, and apply-the-fix."""

from __future__ import annotations

import time

from terminalghost.config.loader import Config
from terminalghost.daemon import process
from terminalghost.daemon.process import Daemon
from terminalghost.storage.db import CommandEvent, Database
from terminalghost.trigger.handler import TriggerHandler


# -- command extraction -----------------------------------------------------


def test_extract_command_from_fenced_block():
    answer = "Try this:\n\n```bash\n$ git checkout -b feature\n```\nGood luck."
    assert TriggerHandler._extract_command(answer) == "git checkout -b feature"


def test_extract_command_skips_comment_lines():
    answer = "```\n# create the branch\ngit branch foo\n```"
    assert TriggerHandler._extract_command(answer) == "git branch foo"


def test_extract_command_inline_fallback():
    assert TriggerHandler._extract_command("just run `npm install` first") == "npm install"


def test_extract_command_none():
    assert TriggerHandler._extract_command("no command here, sorry") is None


# -- suggestion storage / TTL ----------------------------------------------


def test_record_and_get_suggestion():
    h = TriggerHandler(None, object(), object(), Config())
    h._record_suggestion("do this:\n```\nmake build\n```")
    assert h.last_suggestion() == "make build"


def test_suggestion_expires():
    h = TriggerHandler(None, object(), object(), Config())
    h._last_suggestion = (time.monotonic() - (h._SUGGESTION_TTL + 1), "stale cmd")
    assert h.last_suggestion() is None


# -- run-source output gate (daemon) ---------------------------------------


def _event(cmd, output, source):
    return CommandEvent(
        session_id=0, ts=time.time(), cwd="/proj", cmd=cmd,
        exit_code=1, duration_ms=5, output=output, source=source,
    )


async def test_run_source_output_kept_when_capture_disabled():
    db = Database(":memory:")
    db.open()
    daemon = Daemon(Config())  # capture_output defaults to False
    daemon._db = db
    try:
        await daemon._on_event(_event("make build", "BOOM the build broke", "run"),
                               None, "exec")
        stored = db.get_last_error(cwd="/proj")
        assert stored.output == "BOOM the build broke"
    finally:
        db.close()


async def test_hook_output_dropped_when_capture_disabled():
    db = Database(":memory:")
    db.open()
    daemon = Daemon(Config())
    daemon._db = db
    try:
        await daemon._on_event(_event("make build", "some output", None), None, "bash")
        stored = db.get_last_error(cwd="/proj")
        assert stored.output is None
    finally:
        db.close()


# -- exec wrapper -----------------------------------------------------------


def test_cmd_exec_captures_output_and_exit(monkeypatch):
    sent = {}

    def fake_send(config, cmd, rc, dur, output):
        sent.update(cmd=cmd, rc=rc, output=output)

    monkeypatch.setattr(process, "_send_run_event", fake_send)
    rc = process._cmd_exec(Config(), ["echo", "hello-capture"])
    assert rc == 0
    assert "hello-capture" in sent["output"]
    assert sent["cmd"] == "echo hello-capture"
