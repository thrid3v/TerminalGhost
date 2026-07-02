# terminalghost.cli.risk
#
# Risk assessment for suggested commands before they are executed by
# `apply` / the post-answer action bar. One-keypress execution is great UX
# until the suggestion is `rm -rf` — commands matching a destructive pattern
# get a plain-language warning and require a typed "yes" instead.
#
# This is a guardrail, not a sandbox: it catches the common destructive
# shapes, it does not try to prove a command safe.

from __future__ import annotations

import re

# (pattern, plain-language description). First match wins, so the more
# specific/destructive patterns come before the generic ones (sudo last).
_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\brm\s+(?:\S+\s+)*-[a-z]*[rf]", re.I),
     "deletes files without asking (rm -r/-f)"),
    (re.compile(r"\bremove-item\b.*(-recurse|-force)", re.I),
     "deletes files recursively/forcibly (Remove-Item)"),
    (re.compile(r"\b(del|erase)\s+/[fsq]|\b(rmdir|rd)\s+/s", re.I),
     "deletes files or directories without asking"),
    (re.compile(r"\bgit\s+push\b.*(\s--force(-with-lease)?\b|\s-f\b)"),
     "force-pushes, rewriting remote history"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"),
     "discards local changes irreversibly"),
    (re.compile(r"\bgit\s+clean\b.*\s-[a-z]*f", re.I),
     "deletes untracked files (git clean -f)"),
    (re.compile(r"\bdd\b.*\bof="),
     "overwrites a file or device directly (dd)"),
    (re.compile(r"\bmkfs(\.|\b)|^\s*format\s+[a-z]:", re.I),
     "formats a filesystem"),
    (re.compile(r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?[a-z]*sh\b", re.I),
     "pipes downloaded content straight into a shell"),
    (re.compile(r"\bch(mod|own)\b\s+(?:\S+\s+)*-[a-zA-Z]*R"),
     "changes permissions/ownership recursively"),
    (re.compile(r"(>>?|\btee\b)\s*/etc/"),
     "writes to system configuration (/etc)"),
    (re.compile(r"\b(drop|truncate)\s+(table|database|schema)\b", re.I),
     "destroys database objects"),
    (re.compile(r"\bkubectl\s+delete\b"),
     "deletes Kubernetes resources"),
    (re.compile(r"\bterraform\s+destroy\b"),
     "destroys provisioned infrastructure"),
    (re.compile(r"\bdocker\s+(system|volume|container|image)\s+(prune|rm)\b"),
     "removes Docker data"),
    (re.compile(r"\b(shutdown|reboot|poweroff)\b", re.I),
     "shuts down or restarts the machine"),
    (re.compile(r"\bsudo\b", re.I),
     "runs with elevated privileges (sudo)"),
)


def assess(command: str) -> str | None:
    """A plain-language warning if `command` looks destructive, else None."""
    for pattern, description in _PATTERNS:
        if pattern.search(command):
            return description
    return None
