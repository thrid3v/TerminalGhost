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
        # Last (monotonic_ts, commands) plan extracted from an answer, for `apply`.
        self._last_suggestion: tuple[float, list[str]] | None = None

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

    async def handle(
        self, cmd: str, cwd: str, send: SendFn | None = None, pasted: str | None = None
    ) -> None:
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
        # Bare `qq` with nothing to work with → a helpful tip, not a vague answer.
        if not raw_inline and self._is_empty_context(cwd):
            if decorate:
                await emit(HEADER)
            await emit(self._EMPTY_TIP)
            return
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
            # Debugging a failure the daemon never saw the output of? The model
            # is guessing — nudge the user to capture it (only when they're not
            # feeding their own output via `explain`).
            blind_cmd = None if pasted else self._blind_failure(cwd)
            prompt = self._assembler.assemble(
                cwd, inline_context, intent=intent, prior_exchange=prior, pasted=pasted
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
                # Extract the suggestion BEFORE the nudge, so the nudge's own
                # `tgr ...` is never mistaken for the command to apply.
                self._record_suggestion(answer)
                if blind_cmd:
                    await emit(self._blind_nudge(blind_cmd))
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

    async def recap(self, since: float, send: SendFn) -> None:
        """Stream a session summary of commands captured after `since`."""
        prompt = self._assembler.assemble_recap(since)
        if prompt is None:
            await send("Nothing captured in that window yet — run some commands first.\n")
            return
        loop = asyncio.get_running_loop()
        available = await loop.run_in_executor(None, self._backend.is_available)
        if not available:
            await send(
                "TerminalGhost: the LLM backend is not available."
                " Check that it is running/configured (see config.toml).\n"
            )
            return
        async with self._lock:
            try:
                await self._stream_to(self._backend.stream_query(prompt), send)
            except LLMError as exc:
                await send(f"\nTerminalGhost error: {exc}\n")

    # -- conversational follow-ups ---------------------------------------------

    _EMPTY_TIP = (
        "**Nothing to fix here yet.** Run something first, then ask. Meanwhile:\n\n"
        "- `qq <question>` — ask anything, with your shell context\n"
        "- `qq fix` — get the fix for your last error\n"
        "- `tgr <cmd>` — run a command so I can see its output\n"
        "- `terminalghost dashboard` — see what I've captured\n"
    )

    def _split_intent(self, text: str) -> tuple[str, str]:
        """Pull a leading `fix`/`explain` keyword off the inline context."""
        if not text:
            return "default", ""
        first, _, rest = text.partition(" ")
        if first.lower() in self._INTENTS:
            return first.lower(), rest.strip()
        return "default", text

    def _is_empty_context(self, cwd: str) -> bool:
        """True when there's no error here and no real (non-??) command to discuss."""
        if self._db is None:
            return False
        if self._db.get_last_error(cwd=cwd) is not None:
            return False
        recent = self._db.get_recent_commands(limit=10)
        return not any(not e.cmd.strip().startswith("??") for e in recent)

    def _blind_failure(self, cwd: str) -> str | None:
        """The failing command here whose output was never captured, if any.

        That's the case where the model is guessing — we can point the user at
        `tgr` to fix it. Returns the command text, or None.
        """
        if self._db is None:
            return None
        err = self._db.get_last_error(cwd=cwd)
        if err is None or err.output or err.cmd.strip().startswith("??"):
            return None
        return err.cmd

    @staticmethod
    def _blind_nudge(cmd: str) -> str:
        """A gentle, visually-distinct note that output wasn't captured."""
        one_line = cmd.strip().splitlines()[0][:80]
        return (
            "\n\n> 👻 I couldn't see that command's output, so this is a best "
            f"guess. Run `tgr {one_line}` and ask again for a precise fix."
        )

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

    _MAX_STEPS = 10  # cap on extracted plan steps

    @classmethod
    def _extract_commands(cls, answer: str) -> list[str]:
        """Pull the runnable command(s) out of an answer, in order.

        Real fixes are often several commands; the first fenced code block is
        treated as a plan (every non-comment line a step, capped). Falls back
        to "$ command" prose lines, then to the first inline-code span.
        """
        fence = cls._FENCE_RE.search(answer)
        if fence:
            commands: list[str] = []
            for line in fence.group(1).splitlines():
                stripped = line.strip()
                # Strip only a "$ " prompt marker — a bare leading "$" may be
                # part of the command itself (e.g. PowerShell `$env:X = ...`).
                if stripped.startswith("$ "):
                    stripped = stripped[2:].strip()
                if stripped and not stripped.startswith("#"):
                    commands.append(stripped)
                if len(commands) >= cls._MAX_STEPS:
                    break
            if commands:
                return commands
        # "$ command" prompt lines in prose (common when the model skips fences).
        prose = []
        for line in answer.splitlines():
            stripped = line.strip()
            if stripped.startswith("$ "):
                prose.append(stripped[2:].strip())
            if len(prose) >= cls._MAX_STEPS:
                break
        if prose:
            return prose
        inline = cls._INLINE_RE.search(answer)
        if inline:
            return [inline.group(1).strip()]
        return []

    @classmethod
    def _extract_command(cls, answer: str) -> str | None:
        """The first runnable command in an answer (None if there is none)."""
        commands = cls._extract_commands(answer)
        return commands[0] if commands else None

    def _record_suggestion(self, answer: str) -> None:
        commands = self._extract_commands(answer)
        if commands:
            self._last_suggestion = (time.monotonic(), commands)

    def last_suggestions(self) -> list[str]:
        """The most recent suggested plan (possibly one step), if still fresh."""
        if self._last_suggestion is None:
            return []
        ts, commands = self._last_suggestion
        if time.monotonic() - ts > self._SUGGESTION_TTL:
            return []
        return list(commands) if isinstance(commands, list) else [commands]

    def last_suggestion(self) -> str | None:
        """The first command of the most recent suggestion (back-compat)."""
        commands = self.last_suggestions()
        return commands[0] if commands else None

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
