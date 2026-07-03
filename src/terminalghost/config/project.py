# terminalghost.config.project
#
# Repo-local overrides: a `.terminalghost.toml` in (or above) the working
# directory can tighten privacy for that project — useful when one machine
# mixes personal work and a client codebase.
#
# SECURITY: project files ride along with repos you clone, so only settings
# that make TerminalGhost *stricter* are honored:
#
#   [capture]
#   capture_output = false          # drop output for this project (only false)
#   redact_passwords = true         # force redaction on (only true)
#   blocked_commands = ["kubectl"]  # extra patterns (additive)
#
#   [context]
#   project_context = false         # keep manifests/git state out of prompts
#   read_source = false             # never read this repo's source into a prompt
#
# Anything else — [llm], [general], loosening values — is ignored. A repo must
# never be able to switch your backend, point base_url at an attacker's
# server, or turn redaction off.

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass

log = logging.getLogger(__name__)

PROJECT_FILE = ".terminalghost.toml"
_MAX_WALK_UP = 12  # directory levels searched above cwd


@dataclass(frozen=True)
class ProjectOverrides:
    """Stricter-only settings taken from a project's .terminalghost.toml."""

    capture_output_off: bool = False
    redact_passwords_on: bool = False
    extra_blocked: tuple[str, ...] = ()
    project_context_off: bool = False
    read_source_off: bool = False
    path: str | None = None  # where the file was found (None → no overrides)


NO_OVERRIDES = ProjectOverrides()

# file path -> (mtime_ns, parsed) so repeated events don't re-read the file.
_cache: dict[str, tuple[int, ProjectOverrides]] = {}


def load_project_overrides(cwd: str) -> ProjectOverrides:
    """Overrides for `cwd` (walking up to find the project file), cached by mtime."""
    path = _find_project_file(cwd)
    if path is None:
        return NO_OVERRIDES
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return NO_OVERRIDES
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    overrides = _parse(path)
    _cache[path] = (mtime, overrides)
    return overrides


def _find_project_file(cwd: str) -> str | None:
    current = os.path.abspath(cwd)
    for _ in range(_MAX_WALK_UP):
        candidate = os.path.join(current, PROJECT_FILE)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:  # filesystem root
            return None
        current = parent
    return None


def _parse(path: str) -> ProjectOverrides:
    try:
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("ignoring malformed project config %s: %s", path, exc)
        return NO_OVERRIDES
    if not isinstance(raw, dict):
        return NO_OVERRIDES

    ignored = sorted(set(raw) - {"capture", "context"})
    if ignored:
        log.warning(
            "%s: section(s) %s are ignored in project config — only stricter"
            " [capture]/[context] privacy settings are honored",
            path, ", ".join(ignored),
        )

    capture = raw.get("capture")
    capture = capture if isinstance(capture, dict) else {}
    context = raw.get("context")
    context = context if isinstance(context, dict) else {}

    blocked = capture.get("blocked_commands")
    extra_blocked = tuple(
        p for p in blocked if isinstance(p, str) and p
    ) if isinstance(blocked, list) else ()

    return ProjectOverrides(
        # Stricter-only: `is False` / `is True` so the loosening direction
        # (and any mistyped value) is a no-op.
        capture_output_off=capture.get("capture_output") is False,
        redact_passwords_on=capture.get("redact_passwords") is True,
        extra_blocked=extra_blocked,
        project_context_off=context.get("project_context") is False,
        read_source_off=context.get("read_source") is False,
        path=path,
    )
