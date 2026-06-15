# terminalghost.runtime
#
# Small filesystem helpers for the daemon's runtime files (PID + bound port),
# shared by the daemon (which writes them) and the CLI clients (which read the
# port). Kept dependency-light to avoid import cycles between daemon and cli.

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminalghost.config.loader import Config


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
