# terminalghost.capture.shell_hooks
#
# Receives structured command-event data from the shell hook scripts over a
# TCP loopback socket (cross-platform replacement for the original Unix
# domain socket design). This is the "lightweight capture" path.
#
# Protocol (newline-terminated JSON, one object per line):
#
#   Command event (fire-and-forget; connection may close immediately after):
#     {"cmd": "make build", "exit": 1, "cwd": "/home/u/p",
#      "duration": 4231, "ts": 1718000000.123,
#      "pid": 12345, "shell": "zsh"}          # pid/shell optional
#
#   Query (request/response; `terminalghost ask` stays connected and the
#   daemon streams the answer back as raw UTF-8 text until it closes):
#     {"type": "query", "cmd": "?? why did make fail", "cwd": "/home/u/p"}
#
# An object without "type" (or with "type": "command") is a command event.

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Awaitable, Callable

from terminalghost.storage.db import CommandEvent

log = logging.getLogger(__name__)

MAX_CMD_CHARS = 4096
_READ_LIMIT = 256 * 1024  # max bytes per JSON line

# on_event(event, shell_pid, shell) — persist a command event
OnEvent = Callable[[CommandEvent, int | None, str | None], Awaitable[None]]
# on_query(cmd, cwd, send) — run the ?? pipeline, writing chunks via send
OnQuery = Callable[[str, str, Callable[[str], Awaitable[None]]], Awaitable[None]]
# on_hint(cwd, send) — stream a one-line proactive hint (or nothing)
OnHint = Callable[[str, Callable[[str], Awaitable[None]]], Awaitable[None]]
# on_suggestion(send) — write back the last suggested command (or nothing)
OnSuggestion = Callable[[Callable[[str], Awaitable[None]]], Awaitable[None]]


class HookReceiver:
    """Async TCP server receiving events/queries from shell integrations."""

    def __init__(
        self,
        host: str,
        port: int,
        on_event: OnEvent,
        on_query: OnQuery | None = None,
        on_hint: OnHint | None = None,
        on_suggestion: OnSuggestion | None = None,
        auth_token: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._on_event = on_event
        self._on_query = on_query
        self._on_hint = on_hint
        self._on_suggestion = on_suggestion
        # When set, every payload must carry a matching "token"; this stops other
        # local users on a shared host from reading/posting to the daemon.
        self._auth_token = auth_token
        self._server: asyncio.Server | None = None

    def _token_ok(self, got) -> bool:
        """True if auth is disabled (no token) or `got` matches the token."""
        if not self._auth_token:
            return True
        return isinstance(got, str) and hmac.compare_digest(got, self._auth_token)

    @property
    def port(self) -> int:
        """The actually bound port (useful when constructed with port=0)."""
        if self._server is None:
            return self._port
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        """Bind and start accepting connections."""
        self._server = await asyncio.start_server(
            self._handle_connection, self._host, self._port, limit=_READ_LIMIT
        )
        log.info("hook receiver listening on %s:%d", self._host, self.port)

    async def stop(self) -> None:
        """Stop accepting connections and close the server. Idempotent."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None

    # -- connection handling -----------------------------------------------------

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read newline-delimited JSON lines from one client until EOF.

        Malformed packets are logged and skipped, never fatal. A connection
        closed mid-line simply discards the partial buffer (readline returns
        the partial data without a trailing newline; we ignore it).
        """
        try:
            while True:
                try:
                    line = await reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    log.warning("oversized payload line; dropping connection")
                    break
                if not line:
                    break  # EOF
                if not line.endswith(b"\n") and reader.at_eof():
                    # partial line then disconnect — discard
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue  # keep-alive / empty line
                try:
                    obj = json.loads(text)
                except json.JSONDecodeError:
                    log.warning("malformed JSON payload: %.120s", text)
                    continue
                if not isinstance(obj, dict):
                    log.warning("payload is not a JSON object: %.120s", text)
                    continue
                if not self._token_ok(obj.get("token")):
                    log.warning("rejecting payload with missing/invalid auth token")
                    break

                if obj.get("type") == "query":
                    await self._handle_query(obj, writer)
                    break  # one query per connection; close after responding
                if obj.get("type") == "hint":
                    await self._handle_hint(obj, writer)
                    break
                if obj.get("type") == "suggestion":
                    await self._handle_suggestion(writer)
                    break
                try:
                    event, shell_pid, shell = self._parse_payload(text)
                except ValueError as exc:
                    log.warning("invalid command event (%s): %.120s", exc, text)
                    continue
                await self._on_event(event, shell_pid, shell)
        except ConnectionError:
            pass  # client vanished; nothing to do
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _handle_query(self, obj: dict, writer: asyncio.StreamWriter) -> None:
        """Run the ?? pipeline, streaming the answer back to this client."""
        cmd = obj.get("cmd")
        cwd = obj.get("cwd")
        if not isinstance(cmd, str) or not cmd or not isinstance(cwd, str) or not cwd:
            log.warning("invalid query payload: %r", obj)
            return
        if self._on_query is None:
            return

        async def send(text: str) -> None:
            writer.write(text.encode("utf-8", errors="replace"))
            await writer.drain()

        try:
            await self._on_query(cmd[:MAX_CMD_CHARS], cwd, send)
        except ConnectionError:
            log.info("query client disconnected mid-response")

    async def _handle_hint(self, obj: dict, writer: asyncio.StreamWriter) -> None:
        """Stream a one-line proactive hint back to the client (or nothing)."""
        cwd = obj.get("cwd")
        if not isinstance(cwd, str) or not cwd or self._on_hint is None:
            return

        async def send(text: str) -> None:
            writer.write(text.encode("utf-8", errors="replace"))
            await writer.drain()

        try:
            await self._on_hint(cwd, send)
        except ConnectionError:
            log.info("hint client disconnected mid-response")

    async def _handle_suggestion(self, writer: asyncio.StreamWriter) -> None:
        """Write back the last suggested command (empty if none)."""
        if self._on_suggestion is None:
            return

        async def send(text: str) -> None:
            writer.write(text.encode("utf-8", errors="replace"))
            await writer.drain()

        try:
            await self._on_suggestion(send)
        except ConnectionError:
            log.info("suggestion client disconnected")

    # -- payload parsing -----------------------------------------------------------

    def _parse_payload(self, raw: str) -> tuple[CommandEvent, int | None, str | None]:
        """Parse and validate one JSON line into a CommandEvent.

        Returns (event, shell_pid, shell); session_id is 0 — the daemon
        resolves the real session from shell_pid. Raises ValueError on any
        missing/mistyped field.
        """
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("payload must be a JSON object")

        cmd = obj.get("cmd")
        if not isinstance(cmd, str) or not cmd:
            raise ValueError("cmd must be a non-empty string")
        cmd = cmd[:MAX_CMD_CHARS]

        exit_code = obj.get("exit")
        if isinstance(exit_code, bool) or not isinstance(exit_code, int):
            raise ValueError("exit must be an integer")
        if not 0 <= exit_code <= 255:
            raise ValueError("exit must be in [0, 255]")

        cwd = obj.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("cwd must be a non-empty string")

        duration = obj.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            raise ValueError("duration must be a non-negative integer (ms)")

        ts = obj.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or ts <= 0:
            raise ValueError("ts must be a positive number")

        shell_pid = obj.get("pid")
        if shell_pid is not None and (
            isinstance(shell_pid, bool) or not isinstance(shell_pid, int)
        ):
            raise ValueError("pid must be an integer when present")

        shell = obj.get("shell")
        if shell is not None and not isinstance(shell, str):
            raise ValueError("shell must be a string when present")

        output = obj.get("output")
        if output is not None and not isinstance(output, str):
            raise ValueError("output must be a string when present")

        source = obj.get("source")
        if source is not None and not isinstance(source, str):
            raise ValueError("source must be a string when present")

        event = CommandEvent(
            session_id=0,  # resolved by the daemon from shell_pid
            ts=float(ts),
            cwd=cwd,
            cmd=cmd,
            exit_code=exit_code,
            duration_ms=duration,
            output=output,
            source=source,
        )
        return event, shell_pid, shell
