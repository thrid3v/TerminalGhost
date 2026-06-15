"""Tests for secret redaction, output trimming, and cwd-scoped error lookup."""

from __future__ import annotations

import time

import pytest

from terminalghost.daemon.process import (
    _assert_safe_host,
    _is_loopback,
    _redact_command,
    _redact_output,
    _trim_output,
)
from terminalghost.storage.db import CommandEvent, Database


# -- command redaction ------------------------------------------------------


def test_redact_env_assignment():
    assert _redact_command("export GITHUB_TOKEN=ghp_abc123") == (
        "export GITHUB_TOKEN=<redacted>"
    )
    assert _redact_command("API_KEY=sk-xyz npm run") == "API_KEY=<redacted> npm run"
    assert _redact_command("DB_PASSWORD=hunter2") == "DB_PASSWORD=<redacted>"


def test_redact_flag_forms():
    assert _redact_command("mysql --password=root") == "mysql --password=<redacted>"
    assert _redact_command("curl --token abc123 url") == "curl --token <redacted> url"


def test_redact_url_credentials():
    assert _redact_command("psql postgres://user:secretpw@host/db") == (
        "psql postgres://user:<redacted>@host/db"
    )


def test_redact_leaves_normal_commands_untouched():
    for cmd in ("git status", "ls -la", "cp -p a b", "make build", "cd ../foo"):
        assert _redact_command(cmd) == cmd


def test_redact_bearer_and_token_literals():
    assert "<redacted>" in _redact_command('curl -H "Authorization: Bearer ghp_abcdEFGH1234567890xyz"')
    assert "ghp_" not in _redact_command("git remote set-url o https://ghp_abcdEFGH1234567890xyz@x")
    out = _redact_output("key=value\nAKIAIOSFODNN7EXAMPLE here\nsk-ant-abc123DEF456ghi789")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "sk-ant-abc123DEF456ghi789" not in out


# -- loopback enforcement ---------------------------------------------------


def test_is_loopback():
    assert _is_loopback("127.0.0.1") is True
    assert _is_loopback("::1") is True
    assert _is_loopback("localhost") is True
    assert _is_loopback("0.0.0.0") is False
    assert _is_loopback("192.168.1.10") is False


def test_assert_safe_host_allows_loopback():
    _assert_safe_host("127.0.0.1")  # no raise


def test_assert_safe_host_refuses_remote(monkeypatch):
    monkeypatch.delenv("TG_ALLOW_REMOTE", raising=False)
    with pytest.raises(RuntimeError):
        _assert_safe_host("0.0.0.0")


def test_assert_safe_host_remote_opt_in(monkeypatch):
    monkeypatch.setenv("TG_ALLOW_REMOTE", "1")
    _assert_safe_host("0.0.0.0")  # no raise with explicit override


# -- output trim + redact ---------------------------------------------------


def test_trim_output_keeps_last_lines():
    text = "\n".join(str(i) for i in range(100))
    trimmed = _trim_output(text, 10)
    assert trimmed.splitlines() == [str(i) for i in range(90, 100)]


def test_trim_output_noop_when_short():
    assert _trim_output("a\nb", 10) == "a\nb"


def test_redact_output_masks_secret_lines():
    out = "Using password:\nhunter2\nok"
    redacted = _redact_output(out)
    assert "hunter2" not in redacted
    assert "<redacted>" in redacted


# -- cwd-scoped get_last_error ----------------------------------------------


def _ev(cmd, cwd, exit_code):
    return CommandEvent(
        session_id=1, ts=time.time(), cwd=cwd, cmd=cmd,
        exit_code=exit_code, duration_ms=1,
    )


def test_get_last_error_scoped_by_cwd():
    db = Database(":memory:")
    db.open()
    db.start_session(1, "test")
    db.insert_command(_ev("make a", "/proj-a", 1))
    db.insert_command(_ev("make b", "/proj-b", 2))
    try:
        assert db.get_last_error(cwd="/proj-a").cmd == "make a"
        assert db.get_last_error(cwd="/proj-b").cmd == "make b"
        assert db.get_last_error(cwd="/nowhere") is None
        # No cwd → global most-recent failure (back-compat).
        assert db.get_last_error().cmd == "make b"
    finally:
        db.close()
