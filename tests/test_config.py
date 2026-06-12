# tests.test_config — tests for terminalghost.config.loader

import dataclasses

import pytest

from terminalghost.config.loader import (
    Config,
    ConfigError,
    _overlay_env_vars,
    _validate,
    load_config,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip any ambient TG_* overrides so tests are deterministic."""
    import os

    for name in list(os.environ):
        if name.startswith("TG_"):
            monkeypatch.delenv(name)


def test_defaults_when_no_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "terminalghost.config.loader.DEFAULT_CONFIG_PATH",
        str(tmp_path / "missing.toml"),
    )
    config = load_config()
    assert config.general.history_size == 200
    assert config.llm.backend == "ollama"
    assert config.llm.ollama.model == "llama3"
    assert config.capture.redact_passwords is True


def test_valid_toml_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[general]
history_size = 42

[llm]
backend = "claude"

[llm.claude]
model = "claude-sonnet-4-6"
""",
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert config.general.history_size == 42
    assert config.llm.backend == "claude"
    assert config.llm.claude.model == "claude-sonnet-4-6"
    # untouched sections keep defaults
    assert config.context.tree_depth == 3


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[general]
history_size = 10
future_flag = "whatever"

[new_section]
x = 1
""",
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert config.general.history_size == 10


def test_malformed_toml_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[general\nhistory_size = ", encoding="utf-8")
    with pytest.raises(ConfigError, match="malformed TOML"):
        load_config(str(path))


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(str(tmp_path / "nope.toml"))


def test_env_overlay_string(monkeypatch):
    monkeypatch.setenv("TG_LLM__BACKEND", "claude")
    raw = _overlay_env_vars({})
    assert raw["llm"]["backend"] == "claude"


def test_env_overlay_int(monkeypatch):
    monkeypatch.setenv("TG_GENERAL__HISTORY_SIZE", "500")
    raw = _overlay_env_vars({})
    assert raw["general"]["history_size"] == 500


def test_env_overlay_nested(monkeypatch):
    monkeypatch.setenv("TG_LLM__OLLAMA__MODEL", "mistral")
    raw = _overlay_env_vars({})
    assert raw["llm"]["ollama"]["model"] == "mistral"


def test_env_overlay_bool(monkeypatch):
    monkeypatch.setenv("TG_CAPTURE__REDACT_PASSWORDS", "false")
    raw = _overlay_env_vars({})
    assert raw["capture"]["redact_passwords"] is False


def test_env_type_mismatch_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "terminalghost.config.loader.DEFAULT_CONFIG_PATH",
        str(tmp_path / "missing.toml"),
    )
    monkeypatch.setenv("TG_GENERAL__HISTORY_SIZE", "abc")
    with pytest.raises(ConfigError, match="history_size"):
        load_config()


def test_non_tg_env_ignored(monkeypatch):
    monkeypatch.setenv("OTHER_LLM__BACKEND", "claude")
    raw = _overlay_env_vars({})
    assert raw == {}


@pytest.mark.parametrize(
    "raw",
    [
        {"llm": {"backend": "unknown"}},
        {"general": {"history_size": 0}},
        {"general": {"history_size": 10001}},
        {"context": {"tree_depth": 15}},
        {"context": {"token_budget": 50}},
        {"general": {"port": 80}},
    ],
)
def test_validate_rejects(raw):
    with pytest.raises(ConfigError):
        _validate(raw)


def test_validate_accepts_valid():
    _validate(
        {
            "llm": {"backend": "claude"},
            "general": {"history_size": 200, "port": 48632},
            "context": {"tree_depth": 3, "token_budget": 3000},
        }
    )


def test_tilde_expansion(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[general]
db_path = "~/tg/history.db"
""",
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert "~" not in config.general.db_path
    assert config.general.db_path.endswith("history.db")


def test_config_is_frozen():
    config = Config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.general = None  # type: ignore[misc]
