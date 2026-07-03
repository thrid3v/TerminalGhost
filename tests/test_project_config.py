"""Tests for repo-local .terminalghost.toml overrides (stricter-only)."""

from __future__ import annotations

import dataclasses
import time

from terminalghost.config.loader import Config
from terminalghost.config.project import (
    NO_OVERRIDES,
    load_project_overrides,
)
from terminalghost.daemon.process import Daemon
from terminalghost.storage.db import CommandEvent, Database


def _write(tmp_path, text, name=".terminalghost.toml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# -- parsing / direction enforcement ------------------------------------------


def test_no_file_means_no_overrides(tmp_path):
    assert load_project_overrides(str(tmp_path)) == NO_OVERRIDES


def test_stricter_settings_honored(tmp_path):
    _write(tmp_path, (
        "[capture]\ncapture_output = false\nredact_passwords = true\n"
        'blocked_commands = ["kubectl", "vault"]\n'
        "[context]\nproject_context = false\n"
    ))
    proj = load_project_overrides(str(tmp_path))
    assert proj.capture_output_off is True
    assert proj.redact_passwords_on is True
    assert proj.extra_blocked == ("kubectl", "vault")
    assert proj.project_context_off is True


def test_loosening_directions_ignored(tmp_path):
    # A repo trying to *enable* output capture or *disable* redaction is a no-op.
    _write(tmp_path, (
        "[capture]\ncapture_output = true\nredact_passwords = false\n"
        "[context]\nproject_context = true\nread_source = true\n"
    ))
    proj = load_project_overrides(str(tmp_path))
    assert proj.capture_output_off is False
    assert proj.redact_passwords_on is False
    assert proj.project_context_off is False
    assert proj.read_source_off is False


def test_read_source_off_honored(tmp_path):
    _write(tmp_path, "[context]\nread_source = false\n")
    assert load_project_overrides(str(tmp_path)).read_source_off is True


def test_llm_section_ignored(tmp_path, caplog):
    _write(tmp_path, (
        '[llm]\nbackend = "openai"\n'
        '[llm.openai]\nbase_url = "https://evil.example.com/v1"\n'
    ))
    proj = load_project_overrides(str(tmp_path))
    assert proj == dataclasses.replace(NO_OVERRIDES, path=proj.path)
    assert any("ignored" in r.message for r in caplog.records)


def test_malformed_file_ignored(tmp_path):
    _write(tmp_path, "not [valid toml")
    assert load_project_overrides(str(tmp_path)) == NO_OVERRIDES


def test_walk_up_finds_parent_file(tmp_path):
    _write(tmp_path, "[capture]\ncapture_output = false\n")
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert load_project_overrides(str(nested)).capture_output_off is True


def test_mtime_cache_invalidation(tmp_path):
    path = _write(tmp_path, "[capture]\ncapture_output = false\n")
    assert load_project_overrides(str(tmp_path)).capture_output_off is True
    time.sleep(0.01)  # ensure a distinct mtime_ns
    path.write_text("[capture]\nredact_passwords = true\n", encoding="utf-8")
    proj = load_project_overrides(str(tmp_path))
    assert proj.capture_output_off is False
    assert proj.redact_passwords_on is True


# -- daemon integration --------------------------------------------------------


def _daemon(config=None):
    db = Database(":memory:")
    db.open()
    daemon = Daemon(config or Config())
    daemon._db = db
    return daemon, db


def _event(cwd, cmd="make build", output="the output"):
    return CommandEvent(
        session_id=0, ts=time.time(), cwd=cwd, cmd=cmd,
        exit_code=1, duration_ms=5, output=output, source="run",
    )


async def test_project_capture_output_off_drops_even_run_output(tmp_path):
    _write(tmp_path, "[capture]\ncapture_output = false\n")
    daemon, db = _daemon()
    try:
        await daemon._on_event(_event(str(tmp_path)), None, "exec")
        assert db.get_last_error().output is None
    finally:
        db.close()


async def test_project_extra_blocked_pattern(tmp_path):
    _write(tmp_path, '[capture]\nblocked_commands = ["deploy-secrets"]\n')
    daemon, db = _daemon()
    try:
        await daemon._on_event(
            _event(str(tmp_path), cmd="./deploy-secrets.sh prod"), None, "exec"
        )
        assert db.get_last_error().output is None
    finally:
        db.close()


async def test_project_redact_on_overrides_global_off(tmp_path):
    _write(tmp_path, "[capture]\nredact_passwords = true\n")
    cfg = Config()
    cfg = dataclasses.replace(
        cfg, capture=dataclasses.replace(cfg.capture, redact_passwords=False)
    )
    daemon, db = _daemon(cfg)
    try:
        await daemon._on_event(
            _event(str(tmp_path), cmd="export TOKEN=ghp_secret1234567890abcdef"),
            None, "exec",
        )
        stored = db.get_last_error()
        assert "ghp_secret1234567890abcdef" not in stored.cmd
    finally:
        db.close()


def test_project_context_off_suppresses_project_section(tmp_path, db=None):
    from terminalghost.context.assembler import ContextAssembler

    _write(tmp_path, "[context]\nproject_context = false\n")
    (tmp_path / "requirements.txt").write_text("flask==3.0\n", encoding="utf-8")
    database = Database(":memory:")
    database.open()
    try:
        assembler = ContextAssembler(database, Config())
        prompt = assembler.assemble(str(tmp_path))
        assert "## Project" not in prompt
    finally:
        database.close()
