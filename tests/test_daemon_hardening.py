"""Tests for daemon hardening: runtime port file, log rotation, avail cache."""

from __future__ import annotations

import dataclasses

from terminalghost.config.loader import Config
from terminalghost.daemon import process
from terminalghost.llm.backends.ollama import OllamaBackend


def _config_with_pid(tmp_path, port):
    return dataclasses.replace(
        Config(),
        general=dataclasses.replace(
            Config().general, pid_file=str(tmp_path / "tg.pid"), port=port
        ),
    )


def test_effective_port_uses_configured_when_nonzero(tmp_path):
    cfg = _config_with_pid(tmp_path, 49000)
    assert process._effective_port(cfg) == 49000


def test_port_file_roundtrip_for_ephemeral(tmp_path):
    cfg = _config_with_pid(tmp_path, 0)
    # No file yet → falls back to configured (0).
    assert process._effective_port(cfg) == 0
    process._write_port_file(cfg, 51234)
    assert process._effective_port(cfg) == 51234
    process._remove_port_file(cfg)
    assert process._effective_port(cfg) == 0


def test_rotate_log_moves_large_file(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_bytes(b"x" * 100)
    process._rotate_log(str(log), max_bytes=50)
    assert (tmp_path / "daemon.log.1").exists()
    assert not log.exists()  # renamed away; reopened fresh by caller


def test_rotate_log_noop_when_small(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_bytes(b"x" * 10)
    process._rotate_log(str(log), max_bytes=1000)
    assert log.exists()
    assert not (tmp_path / "daemon.log.1").exists()


class _FakeResp:
    status_code = 200

    def json(self):
        return {"models": [{"name": "llama3:latest"}]}


def test_is_available_is_cached(monkeypatch):
    calls = {"n": 0}

    def fake_get(*_a, **_k):
        calls["n"] += 1
        return _FakeResp()

    monkeypatch.setattr("terminalghost.llm.backends.ollama.httpx.get", fake_get)
    backend = OllamaBackend(model="llama3")
    assert backend.is_available() is True
    assert backend.is_available() is True
    assert calls["n"] == 1  # second call served from cache
