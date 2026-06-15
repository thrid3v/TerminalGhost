# terminalghost._assets
#
# Bundled, non-Python resources that ship inside the wheel: the shell hook
# scripts and the example config. After a `pipx install terminalghost` there
# is no source checkout on disk, so these are read through importlib.resources
# rather than by repo-relative paths.

from __future__ import annotations

from importlib.resources import files

# shell name (as the user / hooks identify it) -> bundled script filename
HOOK_FILES = {
    "zsh": "zsh_hooks.sh",
    "bash": "bash_hooks.sh",
    "powershell": "powershell_hooks.ps1",
    "pwsh": "powershell_hooks.ps1",
}

CONFIG_TEMPLATE = "config.example.toml"


def hook_path(shell: str) -> str:
    """Absolute path to the bundled hook script for `shell`.

    pip/pipx install the wheel as real files, so the resource path is a stable
    location on disk that a shell profile can `source`. Raises KeyError for an
    unknown shell.
    """
    filename = HOOK_FILES[shell]
    return str(files(__package__) / filename)


def config_template() -> str:
    """Return the bundled example config as text."""
    return (files(__package__) / CONFIG_TEMPLATE).read_text(encoding="utf-8")


def supported_shells() -> list[str]:
    """Shell identifiers that have a bundled hook script."""
    return sorted(HOOK_FILES)
