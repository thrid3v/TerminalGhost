# terminalghost.cli.doctor
#
# `terminalghost doctor` — a quick health check. Each line is a ✓/✗/! with a
# concrete fix, so "it doesn't work" becomes self-serve. Returns non-zero if
# any critical check fails (handy in scripts).

from __future__ import annotations

import os
import socket
from dataclasses import dataclass


@dataclass
class Check:
    ok: bool | None  # True ok, False fail, None warning/info
    label: str
    detail: str = ""
    fix: str = ""


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def gather_checks(config) -> list[Check]:
    """Run all diagnostics and return them (pure-ish; no printing)."""
    from terminalghost.cli import profiles
    from terminalghost.cli.init import ollama_status
    from terminalghost.daemon.process import _daemon_pid
    from terminalghost.runtime import effective_port

    checks: list[Check] = []

    # Daemon
    pid = _daemon_pid(config)
    checks.append(
        Check(
            pid is not None,
            "Daemon running",
            f"pid {pid}" if pid else "not running",
            "" if pid else "terminalghost start",
        )
    )

    # Socket reachable — resolve the runtime port file when general.port == 0
    # (ephemeral), same as the ask/hint clients do.
    port = effective_port(config)
    reachable = _port_open(config.general.host, port)
    checks.append(
        Check(
            reachable,
            "Daemon reachable",
            f"{config.general.host}:{port}",
            "" if reachable else "terminalghost start  (or check general.port in config)",
        )
    )

    # LLM backend
    backend = config.llm.backend
    if backend == "ollama":
        state, _ = ollama_status(config.llm.ollama.base_url, config.llm.ollama.model)
        checks.append(
            Check(
                state == "ready",
                f"Ollama ({config.llm.ollama.model})",
                {"ready": "running, model present",
                 "missing": "server up, model not pulled",
                 "down": "server unreachable"}[state],
                {"ready": "",
                 "missing": f"ollama pull {config.llm.ollama.model}",
                 "down": "ollama serve"}[state],
            )
        )
    elif backend == "claude":
        has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or config.llm.claude.api_key)
        checks.append(
            Check(has_key, "Claude API key",
                  "configured" if has_key else "missing",
                  "" if has_key else "export ANTHROPIC_API_KEY=sk-ant-...")
        )
        try:
            import anthropic  # noqa: F401

            sdk_ok = True
        except ImportError:
            sdk_ok = False
        checks.append(
            Check(sdk_ok, "anthropic SDK",
                  "installed" if sdk_ok else "missing",
                  "" if sdk_ok else 'pipx install "terminalghost[cloud]"')
        )
    else:
        checks.append(Check(None, f"Backend '{backend}'", "no health check", ""))

    # Shell hooks
    shell = profiles.detect_shell()
    if shell and shell in profiles.SUPPORTED_SHELLS:
        profile = profiles.profile_path(shell)
        installed = profiles.is_installed(profile)
        checks.append(
            Check(installed, f"Shell hooks ({shell})",
                  str(profile) if installed else f"not found in {profile}",
                  "" if installed else "terminalghost init")
        )
    else:
        checks.append(Check(None, "Shell hooks", "shell not detected",
                            "terminalghost init"))

    # Data dir writable
    data_dir = os.path.dirname(config.general.db_path)
    writable = _dir_writable(data_dir)
    checks.append(
        Check(writable, "Data directory", data_dir,
              "" if writable else f"check permissions on {data_dir}")
    )

    return checks


def _dir_writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return os.access(path, os.W_OK)
    except OSError:
        return False


def cmd_doctor(config) -> int:
    from rich.markup import escape

    from terminalghost.ui import console_for

    console = console_for(config)
    console.print("[tg.header]TerminalGhost doctor[/]\n")

    checks = gather_checks(config)
    symbol = {True: "[tg.ok]✓[/]", False: "[tg.fail]✗[/]", None: "[tg.warn]![/]"}
    failed = 0
    for c in checks:
        # Detail/fix can contain [..] (e.g. terminalghost[cloud]) — escape so
        # Rich doesn't treat them as markup tags.
        line = f"  {symbol[c.ok]} {escape(c.label)}"
        if c.detail:
            line += f"  [tg.muted]{escape(c.detail)}[/]"
        console.print(line)
        if c.ok is False:
            failed += 1
            if c.fix:
                console.print(f"      [tg.muted]fix:[/] [tg.key]{escape(c.fix)}[/]")

    console.print()
    if failed == 0:
        console.print("[tg.success]Everything looks good.[/]")
        return 0
    console.print(f"[tg.warn]{failed} issue(s) found.[/] See fixes above.")
    return 1
