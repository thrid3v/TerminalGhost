"""Tests for the theme registry, [ui] theme config, and the settings updater."""

from __future__ import annotations

import pytest

from terminalghost.cli import settings
from terminalghost.config.loader import ConfigError, _build_config, _validate
from terminalghost.ui import get_console
from terminalghost.ui.theme import THEME_NAMES, get_theme


def test_theme_names():
    assert set(THEME_NAMES) == {"dark", "light", "high-contrast"}


def test_get_theme_falls_back():
    assert get_theme("nonsense") is get_theme("dark")


def test_each_theme_defines_all_styles():
    keys = set(get_theme("dark").styles)
    for name in THEME_NAMES:
        assert set(get_theme(name).styles) == keys


def test_console_uses_selected_theme():
    # high-contrast renders tg.ok as bright_cyan; just confirm it renders + no crash.
    console = get_console(color="always", theme="high-contrast", record=True)
    console.print("[tg.ok]ok[/]")
    assert "ok" in console.export_text()


def test_ui_theme_config_default_and_custom():
    assert _build_config({}).ui.theme == "dark"
    assert _build_config({"ui": {"theme": "light"}}).ui.theme == "light"


def test_ui_theme_validation():
    with pytest.raises(ConfigError):
        _validate({"ui": {"theme": "neon"}})


# -- settings.update_toml_text ----------------------------------------------


def test_update_existing_key():
    text = '[ui]\ncolor = "auto"\ntheme = "dark"\n'
    out = settings.update_toml_text(text, "ui", "theme", "light")
    assert 'theme = "light"' in out
    assert 'theme = "dark"' not in out
    assert 'color = "auto"' in out  # untouched


def test_update_inserts_missing_key_in_section():
    text = '[ui]\ncolor = "auto"\n\n[llm]\nbackend = "ollama"\n'
    out = settings.update_toml_text(text, "ui", "theme", "light")
    assert 'theme = "light"' in out
    assert out.index("theme") < out.index("[llm]")  # inserted within [ui]


def test_update_adds_missing_section():
    out = settings.update_toml_text("# just a comment\n", "ui", "theme", "light")
    assert "[ui]" in out
    assert 'theme = "light"' in out


def test_set_value_writes_file(tmp_path):
    path = tmp_path / "config.toml"
    written = settings.set_value(str(path), "llm", "backend", "claude")
    assert written == str(path)
    assert 'backend = "claude"' in path.read_text()


# -- use (model hot-switch) -------------------------------------------------


def test_cmd_use_writes_backend(tmp_path, monkeypatch):
    from terminalghost.cli import client
    from terminalghost.config.loader import Config

    monkeypatch.setattr(client, "_request", lambda *a, **k: "ok")
    cfg_path = tmp_path / "c.toml"
    rc = settings.cmd_use(Config(), str(cfg_path), "claude")
    assert rc == 0
    assert 'backend = "claude"' in cfg_path.read_text()


def test_cmd_use_with_model(tmp_path, monkeypatch):
    from terminalghost.cli import client
    from terminalghost.config.loader import Config

    monkeypatch.setattr(client, "_request", lambda *a, **k: "ok")
    cfg_path = tmp_path / "c.toml"
    settings.cmd_use(Config(), str(cfg_path), "ollama:mistral")
    text = cfg_path.read_text()
    assert 'backend = "ollama"' in text
    assert 'model = "mistral"' in text


def test_cmd_use_rejects_unknown_backend(tmp_path):
    from terminalghost.config.loader import Config

    rc = settings.cmd_use(Config(), str(tmp_path / "c.toml"), "gemini")
    assert rc == 1
