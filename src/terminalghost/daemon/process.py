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
            )
            await receiver.start()

            shutdown = asyncio.Event()
            self._install_signal_handlers(asyncio.get_running_loop(), shutdown)
            log.info("daemon running (pid %d)", os.getpid())
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

    # -- event wiring ------------------------------------------------------------

    async def _on_event(
        self, event: CommandEvent, shell_pid: int | None, shell: str | None
    ) -> None:
        """Persist a command event (with capture-policy filtering applied)."""
        assert self._db is not None
        event.session_id = self._session_for(shell_pid, shell)
        if self._is_blocked(event.cmd):
            event.output = None  # never store output of blocked commands
        if self._config.capture.redact_passwords and event.output:
            event.output = _redact_output(event.output)
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


# -- CLI ------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="terminalghost",
        description="Local-first terminal AI assistant daemon",
    )
    parser.add_argument("--config", help="explicit config file path", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="start the daemon in the background")
    sub.add_parser("stop", help="stop the running daemon")
    sub.add_parser("status", help="show whether the daemon is running")
    sub.add_parser("restart", help="stop then start the daemon")
    sub.add_parser("run", help="run the daemon in the foreground (used by start)")
    ask = sub.add_parser("ask", help="send a ?? query to the daemon and print the answer")
    ask.add_argument("text", nargs="*", help="optional extra context after ??")
    args = parser.parse_args()

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
        sys.exit(_cmd_ask(config, " ".join(args.text)))


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
    print(f"daemon started (pid {proc.pid}), listening on "
          f"{config.general.host}:{config.general.port}")
    return 0


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


def _cmd_ask(config: Config, text: str) -> int:
    """Send a ?? query to the daemon and stream the answer to this terminal.

    This is the client end of the response channel: it runs in the user's
    terminal, so writing to its stdout is what makes the answer visible.
    """
    cmd = "??" if not text else f"?? {text}"
    payload = json.dumps({"type": "query", "cmd": cmd, "cwd": os.getcwd()}) + "\n"
    try:
        with socket.create_connection(
            (config.general.host, config.general.port), timeout=5
        ) as sock:
            sock.sendall(payload.encode("utf-8"))
            sock.settimeout(None)  # the LLM may take a while; block on reads
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                sys.stdout.write(data.decode("utf-8", errors="replace"))
                sys.stdout.flush()
    except (ConnectionRefusedError, socket.timeout, OSError):
        print(
            "TerminalGhost daemon is not reachable. Start it with: terminalghost start",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print()
        return 130
    return 0


if __name__ == "__main__":
    main()
