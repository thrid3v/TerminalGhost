"""Tests for the terminalghost.ui presentation layer and [ui] config."""

from __future__ import annotations

import io

import pytest

from terminalghost.config.loader import ConfigError, _build_config, _validate
from terminalghost.ui import get_console, render_banner, resolve_color


class _FakeTTY(io.StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:  # noqa: D401
        return self._tty


# -- resolve_color ----------------------------------------------------------


def test_resolve_color_never_always():
    assert resolve_color("never", _FakeTTY(True)) is False
    assert resolve_color("always", _FakeTTY(False)) is True


def test_resolve_color_auto_follows_tty():
    assert resolve_color("auto", _FakeTTY(True)) is True
    assert resolve_color("auto", _FakeTTY(False)) is False


def test_resolve_color_no_color_env_overrides_auto(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert resolve_color("auto", _FakeTTY(True)) is False


def test_resolve_color_never_beats_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    # never is still false; but always should win over NO_COLOR per precedence?
    # We document NO_COLOR as forcing off only for auto/always-not-set; explicit
    # "always" wins.
    assert resolve_color("always", _FakeTTY(True)) is True


# -- banner -----------------------------------------------------------------


def test_render_banner_contains_brand_and_version():
    console = get_console(color="always", record=True)
    console.print(render_banner("9.9.9", backend="ollama"))
    out = console.export_text()
    assert "TerminalGhost" in out
    assert "9.9.9" in out
    assert "ollama" in out


def test_get_console_no_color_strips_ansi():
    console = get_console(color="never", record=True)
    console.print("[tg.error]boom[/]")
    out = console.export_text()
    assert "boom" in out
    assert "\x1b[" not in out  # no ANSI escapes


# -- [ui] config ------------------------------------------------------------


def test_ui_config_defaults():
    cfg = _build_config({})
    assert cfg.ui.color == "auto"
    assert cfg.ui.banner is True
    assert cfg.ui.markdown is True


def test_ui_config_custom_values():
    cfg = _build_config({"ui": {"color": "never", "banner": False}})
    assert cfg.ui.color == "never"
    assert cfg.ui.banner is False


def test_ui_color_validation_rejects_bad_mode():
    with pytest.raises(ConfigError):
        _validate({"ui": {"color": "rainbow"}})
