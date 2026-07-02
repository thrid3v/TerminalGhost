# terminalghost.context.assembler
#
# Assembles the prompt sent to the LLM when the user types `??`:
#   1. Rolling command history (storage.Database)
#   2. Current directory tree (filesystem walk)
#   3. Most recent error command (Database.get_last_error)
# Everything is fitted into config.context.token_budget by trimming the
# oldest history entries first, then the tree.

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminalghost.config.loader import Config
    from terminalghost.storage.db import CommandEvent, Database

log = logging.getLogger(__name__)

TREE_ENTRY_CAP = 200
HISTORY_FETCH_LIMIT = 50
# Hard cap on the auto project-context section so a giant manifest can't
# crowd out command history in the token budget.
PROJECT_CONTEXT_MAX_CHARS = 900
_GIT_TIMEOUT = 1.0  # seconds; a slow repo must not stall the query
_MANIFEST_DEP_CAP = 12  # dependencies listed per manifest

_PREAMBLE = (
    "You are TerminalGhost, an assistant that lives in the user's terminal.\n"
    "Using the shell context below, give a concise, actionable answer.\n"
    "If a command failed, explain why and give the exact fix command first."
)

_RECAP_PREAMBLE = (
    "You are TerminalGhost. Summarize this terminal session from the command\n"
    "log below: what was attempted, what failed, and what fixed it. Reply with\n"
    "a short bullet list grouped by task, then one line of key takeaways —\n"
    "useful for a standup note or a PR description. Do not invent commands\n"
    "that are not in the log."
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
        project = (
            self._format_project_context(cwd)
            if self._config.context.project_context
            else None
        )
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
            if project:
                parts.append(project)
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

    def assemble_recap(self, since: float) -> str | None:
        """Prompt asking for a session summary of commands newer than `since`.

        Global (not cwd-scoped): a terminal session usually spans directories.
        Returns None when nothing was captured in the window.
        """
        budget = self._config.context.token_budget
        commands = list(reversed(self._db.get_recent_commands(
            limit=self._config.general.history_size, since=since,
        )))
        if not commands:
            return None

        def build(cmds: list) -> str:
            return _RECAP_PREAMBLE + "\n\n" + self._format_command_history(cmds, None)

        prompt = build(commands)
        # Trim oldest first — the end of the session matters most.
        while self._estimate_tokens(prompt) > budget and len(commands) > 1:
            commands.pop(0)
            prompt = build(commands)
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

    def _format_project_context(self, cwd: str) -> str | None:
        """Auto-detected project facts: git state + manifest snippets.

        Half of "why does this fail" answers hinge on versions/branch state
        the user didn't think to mention. Everything here is best-effort and
        hard-capped so it can't crowd out command history.
        """
        lines: list[str] = []
        git = _git_summary(cwd)
        if git:
            lines.append(git)
        for filename, formatter in _MANIFESTS:
            path = os.path.join(cwd, filename)
            if not os.path.isfile(path):
                continue
            try:
                snippet = formatter(path)
            except Exception:  # noqa: BLE001 — malformed manifests are not our problem
                snippet = None
            if snippet:
                lines.append(f"{filename}: {snippet}")
        if not lines:
            return None
        text = "## Project\n" + "\n".join(lines)
        return text[:PROJECT_CONTEXT_MAX_CHARS]

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


# -- project context helpers ----------------------------------------------------


def _git_summary(cwd: str) -> str | None:
    """One line of git state ("git: branch main, 3 changed files"), or None."""

    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", cwd, *args],
                capture_output=True, text=True, timeout=_GIT_TIMEOUT,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None  # not a repo / git missing — silently skip
    # -uno skips untracked scanning: much faster on big repos, and tracked
    # modifications are what usually matters for "why does this fail".
    status = run("status", "--porcelain", "-uno")
    dirty = len(status.splitlines()) if status else 0
    state = f"{dirty} changed file(s)" if dirty else "clean"
    return f"git: branch {branch}, {state}"


def _snippet_package_json(path: str) -> str | None:
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        return None
    parts = []
    if isinstance(data.get("engines"), dict):
        parts.append("engines " + json.dumps(data["engines"]))
    deps = data.get("dependencies")
    if isinstance(deps, dict) and deps:
        listed = [f"{k}@{v}" for k, v in list(deps.items())[:_MANIFEST_DEP_CAP]]
        more = f" (+{len(deps) - _MANIFEST_DEP_CAP} more)" if len(deps) > _MANIFEST_DEP_CAP else ""
        parts.append("deps " + ", ".join(listed) + more)
    return "; ".join(parts) or None


def _snippet_requirements(path: str) -> str | None:
    with open(path, encoding="utf-8-sig") as fh:
        reqs = [
            line.strip() for line in fh
            if line.strip() and not line.lstrip().startswith(("#", "-"))
        ]
    if not reqs:
        return None
    more = f" (+{len(reqs) - _MANIFEST_DEP_CAP} more)" if len(reqs) > _MANIFEST_DEP_CAP else ""
    return ", ".join(reqs[:_MANIFEST_DEP_CAP]) + more


def _snippet_pyproject(path: str) -> str | None:
    import tomllib

    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    parts = []
    if project.get("requires-python"):
        parts.append(f"requires-python {project['requires-python']}")
    deps = project.get("dependencies")
    if isinstance(deps, list) and deps:
        listed = [str(d) for d in deps[:_MANIFEST_DEP_CAP]]
        more = f" (+{len(deps) - _MANIFEST_DEP_CAP} more)" if len(deps) > _MANIFEST_DEP_CAP else ""
        parts.append("deps " + ", ".join(listed) + more)
    return "; ".join(parts) or None


def _snippet_cargo(path: str) -> str | None:
    import tomllib

    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    deps = data.get("dependencies")
    if not isinstance(deps, dict) or not deps:
        return None
    names = list(deps)[:_MANIFEST_DEP_CAP]
    more = f" (+{len(deps) - _MANIFEST_DEP_CAP} more)" if len(deps) > _MANIFEST_DEP_CAP else ""
    return "deps " + ", ".join(names) + more


def _snippet_go_mod(path: str) -> str | None:
    with open(path, encoding="utf-8-sig") as fh:
        lines = [line.strip() for line in fh if line.strip()]
    module = next((ln for ln in lines if ln.startswith("module ")), None)
    go_ver = next((ln for ln in lines if ln.startswith("go ")), None)
    parts = [p for p in (module, go_ver) if p]
    return "; ".join(parts) or None


# Checked in order; each formatter returns a one-line snippet or None.
_MANIFESTS: tuple[tuple[str, object], ...] = (
    ("package.json", _snippet_package_json),
    ("pyproject.toml", _snippet_pyproject),
    ("requirements.txt", _snippet_requirements),
    ("Cargo.toml", _snippet_cargo),
    ("go.mod", _snippet_go_mod),
)
