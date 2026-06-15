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
import time
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
        # Last (monotonic_ts, question, answer) for short conversational follow-ups.
        self._last_exchange: tuple[float, str, str] | None = None
        # Last (monotonic_ts, command) extracted from an answer, for `apply`.
        self._last_suggestion: tuple[float, str] | None = None

    _INTENTS = ("fix", "explain")
    _MAX_STORED_ANSWER = 2000
    _SUGGESTION_TTL = 600.0  # how long a suggested command stays applyable

    _FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
    _INLINE_RE = re.compile(r"`([^`\n]+)`")

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
        """Run the pipeline if `cmd` is a trigger; no-op otherwise.

        Decoration (the header/footer) is a presentation concern: when streaming
        over a socket (`send` given), the `ask` client renders its own header so
        only the raw answer text goes over the wire. The foreground/dev path
        (`send is None`) has no client, so a plaintext header is emitted here.
        """
        if not self.is_trigger(cmd):
            return
        decorate = send is None
        emit = send or _stdout_send
        raw_inline = self.extract_inline_context(cmd)
        intent, inline_context = self._split_intent(raw_inline)
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
            prior = self._recent_exchange()
            prompt = self._assembler.assemble(
                cwd, inline_context, intent=intent, prior_exchange=prior
            )
            if decorate:
                await emit(HEADER)
            collected: list[str] = []

            async def collecting_emit(text: str) -> None:
                collected.append(text)
                await emit(text)

            try:
                await self._stream_to(
                    self._backend.stream_query(prompt), collecting_emit
                )
                if decorate:
                    await emit("\n")
                answer = "".join(collected)
                self._record_exchange(raw_inline or "??", answer)
                self._record_suggestion(answer)
            except LLMError as exc:
                await emit(f"\nTerminalGhost error: {exc}\n")

    async def hint(self, cwd: str, send: SendFn) -> None:
        """Best-effort one-line proactive hint for a recent failure in `cwd`.

        Stays completely silent (emits nothing) when there's no recent error
        here or the backend is down — it runs in the user's prompt, so it must
        never error or block noticeably.
        """
        if self._db is None or self._db.get_last_error(cwd=cwd) is None:
            return
        loop = asyncio.get_running_loop()
        available = await loop.run_in_executor(None, self._backend.is_available)
        if not available:
            return
        prompt = self._assembler.assemble(cwd, intent="hint")
        async with self._lock:
            try:
                await self._stream_to(self._backend.stream_query(prompt), send)
            except LLMError:
                pass  # hints are best-effort; never disrupt the prompt

    # -- conversational follow-ups ---------------------------------------------

    def _split_intent(self, text: str) -> tuple[str, str]:
        """Pull a leading `fix`/`explain` keyword off the inline context."""
        if not text:
            return "default", ""
        first, _, rest = text.partition(" ")
        if first.lower() in self._INTENTS:
            return first.lower(), rest.strip()
        return "default", text

    def _recent_exchange(self) -> tuple[str, str] | None:
        if self._last_exchange is None:
            return None
        window = self._config.llm.followup_seconds
        if window <= 0:
            return None
        ts, question, answer = self._last_exchange
        if time.monotonic() - ts > window:
            return None
        return question, answer

    def _record_exchange(self, question: str, answer: str) -> None:
        answer = answer.strip()
        if not answer:
            return
        self._last_exchange = (
            time.monotonic(),
            question,
            answer[: self._MAX_STORED_ANSWER],
        )

    # -- apply-the-fix ---------------------------------------------------------

    @classmethod
    def _extract_command(cls, answer: str) -> str | None:
        """Pull the first runnable command out of an answer.

        Prefers a fenced code block (the first non-comment line), falling back
        to the first inline-code span. Returns None if neither is present.
        """
        fence = cls._FENCE_RE.search(answer)
        if fence:
            for line in fence.group(1).splitlines():
                stripped = line.strip().lstrip("$").strip()
                if stripped and not stripped.startswith("#"):
                    return stripped
        inline = cls._INLINE_RE.search(answer)
        if inline:
            return inline.group(1).strip()
        return None

    def _record_suggestion(self, answer: str) -> None:
        command = self._extract_command(answer)
        if command:
            self._last_suggestion = (time.monotonic(), command)

    def last_suggestion(self) -> str | None:
        """The most recent suggested command, if still fresh."""
        if self._last_suggestion is None:
            return None
        ts, command = self._last_suggestion
        if time.monotonic() - ts > self._SUGGESTION_TTL:
            return None
        return command

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
