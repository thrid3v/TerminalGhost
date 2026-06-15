# terminalghost.context.assembler
#
# Assembles the prompt sent to the LLM when the user types `??`:
#   1. Rolling command history (storage.Database)
#   2. Current directory tree (filesystem walk)
#   3. Most recent error command (Database.get_last_error)
# Everything is fitted into config.context.token_budget by trimming the
# oldest history entries first, then the tree.

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminalghost.config.loader import Config
    from terminalghost.storage.db import CommandEvent, Database

log = logging.getLogger(__name__)

TREE_ENTRY_CAP = 200
HISTORY_FETCH_LIMIT = 50

_PREAMBLE = (
    "You are TerminalGhost, an assistant that lives in the user's terminal.\n"
    "Using the shell context below, give a concise, actionable answer.\n"
    "If a command failed, explain why and give the exact fix command first."
)

# Extra instruction appended to the preamble for `?? fix` / `?? explain`.
_INTENT_SUFFIX = {
    "fix": (
        "\nThe user wants the fix only: respond with the corrected command(s)"
        " first, then at most one short line explaining why."
    ),
    "explain": (
        "\nThe user wants to understand: explain clearly and thoroughly, then"
        " give the command if relevant."
    ),
    "hint": (
        "\nRespond with a SINGLE short line (max ~100 chars): the single most"
        " likely fix as a command or a terse tip. No preamble, no markdown."
    ),
}


class ContextAssembler:
    """Builds an LLM prompt from shell context."""

    def __init__(self, db: "Database", config: "Config") -> None:
        self._db = db
        self._config = config

    def assemble(
        self,
        cwd: str,
        inline_context: str = "",
        *,
        intent: str = "default",
        prior_exchange: tuple[str, str] | None = None,
        pasted: str | None = None,
    ) -> str:
        """Build and return the complete prompt string within token_budget.

        `intent` ("fix" | "explain" | "default") tunes the preamble.
        `prior_exchange` is the (question, answer) from a recent ?? so the user
        can ask follow-ups. `pasted` is arbitrary output the user piped in to
        explain (the `explain` command).
        """
        budget = self._config.context.token_budget
        # Scope the surfaced error to this directory so a ?? doesn't pick up an
        # unrelated failure from another terminal/project.
        last_error = self._db.get_last_error(cwd=cwd)
        # newest-first from the DB; the prompt wants oldest-first
        commands = list(reversed(self._db.get_recent_commands(limit=HISTORY_FETCH_LIMIT)))
        tree = self._format_directory_tree(cwd)
        preamble = _PREAMBLE + _INTENT_SUFFIX.get(intent, "")

        def build(cmds: list, tree_text: str) -> str:
            parts = [preamble]
            if pasted:
                parts.append(f"## Output to explain\n{pasted}")
            if prior_exchange is not None:
                q, a = prior_exchange
                parts.append(f"## Earlier in this conversation\nQ: {q}\nA: {a}")
            if last_error is not None:
                parts.append(self._format_last_error(last_error))
            if cmds:
                parts.append(self._format_command_history(cmds, last_error))
            parts.append(f"## Current directory: {cwd}\n{tree_text}")
            if inline_context:
                parts.append(f"## User note\n{inline_context}")
            return "\n\n".join(parts)

        prompt = build(commands, tree)
        # Trim oldest commands first until within budget.
        while self._estimate_tokens(prompt) > budget and commands:
            commands.pop(0)
            prompt = build(commands, tree)
        # Still over (huge tree): drop tree lines from the bottom.
        tree_lines = tree.splitlines()
        while self._estimate_tokens(prompt) > budget and len(tree_lines) > 1:
            tree_lines = tree_lines[: len(tree_lines) // 2]
            prompt = build(commands, "\n".join(tree_lines) + "\n(truncated)")
        return prompt

    # -- formatting helpers ----------------------------------------------------

    def _format_command_history(
        self, commands: list["CommandEvent"], last_error: "CommandEvent | None"
    ) -> str:
        lines = ["## Recent commands (oldest first)"]
        error_id = last_error.id if last_error is not None else None
        for i, event in enumerate(commands, start=1):
            marker = "  ← FAILED" if event.id is not None and event.id == error_id else ""
            lines.append(
                f"{i}. [cwd: {event.cwd}] $ {event.cmd}"
                f"  (exit {event.exit_code}, {event.duration_ms}ms){marker}"
            )
            if event.output:
                for out_line in event.output.splitlines()[:10]:
                    lines.append(f"   output: {out_line}")
        return "\n".join(lines)

    def _format_last_error(self, event: "CommandEvent") -> str:
        lines = [
            "## Most recent FAILING command",
            f"$ {event.cmd}",
            f"exit code: {event.exit_code}  (cwd: {event.cwd})",
        ]
        if event.output:
            lines.append("output:")
            lines.extend(event.output.splitlines()[:20])
        return "\n".join(lines)

    def _format_directory_tree(self, cwd: str) -> str:
        """Tree-style listing of cwd, depth/ignore/entry-cap limited."""
        if not os.path.isdir(cwd):
            return "(directory not found)"
        max_depth = self._config.context.tree_depth
        ignore = set(self._config.context.tree_ignore)
        lines: list[str] = []
        count = 0
        truncated = False

        def walk(path: str, depth: int) -> None:
            nonlocal count, truncated
            if depth > max_depth or truncated:
                return
            try:
                entries = sorted(
                    os.scandir(path), key=lambda e: (not e.is_dir(follow_symlinks=False), e.name)
                )
            except OSError:
                return
            for entry in entries:
                if count >= TREE_ENTRY_CAP:
                    truncated = True
                    return
                is_dir = entry.is_dir(follow_symlinks=False)
                if is_dir and entry.name in ignore:
                    continue
                lines.append("  " * depth + entry.name + ("/" if is_dir else ""))
                count += 1
                if is_dir:
                    walk(entry.path, depth + 1)

        walk(cwd, 0)
        if truncated:
            lines.append("(truncated)")
        return "\n".join(lines) if lines else "(empty directory)"

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough estimate without a tokenizer: ~4 chars per token."""
        return len(text) // 4
