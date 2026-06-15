# terminalghost.runtime
#
# Small filesystem helpers for the daemon's runtime files (PID + bound port),
# shared by the daemon (which writes them) and the CLI clients (which read the
# port). Kept dependency-light to avoid import cycles between daemon and cli.

from __future__ import annotations

import os
import secrets
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminalghost.config.loader import Config

# Fixed location (independent of config) so the daemon, the CLI clients, and the
# shell hooks all agree on where the auth token lives without sharing config.
TOKEN_PATH = os.path.join("~", ".local", "share", "terminalghost", "token")


def token_path() -> str:
    return os.path.expanduser(TOKEN_PATH)


def ensure_token() -> str:
    """Return the daemon's auth token, generating + persisting one (0600) if
    needed. Stable across restarts so already-sourced hooks keep working."""
    path = token_path()
    existing = read_token()
    if existing:
        return existing
    token = secrets.token_hex(16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Create with owner-only perms where the OS honors them (POSIX).
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(token)
    except OSError:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(token)
    return token


def read_token() -> str:
    """The current auth token, or "" if none has been written yet."""
    try:
        with open(token_path(), encoding="ascii") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def read_pid(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def port_file_path(config: "Config") -> str:
    """Runtime file holding the daemon's actually-bound port (next to the PID)."""
    return os.path.join(os.path.dirname(os.path.abspath(config.general.pid_file)), "port")


def write_port_file(config: "Config", port: int) -> None:
    path = port_file_path(config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(port))
    except OSError:
        pass  # best-effort; clients fall back to the configured port


def remove_port_file(config: "Config") -> None:
    try:
        os.remove(port_file_path(config))
    except FileNotFoundError:
        pass


def effective_port(config: "Config") -> int:
    """Port a client should connect to: the configured one, or — when that is 0
    (ephemeral) — the bound port the daemon wrote to its runtime file."""
    if config.general.port != 0:
        return config.general.port
    try:
        with open(port_file_path(config), encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return config.general.port
