"""Tests for the exec capture wrapper, run-source gate, and apply-the-fix."""

from __future__ import annotations

import time

from terminalghost.cli import client
from terminalghost.config.loader import Config
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


def test_extract_command_dollar_prompt_line():
    answer = "Try:\n\n$ git checkout main\n\nThat switches branches."
    assert TriggerHandler._extract_command(answer) == "git checkout main"


def test_extract_command_preserves_dollar_prefixed_command():
    # A leading "$" that is part of the command (PowerShell) must survive;
    # only a "$ " prompt marker is stripped.
    answer = '```powershell\n$env:PATH = "C:\\tools;$env:PATH"\n```'
    assert (
        TriggerHandler._extract_command(answer)
        == '$env:PATH = "C:\\tools;$env:PATH"'
    )


def test_extract_command_inline_fallback():
    assert TriggerHandler._extract_command("just run `npm install` first") == "npm install"


def test_extract_command_none():
    assert TriggerHandler._extract_command("no command here, sorry") is None


# -- multi-step plans ---------------------------------------------------------


def test_extract_commands_all_fence_lines():
    answer = (
        "Do this:\n\n```bash\n"
        "# stop the service first\n"
        "sudo systemctl stop app\n"
        "$ pip install -U app\n"
        "sudo systemctl start app\n"
        "```"
    )
    assert TriggerHandler._extract_commands(answer) == [
        "sudo systemctl stop app",
        "pip install -U app",
        "sudo systemctl start app",
    ]


def test_extract_commands_caps_steps():
    body = "\n".join(f"echo step-{i}" for i in range(20))
    answer = f"```\n{body}\n```"
    assert len(TriggerHandler._extract_commands(answer)) == TriggerHandler._MAX_STEPS


def test_extract_commands_prose_dollar_lines():
    answer = "First:\n\n$ git fetch origin\n\nthen\n\n$ git rebase origin/main\n"
    assert TriggerHandler._extract_commands(answer) == [
        "git fetch origin",
        "git rebase origin/main",
    ]


def test_last_suggestions_returns_plan():
    h = TriggerHandler(None, object(), object(), Config())
    h._record_suggestion("```\nmake build\nmake test\n```")
    assert h.last_suggestions() == ["make build", "make test"]
    assert h.last_suggestion() == "make build"  # back-compat: first step


# -- step runner (client) -----------------------------------------------------


def _step_client_env(monkeypatch, inputs, exit_codes=None):
    """Monkeypatch input + _cmd_exec; returns the list of executed commands."""
    executed = []
    answers = iter(inputs)
    codes = iter(exit_codes or [])
    monkeypatch.setattr("builtins.input", lambda p="": next(answers))
    monkeypatch.setattr(
        client, "_cmd_exec",
        lambda c, cmd: (executed.append(cmd), next(codes, 0))[1],
    )
    return executed


def _no_color():
    import dataclasses

    return dataclasses.replace(
        Config(), ui=dataclasses.replace(Config().ui, color="never")
    )


def test_run_steps_runs_all_on_default(monkeypatch):
    executed = _step_client_env(monkeypatch, ["", ""])  # Enter = yes
    rc = client._run_steps(_no_color(), ["make build", "make test"])
    assert rc == 0
    assert executed == ["make build", "make test"]


def test_run_steps_skip_and_quit(monkeypatch):
    executed = _step_client_env(monkeypatch, ["s", "q"])
    rc = client._run_steps(_no_color(), ["one", "two", "three"])
    assert rc == 0
    assert executed == []  # skipped the first, quit on the second


def test_run_steps_stops_on_failure(monkeypatch):
    executed = _step_client_env(monkeypatch, ["", "n"], exit_codes=[7])
    rc = client._run_steps(_no_color(), ["breaks", "never runs"])
    assert rc == 7
    assert executed == ["breaks"]


def test_run_steps_failure_continue(monkeypatch):
    executed = _step_client_env(monkeypatch, ["", "y", ""], exit_codes=[7, 0])
    rc = client._run_steps(_no_color(), ["breaks", "still runs"])
    assert rc == 0
    assert executed == ["breaks", "still runs"]


def test_run_steps_risky_needs_typed_yes(monkeypatch):
    executed = _step_client_env(monkeypatch, ["y", ""])  # "y" not enough for risky
    rc = client._run_steps(_no_color(), ["rm -rf build", "make build"])
    assert executed == ["make build"]  # risky step skipped
    assert rc == 0


def test_run_steps_assume_yes_stops_on_failure(monkeypatch):
    executed = _step_client_env(monkeypatch, [], exit_codes=[3])
    rc = client._run_steps(_no_color(), ["breaks", "never"], assume_yes=True)
    assert rc == 3
    assert executed == ["breaks"]


def test_apply_multi_step_plan(monkeypatch):
    monkeypatch.setattr(client, "_fetch_suggestion", lambda c: "git fetch\ngit rebase")
    monkeypatch.setattr(client.sys.stdin, "isatty", lambda: True, raising=False)
    executed = _step_client_env(monkeypatch, ["", ""])
    rc = client._cmd_apply(_no_color())
    assert rc == 0
    assert executed == ["git fetch", "git rebase"]


def test_post_answer_actions_multi_runs_steps(monkeypatch):
    monkeypatch.setattr(client, "_fetch_suggestion", lambda c: "one\ntwo")
    monkeypatch.setattr(client, "_read_key", lambda: "r")
    executed = _step_client_env(monkeypatch, ["", ""])
    client._post_answer_actions(_no_color())
    assert executed == ["one", "two"]


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

    monkeypatch.setattr(client, "_send_run_event", fake_send)
    rc = client._cmd_exec(Config(), "echo hello-capture")
    assert rc == 0
    assert "hello-capture" in sent["output"]
    assert sent["cmd"] == "echo hello-capture"


def test_build_cmdline_preserves_quoted_args():
    line = client._build_cmdline(["pytest", "-k", "foo bar"])
    # the arg with a space stays one quoted token, not split into foo / bar
    assert "foo bar" in line
    assert line.startswith("pytest -k ")
