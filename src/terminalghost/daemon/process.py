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
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import time

import psutil

from terminalghost.capture.shell_hooks import HookReceiver
from terminalghost.config.loader import Config, ConfigError, load_config
from terminalghost.context.assembler import ContextAssembler
from terminalghost.llm.base import get_backend
from terminalghost.storage.db import CommandEvent, Database
from terminalghost.trigger.handler import TriggerHandler

log = logging.getLogger(__name__)

DATA_DIR = os.path.join("~", ".local", "share", "terminalghost")
LOG_FILE = os.path.join(DATA_DIR, "daemon.log")

# Client read timeout for `ask`: large enough for a cold model's first token,
# small enough that a wedged daemon doesn't hang the prompt forever.
ASK_IDLE_TIMEOUT = 300.0
# Proactive hint: short read timeout so a cold/slow model just yields no hint
# rather than stalling the user's prompt.
HINT_TIMEOUT = 12.0
# Rotate the daemon log once it grows past this many bytes.
LOG_MAX_BYTES = 5 * 1024 * 1024
# Cap on captured output the `exec` wrapper keeps (tail); the daemon trims more.
EXEC_CAPTURE_BYTES = 64 * 1024


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
        # Output: privacy gate first. Ambient hook output needs capture_output;
        # explicit `terminalghost exec` (source="run") is always kept. Blocked
        # commands never store output regardless.
        keep_output = (cap.capture_output or event.source == "run") and not self._is_blocked(
            event.cmd
        )
        if not keep_output:
            event.output = None
        elif event.output:
            event.output = _trim_output(event.output, cap.output_max_lines)
            if cap.redact_passwords:
                event.output = _redact_output(event.output)
        # Redact secrets that live in the command text itself (tokens, creds-in-URLs).
        if cap.redact_passwords:
            event.cmd = _redact_command(event.cmd)
        self._db.insert_command(event)

    async def _on_query(self, cmd: str, cwd: str, send) -> None:
        """Handle a ?? query: record it, then stream the answer back."""
        assert self._db is not None and self._trigger is not None
        self._db.insert_command(
            CommandEvent(
                session_id=self._session_for(None, None),
                ts=time.time(),
                cwd=cwd,
                cmd=cmd,
                exit_code=0,
                duration_ms=0,
            )
        )
        await self._trigger.handle(cmd, cwd, send=send)

    async def _on_hint(self, cwd: str, send) -> None:
        """Stream a one-line proactive hint (best-effort; records nothing)."""
        assert self._trigger is not None
        await self._trigger.hint(cwd, send)

    async def _on_suggestion(self, send) -> None:
        """Write back the last command TerminalGhost suggested (for `apply`)."""
        assert self._trigger is not None
        command = self._trigger.last_suggestion()
        if command:
            await send(command)

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

    def _is_blocked(self, cmd: str) -> bool:
        for pattern in self._config.capture.blocked_commands:
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


def _trim_output(output: str, max_lines: int) -> str:
    """Keep only the last `max_lines` lines (errors are usually at the end)."""
    lines = output.splitlines()
    if max_lines > 0 and len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


# Secret-bearing command patterns, redacted before storage so they never reach
# the database or a cloud LLM.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)[A-Z0-9_]*)=(\S+)"
)
_SECRET_FLAG_RE = re.compile(
    r"(?i)(--?(?:password|passwd|pass|token|secret|api[_-]?key)[=\s])(\S+)"
)
_URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.\-]*://[^:@/\s]+):([^@/\s]+)@")


def _redact_command(cmd: str) -> str:
    """Redact obvious secrets in a command line (assignments, flags, URL creds)."""
    cmd = _SECRET_ASSIGN_RE.sub(r"\1=<redacted>", cmd)
    cmd = _SECRET_FLAG_RE.sub(r"\1<redacted>", cmd)
    cmd = _URL_CRED_RE.sub(r"\1:<redacted>@", cmd)
    return cmd


def _redact_output(output: str) -> str:
    """Replace lines that look like password/secret prompts or values."""
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
    return "\n".join(redacted)


def _read_pid(path: str) -> int | None:
    try:
        with open(path, encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _port_file_path(config: Config) -> str:
    """Runtime file holding the daemon's actually-bound port (next to the PID)."""
    return os.path.join(os.path.dirname(os.path.abspath(config.general.pid_file)), "port")


def _write_port_file(config: Config, port: int) -> None:
    path = _port_file_path(config)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(str(port))
    except OSError:
        pass  # best-effort; clients fall back to the configured port


def _remove_port_file(config: Config) -> None:
    try:
        os.remove(_port_file_path(config))
    except FileNotFoundError:
        pass


def _effective_port(config: Config) -> int:
    """Port a client should connect to: the configured one, or — when that is 0
    (ephemeral) — the bound port the daemon wrote to its runtime file."""
    if config.general.port != 0:
        return config.general.port
    try:
        with open(_port_file_path(config), encoding="ascii") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return config.general.port


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
        "apply", help="run the command TerminalGhost last suggested (alias: tga)"
    )
    apply_p.add_argument("-y", "--yes", action="store_true",
                         help="run without confirmation")

    sub.add_parser("hint", help="print a one-line proactive hint for the last failure")
    sub.add_parser("init", help="interactive first-time setup wizard")
    sub.add_parser("doctor", help="diagnose the installation and print fixes")
    log_p = sub.add_parser("log", help="show recently captured commands")
    log_p.add_argument("-n", "--limit", type=int, default=20, help="how many to show")
    hp = sub.add_parser("hook-path", help="print the path to a bundled shell hook script")
    hp.add_argument("shell", help="shell name (zsh | bash | powershell)")
    uninst = sub.add_parser("uninstall", help="remove the TerminalGhost block from your shell profile")
    uninst.add_argument("--shell", help="shell to uninstall from (default: autodetect)")
    enable = sub.add_parser("enable", help="start the daemon automatically at login")
    enable.add_argument("--shell", help=argparse.SUPPRESS)
    sub.add_parser("disable", help="stop starting the daemon at login")
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
        sys.exit(_cmd_ask(config, " ".join(args.text), copy=args.copy))
    elif command == "hint":
        sys.exit(_cmd_hint(config))
    elif command == "exec":
        sys.exit(_cmd_exec(config, args.cmd))
    elif command == "apply":
        sys.exit(_cmd_apply(config, assume_yes=args.yes))
    elif command == "init":
        from terminalghost.cli.init import cmd_init

        sys.exit(cmd_init(config, args.config))
    elif command == "doctor":
        from terminalghost.cli.doctor import cmd_doctor

        sys.exit(cmd_doctor(config))
    elif command == "log":
        from terminalghost.cli.logview import cmd_log

        sys.exit(cmd_log(config, args.limit))
    elif command == "uninstall":
        from terminalghost.cli.init import cmd_uninstall

        sys.exit(cmd_uninstall(config, args.shell))
    elif command == "enable":
        from terminalghost.cli.autostart import cmd_enable

        sys.exit(cmd_enable(config))
    elif command == "disable":
        from terminalghost.cli.autostart import cmd_disable

        sys.exit(cmd_disable(config))


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
    # Give the child a moment to bind / write its PID file or fail fast.
    time.sleep(0.5)
    if proc.poll() is not None:
        print(f"daemon failed to start; see {log_path}", file=sys.stderr)
        return 1
    _show_banner(config)
    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color)
    port = _effective_port(config)  # resolves the bound port when general.port == 0
    console.print(
        f"[tg.success]✓[/] daemon started (pid {proc.pid}), listening on "
        f"[tg.key]{config.general.host}:{port}[/]"
    )
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
    from terminalghost.ui import get_console, render_banner

    console = get_console(color=config.ui.color)
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
    print("daemon stopped")
    return 0


def _cmd_status(config: Config) -> int:
    pid = _daemon_pid(config)
    if pid is None:
        print("stopped")
        return 1
    print(f"running (pid {pid})")
    return 0


def _answering_model(config: Config) -> str:
    """The model name the configured backend will answer with (for the footer)."""
    backend = config.llm.backend
    if backend == "ollama":
        return config.llm.ollama.model
    if backend == "claude":
        return config.llm.claude.model
    if backend == "openai":
        return config.llm.openai.model
    return backend


def _iter_socket_text(sock: socket.socket):
    """Yield decoded text chunks from the daemon's streamed answer until EOF."""
    while True:
        data = sock.recv(4096)
        if not data:
            return
        yield data.decode("utf-8", errors="replace")


def _cmd_ask(config: Config, text: str, copy: bool = False) -> int:
    """Send a ?? query to the daemon and render the streamed answer.

    This is the client end of the response channel: it runs in the user's
    terminal, so writing to its stdout is what makes the answer visible. With
    a TTY it renders the answer as live markdown with a spinner + footer;
    otherwise it streams plain text (pipes, NO_COLOR, ui.markdown = false).
    """
    # Windows consoles may default to a legacy codepage (cp1252) that cannot
    # encode characters the LLM (or our header) emits; force UTF-8 output.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass  # non-reconfigurable stream (e.g. pipe wrapper) — best effort

    from terminalghost.ui import resolve_color

    cmd = "??" if not text else f"?? {text}"
    payload = json.dumps({"type": "query", "cmd": cmd, "cwd": os.getcwd()}) + "\n"
    use_rich = config.ui.markdown and resolve_color(config.ui.color, sys.stdout)

    try:
        sock = socket.create_connection(
            (config.general.host, _effective_port(config)), timeout=5
        )
    except (ConnectionRefusedError, socket.timeout, OSError):
        _print_unreachable(config)
        return 1

    try:
        with sock:
            sock.sendall(payload.encode("utf-8"))
            # Generous idle timeout: a cold model can take a while to first
            # token, but a wedged daemon shouldn't freeze the prompt forever.
            sock.settimeout(ASK_IDLE_TIMEOUT)
            if use_rich:
                answer = _render_answer_rich(config, sock)
            else:
                answer = _render_answer_plain(sock)
        if copy and answer.strip():
            _copy_to_clipboard(answer.strip(), config)
    except KeyboardInterrupt:
        print()
        return 130
    except socket.timeout:
        from terminalghost.ui import get_console

        get_console(color=config.ui.color, stderr=True).print(
            "[tg.error]TerminalGhost timed out waiting for the daemon.[/]"
        )
        return 1
    except (ConnectionError, OSError):
        _print_unreachable(config)
        return 1
    return 0


def _write_raw(data: bytes) -> None:
    """Write bytes to stdout, tolerating streams without a binary buffer."""
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(data)
        buf.flush()
    else:
        sys.stdout.write(data.decode("utf-8", errors="replace"))
        sys.stdout.flush()


def _cmd_exec(config: Config, argv: list[str]) -> int:
    """Run a command, mirror its output live, and ship the captured output to
    the daemon so a following ?? sees the real error text. Cross-platform."""
    import subprocess

    if not argv:
        print("usage: terminalghost exec <command> [args...]", file=sys.stderr)
        return 2
    cmdline = " ".join(argv)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmdline, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
    except OSError as exc:
        print(f"terminalghost exec: cannot run command: {exc}", file=sys.stderr)
        return 1

    captured = bytearray()
    fd = proc.stdout.fileno()  # type: ignore[union-attr]
    try:
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            _write_raw(chunk)
            captured += chunk
            if len(captured) > EXEC_CAPTURE_BYTES:
                del captured[:-EXEC_CAPTURE_BYTES]
    except KeyboardInterrupt:
        proc.terminate()
    rc = proc.wait()
    duration = int((time.monotonic() - start) * 1000)
    text = captured.decode("utf-8", errors="replace")
    _send_run_event(config, cmdline, rc, duration, text)
    return rc


def _fetch_suggestion(config: Config) -> str:
    """Ask the daemon for the last suggested command (empty string if none)."""
    data = b""
    try:
        with socket.create_connection(
            (config.general.host, _effective_port(config)), timeout=3
        ) as sock:
            sock.sendall((json.dumps({"type": "suggestion"}) + "\n").encode("utf-8"))
            sock.settimeout(5)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def _cmd_apply(config: Config, assume_yes: bool = False) -> int:
    """Run the command TerminalGhost last suggested, after confirmation."""
    from rich.markup import escape

    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color)
    command = _fetch_suggestion(config)
    if not command:
        console.print(
            "[tg.muted]Nothing to apply yet — ask a [tg.key]??[/] first "
            "(works best with [tg.key]?? fix[/]).[/]"
        )
        return 0

    console.print(f"[tg.header]Suggested:[/] [tg.cmd]{escape(command)}[/]")
    if not assume_yes:
        if not sys.stdin.isatty():
            console.print(
                "[tg.warn]Refusing to run without confirmation.[/] "
                "Re-run with [tg.key]--yes[/]."
            )
            return 1
        try:
            answer = input("Run it? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130
        if answer not in ("y", "yes"):
            console.print("[tg.muted]Skipped.[/]")
            return 0
    return _cmd_exec(config, [command])


def _send_run_event(config: Config, cmd: str, exit_code: int, duration_ms: int,
                    output: str) -> None:
    """Fire-and-forget a run-sourced command event (best-effort)."""
    payload = json.dumps({
        "cmd": cmd,
        "exit": exit_code & 0xFF,
        "cwd": os.getcwd(),
        "duration": max(0, duration_ms),
        "ts": time.time(),
        "shell": "exec",
        "source": "run",
        "output": output,
    }) + "\n"
    try:
        with socket.create_connection(
            (config.general.host, _effective_port(config)), timeout=1
        ) as sock:
            sock.sendall(payload.encode("utf-8"))
    except OSError:
        pass  # daemon not running — capture is best-effort, never block the user


def _cmd_hint(config: Config) -> int:
    """Print a one-line proactive hint for the last failure (silent otherwise).

    Runs from the shell prompt when TG_HINTS is enabled, so it must never error
    or hang: any problem (daemon down, slow model, no error) yields no output.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    payload = json.dumps({"type": "hint", "cwd": os.getcwd()}) + "\n"
    data = b""
    try:
        with socket.create_connection(
            (config.general.host, _effective_port(config)), timeout=2
        ) as sock:
            sock.sendall(payload.encode("utf-8"))
            sock.settimeout(HINT_TIMEOUT)
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
    except (OSError, KeyboardInterrupt):
        return 0  # never disrupt the prompt
    text = data.decode("utf-8", errors="replace").strip()
    if not text:
        return 0
    from rich.markup import escape

    from terminalghost.ui import get_console
    from terminalghost.ui.theme import GHOST_GLYPH

    line = text.splitlines()[0][:200]
    console = get_console(color=config.ui.color)
    console.print(f"[tg.muted]{GHOST_GLYPH} hint:[/] [tg.muted]{escape(line)}[/]")
    return 0


def _print_unreachable(config: Config) -> None:
    from terminalghost.ui import get_console

    console = get_console(color=config.ui.color, stderr=True)
    console.print(
        "[tg.error]TerminalGhost daemon is not reachable.[/] "
        "Start it with: [tg.key]terminalghost start[/]"
    )


def _render_answer_plain(sock: socket.socket) -> str:
    """Stream the raw answer to stdout (pipe / no-color path). Returns the text."""
    sys.stdout.write("TerminalGhost ▶\n\n")
    sys.stdout.flush()
    collected: list[str] = []
    for chunk in _iter_socket_text(sock):
        collected.append(chunk)
        sys.stdout.write(chunk)
        sys.stdout.flush()
    sys.stdout.write("\n")
    sys.stdout.flush()
    return "".join(collected)


def _render_answer_rich(config: Config, sock: socket.socket, console=None) -> str:
    """Render the streamed answer as live markdown with a spinner + footer.

    Returns the plain answer text (for --copy)."""
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.spinner import Spinner
    from rich.text import Text

    from terminalghost.ui import get_console
    from terminalghost.ui.theme import GHOST_GLYPH

    if console is None:
        console = get_console(color=config.ui.color)

    header = Text()
    header.append(f"{GHOST_GLYPH} ", style="tg.accent")
    header.append("TerminalGhost", style="tg.brand")
    console.print(header)

    start = time.monotonic()
    buf: list[str] = []
    spinner = Spinner("dots", text=Text(" thinking…", style="tg.muted"))
    with Live(
        spinner,
        console=console,
        refresh_per_second=12,
        vertical_overflow="visible",
    ) as live:
        for chunk in _iter_socket_text(sock):
            buf.append(chunk)
            live.update(Markdown("".join(buf).strip()))
        if not buf:
            live.update(Text("(no response)", style="tg.muted"))

    elapsed = time.monotonic() - start
    footer = Text(
        f"{config.llm.backend} · {_answering_model(config)} · {elapsed:.1f}s",
        style="tg.footer",
    )
    console.print(footer)
    return "".join(buf)


def _copy_to_clipboard(text: str, config: Config) -> None:
    """Best-effort copy to the system clipboard, with a small confirmation."""
    import shutil
    import subprocess

    from terminalghost.ui import get_console

    data = text.encode("utf-8", errors="replace")
    ok = False
    try:
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=data, check=True)
            ok = True
        elif os.name == "nt":
            subprocess.run(["clip"], input=data, check=True)
            ok = True
        else:
            for tool in (
                ["wl-copy"],
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ):
                if shutil.which(tool[0]):
                    subprocess.run(tool, input=data, check=True)
                    ok = True
                    break
    except (OSError, subprocess.CalledProcessError):
        ok = False

    console = get_console(color=config.ui.color)
    if ok:
        console.print("[tg.muted]copied to clipboard[/]")
    else:
        console.print("[tg.muted](could not access a clipboard tool)[/]")


if __name__ == "__main__":
    main()
