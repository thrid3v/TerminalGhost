# terminalghost.cli.autostart
#
# `terminalghost enable` / `disable` — run the daemon at login. Each platform
# uses its native mechanism: a systemd user unit on Linux, a launchd agent on
# macOS, a Startup-folder script on Windows. The unit/plist text builders are
# pure functions so they can be unit-tested; the commands write + (de)register.

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SERVICE_NAME = "terminalghost"
LAUNCHD_LABEL = "com.terminalghost.daemon"


def _run_argv() -> list[str]:
    """Argv that runs the daemon in the foreground (managed by the OS)."""
    return [sys.executable, "-m", "terminalghost.daemon.process", "run"]


def systemd_unit_text() -> str:
    exec_start = " ".join(_run_argv())
    return (
        "[Unit]\n"
        "Description=TerminalGhost daemon\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def launchd_plist_text() -> str:
    args = "".join(f"    <string>{a}</string>\n" for a in _run_argv())
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "  <key>Label</key>\n"
        f"  <string>{LAUNCHD_LABEL}</string>\n"
        "  <key>ProgramArguments</key>\n"
        f"  <array>\n{args}  </array>\n"
        "  <key>RunAtLoad</key>\n"
        "  <true/>\n"
        "</dict>\n"
        "</plist>\n"
    )


# -- locations --------------------------------------------------------------


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _launchd_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _windows_startup_script() -> Path:
    appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    return (
        Path(appdata)
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "terminalghost.cmd"
    )


# -- commands ---------------------------------------------------------------


def cmd_enable(config) -> int:
    from terminalghost.ui import console_for

    console = console_for(config)
    try:
        if sys.platform == "linux":
            _enable_systemd(console)
        elif sys.platform == "darwin":
            _enable_launchd(console)
        elif os.name == "nt":
            _enable_windows(console)
        else:
            console.print(f"[tg.warn]Autostart not supported on {sys.platform}.[/]")
            return 1
    except OSError as exc:
        console.print(f"[tg.error]Could not enable autostart:[/] {exc}")
        return 1
    return 0


def cmd_disable(config) -> int:
    from terminalghost.ui import console_for

    console = console_for(config)
    if sys.platform == "linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME],
                       check=False)
        _unlink(_systemd_unit_path())
    elif sys.platform == "darwin":
        path = _launchd_plist_path()
        subprocess.run(["launchctl", "unload", str(path)], check=False)
        _unlink(path)
    elif os.name == "nt":
        _unlink(_windows_startup_script())
    else:
        console.print(f"[tg.warn]Autostart not supported on {sys.platform}.[/]")
        return 1
    console.print("[tg.success]✓[/] Autostart disabled.")
    return 0


def _enable_systemd(console) -> None:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(systemd_unit_text(), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=False)
    console.print(f"[tg.success]✓[/] Installed systemd user unit at [tg.key]{path}[/]")


def _enable_launchd(console) -> None:
    path = _launchd_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(launchd_plist_text(), encoding="utf-8")
    subprocess.run(["launchctl", "load", str(path)], check=False)
    console.print(f"[tg.success]✓[/] Installed launchd agent at [tg.key]{path}[/]")


def _enable_windows(console) -> None:
    path = _windows_startup_script()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@echo off\r\nterminalghost start\r\n", encoding="utf-8")
    console.print(f"[tg.success]✓[/] Added startup script at [tg.key]{path}[/]")


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
