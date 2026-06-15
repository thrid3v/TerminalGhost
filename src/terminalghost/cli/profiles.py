# terminalghost.cli.profiles
#
# Shell detection and idempotent management of the TerminalGhost block inside
# a user's shell profile. The block sources the bundled hook script via the
# `terminalghost hook-path` command, so it keeps working wherever pipx put the
# package:
#
#   # >>> terminalghost >>>
#   source "$(terminalghost hook-path zsh)"
#   # <<< terminalghost <<<

from __future__ import annotations

import os
import re
from pathlib import Path

import psutil

from terminalghost._assets import HOOK_FILES

BLOCK_START = "# >>> terminalghost >>>"
BLOCK_END = "# <<< terminalghost <<<"

_BLOCK_RE = re.compile(
    r"\n*" + re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?",
    re.DOTALL,
)

SUPPORTED_SHELLS = ("zsh", "bash", "powershell")


def detect_shell() -> str | None:
    """Best-effort: which shell launched this process.

    Uses the parent process name (robust — the parent of `terminalghost init`
    is the interactive shell), falling back to $SHELL on POSIX.
    """
    try:
        name = psutil.Process(os.getppid()).name()
    except Exception:  # noqa: BLE001 — detection is best-effort
        name = ""
    return _classify(name) or _classify(os.path.basename(os.environ.get("SHELL", "")))


def _classify(name: str) -> str | None:
    name = (name or "").lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if "zsh" in name:
        return "zsh"
    if "bash" in name:
        return "bash"
    if "pwsh" in name or "powershell" in name:
        return "powershell"
    return None


def profile_path(shell: str) -> Path:
    """Conventional profile file for `shell`.

    For PowerShell, prefer PowerShell 7 (`Documents/PowerShell`) when `pwsh` is
    installed, otherwise Windows PowerShell 5.1 (`Documents/WindowsPowerShell`).
    """
    import shutil

    home = Path.home()
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "bash":
        return home / ".bashrc"
    if shell == "powershell":
        docs = home / "Documents"
        if shutil.which("pwsh"):
            return docs / "PowerShell" / "Microsoft.PowerShell_profile.ps1"
        return docs / "WindowsPowerShell" / "Microsoft.PowerShell_profile.ps1"
    raise ValueError(f"unsupported shell: {shell!r}")


def hook_source_line(shell: str) -> str:
    """The single line (inside the block) that loads the hooks for `shell`."""
    if shell in ("zsh", "bash"):
        return f'source "$(terminalghost hook-path {shell})"'
    if shell == "powershell":
        return ". (terminalghost hook-path powershell)"
    raise ValueError(f"unsupported shell: {shell!r}")


def render_block(shell: str) -> str:
    return f"{BLOCK_START}\n{hook_source_line(shell)}\n{BLOCK_END}\n"


def install_block(profile: Path, shell: str) -> str:
    """Add/refresh the TerminalGhost block in `profile`.

    Returns one of: "installed" (block added), "updated" (block existed but
    differed and was replaced), "already" (block already current). Always
    produces the same canonical output, so it is safe to run repeatedly.
    """
    if shell not in HOOK_FILES:
        raise ValueError(f"unsupported shell: {shell!r}")
    block = render_block(shell)
    original = profile.read_text(encoding="utf-8") if profile.exists() else ""
    had_block = BLOCK_START in original

    base = (_BLOCK_RE.sub("\n", original) if had_block else original).rstrip("\n")
    final = block if base == "" else base + "\n\n" + block

    if final == original:
        return "already"
    _write(profile, final)
    return "updated" if had_block else "installed"


def uninstall_block(profile: Path) -> str:
    """Remove the TerminalGhost block. Returns "removed" or "absent"."""
    if not profile.exists():
        return "absent"
    existing = profile.read_text(encoding="utf-8")
    if BLOCK_START not in existing:
        return "absent"
    cleaned = _BLOCK_RE.sub("\n", existing).lstrip("\n")
    _write(profile, cleaned)
    return "removed"


def is_installed(profile: Path) -> bool:
    return profile.exists() and BLOCK_START in profile.read_text(encoding="utf-8")


def _write(profile: Path, text: str) -> None:
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(text, encoding="utf-8")
