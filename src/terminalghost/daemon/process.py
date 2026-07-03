# terminalghost.daemon.process
#
# The long-running background process that wires all modules together, plus
# the `terminalghost` CLI entrypoint (start / stop / status / restart / run /
# ask).
#
# Cross-platform notes (this replaces the original Unix-only double-fork
# design): `start` spawns a detached child process running the `run`
# subcommand — DETACHED_PROCESS on Windows, start_new_session on POSIX —
# with stdout/stderr redirected to a log file. Signal handlers fall back to
# signal.signal() where loop.add_signal_handler is unsupported (Windows
# Proactor loop); SIGHUP config hot-reload is installed only where SIGHUP
# exists.

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import re
import signal
import subprocess
import sys
import time

import psutil

from terminalghost.capture.shell_hooks import HookReceiver
from terminalghost.config.loader import Config, ConfigError, load_config
from terminalghost.config.project import load_project_overrides
from terminalghost.context.assembler import ContextAssembler
from terminalghost.llm.base import get_backend
from terminalghost.runtime import (
    effective_port as _effective_port,
    ensure_token,
    read_pid as _read_pid,
    remove_port_file as _remove_port_file,
    write_port_file as _write_port_file,
)
from terminalghost.storage.db import CommandEvent, Database
from terminalghost.trigger.handler import TriggerHandler

log = logging.getLogger(__name__)

DATA_DIR = os.path.join("~", ".local", "share", "terminalghost")
LOG_FILE = os.path.join(DATA_DIR, "daemon.log")

# Rotate the daemon log once it grows past this many bytes.
LOG_MAX_BYTES = 5 * 1024 * 1024


class Daemon:
    """Orchestrates lifecycle and wiring; no business logic lives here."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._config_path: str | None = None  # set by main() for SIGHUP reload
        self._db: Database | None = None
        self._trigger: TriggerHandler | None = None
        self._sessions: dict[int, int] = {}  # shell_pid → session_id
        self._default_session: int | None = None

    async def run(self) -> None:
        general = self._config.general
        self._write_pid_file(general.pid_file)
        receiver: HookReceiver | None = None
        try:
            self._db = Database(
                general.db_path,
                history_size=general.history_size,
                max_output_bytes=self._config.capture.max_output_bytes,
            )
            self._db.open()
            _assert_safe_host(general.host)
            auth_token = ensure_token()
            backend = get_backend(self._config)
            assembler = ContextAssembler(self._db, self._config)
            self._trigger = TriggerHandler(self._db, assembler, backend, self._config)

            receiver = HookReceiver(
                general.host,
                general.port,
                on_event=self._on_event,
                on_query=self._on_query,
                on_hint=self._on_hint,
                on_suggestion=self._on_suggestion,
                on_reload=self._on_reload,
                on_recap=self._on_recap,
                auth_token=auth_token,
            )
            await receiver.start()
            # Record the actually-bound port so clients can find us even when
            # general.port == 0 (ephemeral).
            _write_port_file(self._config, receiver.port)

            shutdown = asyncio.Event()
            self._install_signal_handlers(asyncio.get_running_loop(), shutdown)
            log.info("daemon running (pid %d) on %s:%d", os.getpid(),
                     general.host, receiver.port)
            await shutdown.wait()
            log.info("shutdown requested")
        finally:
            if receiver is not None:
                await receiver.stop()
            for session_id in self._sessions.values():
                try:
                    self._db.end_session(session_id)  # type: ignore[union-attr]
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    pass
            if self._db is not None:
                self._db.close()
            self._remove_pid_file(general.pid_file)
            _remove_port_file(self._config)

    # -- event wiring ------------------------------------------------------------

    async def _on_event(
        self, event: CommandEvent, shell_pid: int | None, shell: str | None
    ) -> None:
        """Persist a command event (with capture-policy filtering applied)."""
        assert self._db is not None
        event.session_id = self._session_for(shell_pid, shell)
        cap = self._config.capture
        # Repo-local .terminalghost.toml can only tighten these settings.
        proj = load_project_overrides(event.cwd)
        # Output: privacy gate first. Ambient hook output needs capture_output;
        # explicit `terminalghost exec` (source="run") is normally kept, but a
        # project-level opt-out silences even that (the repo is sensitive).
        # Blocked commands never store output regardless.
        keep_output = (
            (cap.capture_output or event.source == "run")
            and not proj.capture_output_off
            and not self._is_blocked(event.cmd, proj.extra_blocked)
        )
        redact = cap.redact_passwords or proj.redact_passwords_on
        if not keep_output:
            event.output = None
        elif event.output:
            event.output = _trim_output(event.output, cap.output_max_lines)
            if redact:
                event.output = _redact_output(event.output)
        # Redact secrets that live in the command text itself (tokens, creds-in-URLs).
        if redact:
            event.cmd = _redact_command(event.cmd)
        self._db.insert_command(event)

    async def _on_query(self, cmd: str, cwd: str, send, context: str | None = None) -> None:
        """Handle a ?? query: record it, then stream the answer back."""
        assert self._db is not None and self._trigger is not None
        # Redact the stored copy like any other command (`?? my TOKEN=... fails`);
        # the live query keeps the original text — the user typed it for the LLM.
        stored_cmd = cmd
        if (
            self._config.capture.redact_passwords
            or load_project_overrides(cwd).redact_passwords_on
        ):
            stored_cmd = _redact_command(cmd)
        self._db.insert_command(
            CommandEvent(
                session_id=self._session_for(None, None),
                ts=time.time(),
                cwd=cwd,
                cmd=stored_cmd,
                exit_code=0,
                duration_ms=0,
            )
        )
        await self._trigger.handle(cmd, cwd, send=send, pasted=context)

    async def _on_hint(self, cwd: str, send) -> None:
        """Stream a one-line proactive hint (best-effort; records nothing)."""
        assert self._trigger is not None
        await self._trigger.hint(cwd, send)

    async def _on_suggestion(self, send) -> None:
        """Write back the last suggested plan, one command per line (for `apply`).

        Commands are extracted per-line so none can contain a newline; the
        newline-joined wire format is unambiguous.
        """
        assert self._trigger is not None
        commands = self._trigger.last_suggestions()
        if commands:
            await send("\n".join(commands))

    async def _on_reload(self, send) -> None:
        """Hot-reload config (used by `terminalghost use` to switch backends)."""
        self._reload_config()
        await send("ok")

    async def _on_recap(self, since: float, send) -> None:
        """Stream a session summary (what broke, what fixed it)."""
        assert self._trigger is not None
        await self._trigger.recap(since, send)

    def _session_for(self, shell_pid: int | None, shell: str | None) -> int:
        assert self._db is not None
        if shell_pid is None:
            if self._default_session is None:
                self._default_session = self._db.start_session(0, shell or "unknown")
            return self._default_session
        if shell_pid not in self._sessions:
            self._sessions[shell_pid] = self._db.start_session(
                shell_pid, shell or "unknown"
            )
        return self._sessions[shell_pid]

    def _is_blocked(self, cmd: str, extra: tuple[str, ...] = ()) -> bool:
        for pattern in (*self._config.capture.blocked_commands, *extra):
            try:
                if re.search(pattern, cmd):
                    return True
            except re.error:
                log.warning("invalid blocked_commands pattern: %r", pattern)
        return False

    # -- signals -------------------------------------------------------------------

    def _install_signal_handlers(
        self, loop: asyncio.AbstractEventLoop, shutdown_event: asyncio.Event
    ) -> None:
        def request_shutdown(*_args) -> None:
            loop.call_soon_threadsafe(shutdown_event.set)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown_event.set)
            except (NotImplementedError, RuntimeError):
                # Windows Proactor loop: fall back to a plain signal handler.
                signal.signal(sig, request_shutdown)

        if hasattr(signal, "SIGHUP"):
            try:
                loop.add_signal_handler(signal.SIGHUP, self._reload_config)
            except (NotImplementedError, RuntimeError):
                pass

    def _reload_config(self) -> None:
        """SIGHUP hot-reload: only LLM settings are re-applied (DB and socket
        server keep running)."""
        try:
            new_config = load_config(self._config_path)
        except ConfigError as exc:
            log.error("config reload failed, keeping old config: %s", exc)
            return
        self._config = new_config
        if self._trigger is not None:
            self._trigger.set_backend(get_backend(new_config))
        log.info("config reloaded (llm backend: %s)", new_config.llm.backend)

    # -- PID file --------------------------------------------------------------------

    def _write_pid_file(self, path: str) -> None:
        if os.path.exists(path):
            pid = _read_pid(path)
            if pid is not None and psutil.pid_exists(pid):
                raise RuntimeError(
                    f"terminalghost daemon already running (pid {pid});"
                    " stop it first or remove the stale PID file"
                )
            # stale PID file from a crashed daemon — overwrite
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(os.getpid()))

    @staticmethod
    def _remove_pid_file(path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _assert_safe_host(host: str) -> None:
    """Refuse to expose the daemon off-machine.

    The socket's auth token only guards against other local users; it travels
    unencrypted and the runtime files live on this machine, so binding a
    routable address would hand shell history + LLM access to the network.
    Require an explicit opt-in for that.
    """
    if _is_loopback(host) or os.environ.get("TG_ALLOW_REMOTE") == "1":
        return
    raise RuntimeError(
        f"refusing to bind non-loopback host {host!r}: this would expose your "
        "shell history and LLM to the network with no authentication. Use a "
        "loopback host (127.0.0.1), or set TG_ALLOW_REMOTE=1 to override."
    )


def _trim_output(output: str, max_lines: int) -> str:
    """Keep only the last `max_lines` lines (errors are usually at the end)."""
    lines = output.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


# Secret-bearing patterns, redacted before storage so they never reach the
# database or a cloud LLM. Applied to both command text and captured output.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)[A-Z0-9_]*)=(\S+)"
)
_SECRET_FLAG_RE = re.compile(
    r"(?i)(--?(?:password|passwd|pass|token|secret|api[_-]?key)[=\s])(\S+)"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})")
_URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.\-]*://[^:@/\s]+):([^@/\s]+)@")
# Well-known token literals (GitHub, OpenAI/Anthropic, Slack, AWS access keys).
_TOKEN_LITERAL_RE = re.compile(
    r"\b("
    r"gh[opsu]_[A-Za-z0-9]{20,}"
    r"|sk-(?:ant-)?[A-Za-z0-9_\-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r")\b"
)


def _redact_secrets(text: str) -> str:
    """Redact obvious secrets (assignments, flags, bearer tokens, URL creds,
    well-known key literals) anywhere in `text`."""
    text = _SECRET_ASSIGN_RE.sub(r"\1=<redacted>", text)
    text = _SECRET_FLAG_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _TOKEN_LITERAL_RE.sub("<redacted>", text)
    text = _URL_CRED_RE.sub(r"\1:<redacted>@", text)
    return text


def _redact_command(cmd: str) -> str:
    return _redact_secrets(cmd)


def _redact_output(output: str) -> str:
    """Mask secret-looking value lines, then redact token literals everywhere."""
    sensitive = re.compile(r"(?i)\b(password|passphrase|token|secret|api[-_]?key)\b")
    redacted = []
    suppress_next = False
    for line in output.splitlines():
        if suppress_next:
            redacted.append("<redacted>")
            suppress_next = False
        elif sensitive.search(line):
            redacted.append("<redacted>")
            suppress_next = True  # the following line is often the echoed value
        else:
            redacted.append(line)
    return _redact_secrets("\n".join(redacted))


def _cmd_redact_check(config: Config, text: str) -> int:
    """Dry-run the storage redaction on `text` and show the result.

    Turns the redaction feature into something users can verify ("what would
    TerminalGhost keep if I typed this?") instead of a black box. Nothing is
    stored or sent anywhere.
    """
    from rich.markup import escape

    from terminalghost.ui import console_for

    console = console_for(config)
    if not config.capture.redact_passwords:
        console.print(
            "[tg.warn]![/] capture.redact_passwords is disabled — commands are "
            "stored verbatim. Enable it in config.toml to redact secrets."
        )
        return 1
    redacted = _redact_command(text)
    if redacted == text:
        console.print("[tg.success]✓[/] No secrets detected; this would be stored as-is:")
        console.print(f"  [tg.cmd]{escape(text)}[/]")
    else:
        highlighted = escape(redacted).replace(
            "<redacted>", "[tg.warn]<redacted>[/tg.warn]"
        )
        console.print("[tg.warn]![/] Secrets detected — this would be stored as:")
        console.print(f"  [tg.cmd]{highlighted}[/]")
    return 0


# -- CLI ------------------------------------------------------------------------------


def _force_utf8_io() -> None:
    """Make stdout/stderr UTF-8 so Rich never crashes on a legacy Windows
    console (cp1252) when emitting ✓, box-drawing, or model output."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass  # non-reconfigurable stream (e.g. a pipe wrapper) — best effort


def main() -> None:
    from terminalghost import __version__

    _force_utf8_io()
    parser = argparse.ArgumentParser(
        prog="terminalghost",
        description="Local-first terminal AI assistant daemon",
    )
    parser.add_argument("--config", help="explicit config file path", default=None)
    parser.add_argument(
        "--version",
        action="version",
        version=f"terminalghost {__version__}",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="start the daemon in the background")
    sub.add_parser("stop", help="stop the running daemon")
    sub.add_parser("status", help="show whether the daemon is running")
    sub.add_parser("restart", help="stop then start the daemon")
    sub.add_parser("run", help="run the daemon in the foreground (used by start)")
    ask = sub.add_parser("ask", help="send a ?? query to the daemon and print the answer")
    ask.add_argument("text", nargs="*", help="optional extra context after ??")
    ask.add_argument("-c", "--copy", action="store_true",
                     help="copy the answer to the clipboard")
    exec_p = sub.add_parser(
        "exec", help="run a command and capture its output for ?? (alias: tgr)"
    )
    exec_p.add_argument("cmd", nargs=argparse.REMAINDER, help="the command to run")
    apply_p = sub.add_parser(
        "apply", help="run the last suggested fix, or a saved one (alias: tga)"
    )
    apply_p.add_argument("name", nargs="?", default=None,
                         help="a saved fix to run (see: terminalghost fixes)")
    apply_p.add_argument("-y", "--yes", action="store_true",
                         help="run without confirmation")
    save_p = sub.add_parser(
        "save", help="save the last suggested fix under a name (alias: tgs)"
    )
    save_p.add_argument("name", help="name for the fix, e.g. jest-cache")
    save_p.add_argument("-n", "--note", default="", help="what this fixes")
    fixes_p = sub.add_parser("fixes", help="list saved fixes (your personal runbook)")
    fixes_p.add_argument("--grep", default=None, metavar="TEXT",
                         help="only fixes whose name/commands contain TEXT")
    fixes_p.add_argument("--delete", default=None, metavar="NAME",
                         help="delete the named fix")

    sub.add_parser("hint", help="print a one-line proactive hint for the last failure")
    recap_p = sub.add_parser(
        "recap", help="summarize this session: what broke, what fixed it"
    )
    recap_p.add_argument("--since", default="8h", metavar="AGE",
                         help="window to summarize (e.g. 90m, 8h, 1d; default 8h)")
    sub.add_parser("init", help="interactive first-time setup wizard")
    sub.add_parser("doctor", help="diagnose the installation and print fixes")
    log_p = sub.add_parser("log", help="show recently captured commands")
    log_p.add_argument("-n", "--limit", type=int, default=20, help="how many to show")
    log_p.add_argument("--failed", action="store_true",
                       help="only commands that exited non-zero")
    log_p.add_argument("--cwd", nargs="?", const=".", default=None, metavar="DIR",
                       help="only commands run in DIR (bare --cwd = here)")
    log_p.add_argument("--since", default=None, metavar="AGE",
                       help="only commands newer than AGE (e.g. 90s, 15m, 2h, 3d)")
    log_p.add_argument("--grep", default=None, metavar="TEXT",
                       help="only commands containing TEXT (case-insensitive)")
    clear_p = sub.add_parser("clear", help="delete captured command history")
    clear_p.add_argument("--last", type=int, default=None, metavar="N",
                         help="delete only the N most recent commands")
    clear_p.add_argument("-y", "--yes", action="store_true",
                         help="delete without confirmation")
    export_p = sub.add_parser(
        "export", help="dump captured history as JSON (stdout or a file)"
    )
    export_p.add_argument("-o", "--out", default=None, metavar="FILE",
                          help="write to FILE instead of stdout")
    import_p = sub.add_parser(
        "import", help="load a history snapshot produced by export"
    )
    import_p.add_argument("file", help="snapshot file to import")
    rc_p = sub.add_parser(
        "redact-check",
        help="dry-run: show what would be stored for a command (nothing is saved)",
    )
    rc_p.add_argument("text", nargs="+", help="the command to test")
    hp = sub.add_parser("hook-path", help="print the path to a bundled shell hook script")
    hp.add_argument("shell", help="shell name (zsh | bash | powershell)")
    uninst = sub.add_parser("uninstall", help="remove the TerminalGhost block from your shell profile")
    uninst.add_argument("--shell", help="shell to uninstall from (default: autodetect)")
    enable = sub.add_parser("enable", help="start the daemon automatically at login")
    enable.add_argument("--shell", help=argparse.SUPPRESS)
    sub.add_parser("disable", help="stop starting the daemon at login")
    sub.add_parser("cheatsheet", help="show the TerminalGhost command cheatsheet")
    theme_p = sub.add_parser("theme", help="set the color theme")
    theme_p.add_argument("name", help="dark | light | high-contrast")
    use_p = sub.add_parser("use", help="switch LLM backend/model, e.g. use ollama:mistral")
    use_p.add_argument("target", help="backend[:model] (ollama | claude | openai)")
    dash = sub.add_parser("dashboard", help="open the full-screen TerminalGhost dashboard")
    dash.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    explain_p = sub.add_parser(
        "explain", help="explain piped output or a file (e.g. make 2>&1 | tg explain)"
    )
    explain_p.add_argument("file", nargs="?", help="file to explain (default: stdin)")
    args = parser.parse_args()

    # hook-path must stay silent + dependency-free: it runs on every shell start.
    if args.command == "hook-path":
        sys.exit(_cmd_hook_path(args.shell))

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        sys.exit(1)

    command = args.command
    if command == "run":
        _cmd_run(config, args.config)
    elif command == "start":
        sys.exit(_cmd_start(config, args.config))
    elif command == "stop":
        sys.exit(_cmd_stop(config))
    elif command == "status":
        sys.exit(_cmd_status(config))
    elif command == "restart":
        _cmd_stop(config)
        sys.exit(_cmd_start(config, args.config))
    elif command == "ask":
        from terminalghost.cli import client

        sys.exit(client._cmd_ask(config, " ".join(args.text), copy=args.copy))
    elif command == "hint":
        from terminalghost.cli import client

        sys.exit(client._cmd_hint(config))
    elif command == "recap":
        from terminalghost.cli import client

        sys.exit(client._cmd_recap(config, args.since))
    elif command == "exec":
        from terminalghost.cli import client

        sys.exit(client._cmd_exec(config, client._build_cmdline(args.cmd)))
    elif command == "apply":
        from terminalghost.cli import client

        sys.exit(client._cmd_apply(config, assume_yes=args.yes, name=args.name))
    elif command == "save":
        from terminalghost.cli.fixes import cmd_save

        sys.exit(cmd_save(config, args.name, note=args.note))
    elif command == "fixes":
        from terminalghost.cli.fixes import cmd_fixes

        sys.exit(cmd_fixes(config, grep=args.grep, delete=args.delete))
    elif command == "init":
        from terminalghost.cli.init import cmd_init

        sys.exit(cmd_init(config, args.config))
    elif command == "doctor":
        from terminalghost.cli.doctor import cmd_doctor

        sys.exit(cmd_doctor(config))
    elif command == "log":
        from terminalghost.cli.logview import cmd_log

        sys.exit(cmd_log(config, args.limit, failed=args.failed, cwd=args.cwd,
                         since=args.since, grep=args.grep))
    elif command == "clear":
        from terminalghost.cli.logview import cmd_clear

        sys.exit(cmd_clear(config, args.last, args.yes))
    elif command == "export":
        from terminalghost.cli.transfer import cmd_export

        sys.exit(cmd_export(config, args.out))
    elif command == "import":
        from terminalghost.cli.transfer import cmd_import

        sys.exit(cmd_import(config, args.file))
    elif command == "redact-check":
        sys.exit(_cmd_redact_check(config, " ".join(args.text)))
    elif command == "uninstall":
        from terminalghost.cli.init import cmd_uninstall

        sys.exit(cmd_uninstall(config, args.shell))
    elif command == "enable":
        from terminalghost.cli.autostart import cmd_enable

        sys.exit(cmd_enable(config))
    elif command == "disable":
        from terminalghost.cli.autostart import cmd_disable

        sys.exit(cmd_disable(config))
    elif command == "cheatsheet":
        from terminalghost.cli.cheatsheet import cmd_cheatsheet

        sys.exit(cmd_cheatsheet(config))
    elif command == "theme":
        from terminalghost.cli.settings import cmd_theme

        sys.exit(cmd_theme(config, args.config, args.name))
    elif command == "use":
        from terminalghost.cli.settings import cmd_use

        sys.exit(cmd_use(config, args.config, args.target))
    elif command == "dashboard":
        try:
            from terminalghost.cli.dashboard import cmd_dashboard
        except ImportError:
            print(
                "The dashboard needs textual: pip install textual",
                file=sys.stderr,
            )
            sys.exit(1)
        sys.exit(cmd_dashboard(config))
    elif command == "explain":
        from terminalghost.cli import client

        sys.exit(client._cmd_explain(config, args.file))


def _cmd_hook_path(shell: str) -> int:
    """Print the absolute path to the bundled hook script for `shell`.

    Sourced by the profile block on every shell start, so it must print only
    the path (no banner/log) and never import heavy modules.
    """
    from terminalghost._assets import HOOK_FILES, hook_path

    key = shell.strip().lower()
    if key not in HOOK_FILES:
        print(
            f"unknown shell {shell!r}; expected one of {sorted(set(HOOK_FILES))}",
            file=sys.stderr,
        )
        return 1
    print(hook_path(key))
    return 0


def _cmd_run(config: Config, config_path: str | None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    daemon = Daemon(config)
    daemon._config_path = config_path
    try:
        asyncio.run(daemon.run())
    except RuntimeError as exc:  # e.g. already running
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


def _daemon_pid(config: Config) -> int | None:
    pid = _read_pid(config.general.pid_file)
    if pid is not None and psutil.pid_exists(pid):
        return pid
    return None


def _cmd_start(config: Config, config_path: str | None) -> int:
    if _daemon_pid(config) is not None:
        print(f"daemon already running (pid {_daemon_pid(config)})", file=sys.stderr)
        return 1
    log_path = os.path.expanduser(LOG_FILE)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _rotate_log(log_path)
    cmd = [sys.executable, "-m", "terminalghost.daemon.process"]
    if config_path:
        cmd += ["--config", config_path]
    cmd.append("run")
    with open(log_path, "ab") as log_fh:
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                creationflags=flags,
            )
        else:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=log_fh,
                stderr=log_fh,
                start_new_session=True,
            )
    # Wait for the daemon to write its PID file. proc.pid may be a launcher
    # shim (e.g. a venv python re-exec), so the PID file is the real daemon.
    real_pid = None
    for _ in range(20):  # up to ~2s
        time.sleep(0.1)
        real_pid = _daemon_pid(config)
        if real_pid is not None:
            break
        if proc.poll() is not None:  # child exited without writing a PID
            break
    if real_pid is None:
        print(f"daemon failed to start; see {log_path}", file=sys.stderr)
        return 1
    _show_banner(config)
    from rich.markup import escape

    from terminalghost.cli.tips import random_tip
    from terminalghost.ui import console_for

    console = console_for(config)
    port = _effective_port(config)  # resolves the bound port when general.port == 0
    console.print(
        f"[tg.success]✓[/] daemon started [tg.subtle](pid {real_pid})[/] · "
        f"listening on [tg.key]{config.general.host}:{port}[/]"
    )
    console.print(f"  [tg.eyebrow]tip:[/] [tg.subtle]{escape(random_tip())}[/]")
    return 0


def _rotate_log(log_path: str, max_bytes: int = LOG_MAX_BYTES) -> None:
    """Rename the daemon log to .1 once it grows too large (keeps one backup)."""
    try:
        if os.path.getsize(log_path) < max_bytes:
            return
    except OSError:
        return
    backup = log_path + ".1"
    try:
        if os.path.exists(backup):
            os.remove(backup)
        os.replace(log_path, backup)
    except OSError:
        pass


def _show_banner(config: Config) -> None:
    """Print the brand banner unless disabled in config."""
    if not config.ui.banner:
        return
    from terminalghost import __version__
    from terminalghost.ui import console_for, render_banner

    console = console_for(config)
    console.print(render_banner(__version__, backend=config.llm.backend))


def _cmd_stop(config: Config) -> int:
    pid = _daemon_pid(config)
    if pid is None:
        print("daemon is not running")
        return 0
    try:
        proc = psutil.Process(pid)
        proc.terminate()  # SIGTERM on POSIX; TerminateProcess on Windows
        proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        pass
    except psutil.TimeoutExpired:
        print(f"daemon (pid {pid}) did not exit within 5s", file=sys.stderr)
        return 1
    Daemon._remove_pid_file(config.general.pid_file)
    # On Windows terminate() is TerminateProcess, so the daemon's own cleanup
    # never runs — remove the runtime port file here too (idempotent).
    _remove_port_file(config)
    print("daemon stopped")
    return 0


def _cmd_status(config: Config) -> int:
    pid = _daemon_pid(config)
    if pid is None:
        print("stopped")
        return 1
    print(f"running (pid {pid})")
    return 0


if __name__ == "__main__":
    main()
