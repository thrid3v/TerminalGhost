# terminalghost.trigger.handler
#
# Detects the `??` trigger and orchestrates the full query pipeline:
# trigger detection → context assembly → LLM query → streamed output.
#
# DESIGN NOTE (response channel): the daemon's stdout is detached from the
# user's terminal, so output is delivered through an async `send` callback
# supplied by the caller. For `??` queries arriving over the socket, the
# HookReceiver passes a send() that writes back to the connected client
# (the `terminalghost ask` process running in the user's terminal). When no
# send is given (e.g. foreground/dev mode), output goes to this process's
# stdout.

from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable

from terminalghost.llm.base import LLMError

if TYPE_CHECKING:
    from terminalghost.config.loader import Config
    from terminalghost.context.assembler import ContextAssembler
    from terminalghost.llm.base import LLMBackend
    from terminalghost.storage.db import Database

log = logging.getLogger(__name__)

SendFn = Callable[[str], Awaitable[None]]

TRIGGER_RE = re.compile(r"^\?\?(\s+.*)?$", re.DOTALL)

HEADER = "TerminalGhost ▶\n\n"


async def _stdout_send(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


class TriggerHandler:
    """Runs the query pipeline when a `??` command is detected."""

    def __init__(
        self,
        db: "Database",
        assembler: "ContextAssembler",
        backend: "LLMBackend",
        config: "Config",
    ) -> None:
        self._db = db
        self._assembler = assembler
        self._backend = backend
        self._config = config
        # Serializes pipeline runs so two rapid ?? don't interleave output.
        self._lock = asyncio.Lock()

    def set_backend(self, backend: "LLMBackend") -> None:
        """Swap the LLM backend (used by SIGHUP config hot-reload)."""
        self._backend = backend

    # -- trigger parsing -------------------------------------------------------

    def is_trigger(self, cmd: str) -> bool:
        """True for "??" or "?? <text>"; False for "echo ??", "??x", etc."""
        return bool(TRIGGER_RE.match(cmd.strip()))

    def extract_inline_context(self, cmd: str) -> str:
        """Extract the optional free text after "??" (stripped)."""
        if not self._config.llm.allow_inline_context:
            return ""
        stripped = cmd.strip()
        if not stripped.startswith("??"):
            return ""
        return stripped[2:].strip()

    # -- pipeline ----------------------------------------------------------------

    async def handle(self, cmd: str, cwd: str, send: SendFn | None = None) -> None:
        """Run the pipeline if `cmd` is a trigger; no-op otherwise."""
        if not self.is_trigger(cmd):
            return
        emit = send or _stdout_send
        inline_context = self.extract_inline_context(cmd)
        async with self._lock:
            loop = asyncio.get_running_loop()
            # is_available() may block on a short HTTP probe — keep it off the loop.
            available = await loop.run_in_executor(None, self._backend.is_available)
            if not available:
                await emit(
                    "TerminalGhost: the LLM backend is not available."
                    " Check that it is running/configured (see config.toml).\n"
                )
                return
            prompt = self._assembler.assemble(cwd, inline_context)
            await emit(HEADER)
            try:
                await self._stream_to(self._backend.stream_query(prompt), emit)
                await emit("\n")
            except LLMError as exc:
                await emit(f"\nTerminalGhost error: {exc}\n")

    async def _stream_to(self, stream: AsyncIterator[str], emit: SendFn) -> None:
        """Consume the LLM stream, leaving the output clean on interrupt."""
        try:
            async for chunk in stream:
                await emit(chunk)
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl-C mid-stream: terminate the line, no traceback.
            try:
                await emit("\n[interrupted]\n")
            except Exception:  # noqa: BLE001 — peer may already be gone
                pass
