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
import re
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from terminalghost.config.loader import Config
    from terminalghost.storage.db import CommandEvent, Database

log = logging.getLogger(__name__)

TREE_ENTRY_CAP = 200
HISTORY_FETCH_LIMIT = 50
# How many recent failures to scan when clustering, and how many same-tool ones
# to actually surface alongside the anchor error.
RELATED_ERROR_SCAN = 12
RELATED_ERROR_CAP = 3
# Hard cap on the auto project-context section so a giant manifest can't
# crowd out command history in the token budget.
PROJECT_CONTEXT_MAX_CHARS = 900
_GIT_TIMEOUT = 1.0  # seconds; a slow repo must not stall the query
_MANIFEST_DEP_CAP = 12  # dependencies listed per manifest

_PREAMBLE = (
    "You are TerminalGhost, an expert engineer living in the user's terminal.\n"
    "Answer only from the shell context below — recent commands, the failing\n"
    "command's output, source excerpts, and project facts. Be concise and\n"
    "concrete.\n"
    "\n"
    "Rules:\n"
    "- Ground every claim in what is actually shown. Never invent file names,\n"
    "  flags, commands, package names, or error messages that do not appear in\n"
    "  the context.\n"
    "- When a command failed: first explain the actual cause in 1-2 plain\n"
    "  sentences (what went wrong and why), then give the exact fix command(s)\n"
    "  in a code block, copy-paste ready.\n"
    "- If several recent failures come from the same tool (shown as earlier\n"
    "  failures), treat them as ONE ongoing problem and fix the root cause, not\n"
    "  each symptom.\n"
    "- If the failing command's output is NOT shown, say so plainly and tell the\n"
    "  user to re-run it with `tgr <command>` so you can see the real error;\n"
    "  give your best guess only after, and mark it as a guess.\n"
    "- Prefer the project's real run tasks (shown in the project facts) over\n"
    "  generic ones, and use any provided source excerpts to point at the exact\n"
    "  line to change."
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
        "\n\nThe user wants the fix only: reply with the corrected command(s) in"
        " a code block first, then at most one short line of why. No preamble."
    ),
    "explain": (
        "\n\nThe user wants to understand: explain the cause clearly using the"
        " shown output and source, then give the command if one applies."
    ),
    "hint": (
        "\n\nRespond with a SINGLE short line (max ~100 chars): the single most"
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
        # Cluster around the error being asked about: pull the earlier failures
        # from the SAME tool (git vs docker vs npm...) so the model sees the
        # thread, not an unrelated failure that just happened to be recent.
        related_errors = self._related_errors(cwd, last_error)
        # newest-first from the DB; the prompt wants oldest-first
        commands = list(reversed(self._db.get_recent_commands(limit=HISTORY_FETCH_LIMIT)))
        tree = self._format_directory_tree(cwd)
        # A repo-local .terminalghost.toml can opt this project out.
        from terminalghost.config.project import load_project_overrides

        overrides = load_project_overrides(cwd)
        project = (
            self._format_project_context(cwd)
            if self._config.context.project_context and not overrides.project_context_off
            else None
        )
        # Read the source around the error's file:line refs so the model debugs
        # real code. Scoped to the failing output the model can actually see.
        source = None
        if self._source_enabled(overrides):
            error_text = "\n".join(
                t for t in (pasted, last_error.output if last_error else None) if t
            )
            if error_text:
                from terminalghost.context.source import collect_source_context

                source = collect_source_context(error_text, cwd)
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
            if related_errors:
                parts.append(self._format_related_errors(last_error, related_errors))
            if source:
                parts.append(source)
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
        # Drop TerminalGhost's own ?? queries — they're noise to the model, not
        # commands the user actually ran.
        commands = [e for e in commands if not _is_trigger_cmd(e.cmd)]
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

    def _related_errors(
        self, cwd: str, anchor: "CommandEvent | None"
    ) -> list["CommandEvent"]:
        """Earlier failures from the same tool as `anchor`, oldest first.

        This is what lets a ?? about a git error ignore the docker errors that
        happened just before it — same-tool failures are the actual thread.
        """
        if anchor is None:
            return []
        tool = _command_tool(anchor.cmd)
        if not tool:
            return []
        recent = self._db.get_recent_errors(cwd=cwd, limit=RELATED_ERROR_SCAN)
        related = [
            e for e in recent
            if e.id != anchor.id and _command_tool(e.cmd) == tool
        ][:RELATED_ERROR_CAP]
        return list(reversed(related))  # oldest → newest for a readable thread

    def _format_related_errors(
        self, anchor: "CommandEvent | None", related: list["CommandEvent"]
    ) -> str:
        tool = _command_tool(anchor.cmd) if anchor else "this tool"
        lines = [
            f"## Earlier `{tool}` failures leading up to this (oldest first)",
            "These are likely the same underlying problem; address the root cause.",
        ]
        for i, e in enumerate(related, start=1):
            lines.append(f"{i}. $ {e.cmd}  (exit {e.exit_code})")
            if e.output:
                for out_line in e.output.splitlines()[:6]:
                    lines.append(f"   {out_line}")
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
        else:
            # Tell the model it's working blind so it asks for output instead of
            # confidently guessing (see the preamble's tgr rule).
            lines.append("output: (not captured — the user has not run this via tgr)")
        return "\n".join(lines)

    def _source_enabled(self, overrides) -> bool:
        """Whether to read error-referenced source into the prompt for this run.

        "off" never; "always" including cloud (source would leave the machine);
        "local" only when the backend is local (Ollama, or a localhost OpenAI-
        compatible server). A repo-local override can always force it off.
        """
        mode = self._config.context.read_source
        if mode == "off" or overrides.read_source_off:
            return False
        if mode == "always":
            return True
        return self._backend_is_local()  # mode == "local"

    def _backend_is_local(self) -> bool:
        llm = self._config.llm
        if llm.backend == "ollama":
            return True
        if llm.backend == "openai":
            url = llm.openai.base_url
            return "://localhost" in url or "://127.0.0.1" in url
        return False  # claude / remote

    def _format_project_context(self, cwd: str) -> str | None:
        """Auto-detected project facts: git state, manifest deps, and a small
        "how to run it" fingerprint (project root + package.json/Makefile tasks).

        Half of "why does this fail" answers hinge on versions/branch state or
        the actual run command the user didn't think to mention. Best-effort and
        hard-capped so it can't crowd out command history.
        """
        from terminalghost.context.source import find_project_root

        lines: list[str] = []
        root = find_project_root(cwd)
        if os.path.abspath(root) != os.path.abspath(cwd):
            lines.append(f"root: {root}")
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
        tasks = _run_tasks(cwd)
        if tasks:
            lines.append(tasks)
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


# -- helpers --------------------------------------------------------------------


def _is_trigger_cmd(cmd: str) -> bool:
    """True for TerminalGhost's own ?? queries (noise in the command history)."""
    return cmd.strip().startswith("??")


# Prefixes that wrap another command — skip them to find the real program.
_CMD_WRAPPERS = {"sudo", "command", "time", "env", "nice", "nohup", "exec",
                 "doas", "xargs", "watch", "strace", "ltrace"}
_ENV_ASSIGN_RE = re.compile(r"^\w+=")


def _command_tool(cmd: str) -> str | None:
    """The program a command runs, normalized — the key for grouping errors.

    `git push --force` → "git"; `sudo docker build .` → "docker";
    `FOO=bar npm run x` → "npm"; `./gradlew build` → "gradlew". None if empty.
    """
    for token in cmd.strip().split():
        if _ENV_ASSIGN_RE.match(token):  # leading VAR=val
            continue
        if token in _CMD_WRAPPERS:
            continue
        # basename, drop path + a trailing .exe/.cmd so "python3" == "python3".
        name = token.replace("\\", "/").split("/")[-1].lower()
        for suffix in (".exe", ".cmd", ".bat", ".ps1"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
        return name or None
    return None


# -- project context helpers ----------------------------------------------------

_TASK_CAP = 8  # run-tasks listed per source
_MAKE_TARGET_RE = re.compile(r"^([A-Za-z0-9][\w.-]*)\s*:(?!=)")


def _run_tasks(cwd: str) -> str | None:
    """A one-line "how to run this" fingerprint: package.json scripts and/or
    Makefile targets, so the model knows the real build/test commands."""
    parts: list[str] = []
    pkg = os.path.join(cwd, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, encoding="utf-8-sig") as fh:
                scripts = json.load(fh).get("scripts")
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            scripts = None
        if isinstance(scripts, dict) and scripts:
            names = list(scripts)[:_TASK_CAP]
            parts.append("npm run: " + ", ".join(names))

    makefile = next(
        (os.path.join(cwd, n) for n in ("Makefile", "makefile", "GNUmakefile")
         if os.path.isfile(os.path.join(cwd, n))), None
    )
    if makefile:
        targets: list[str] = []
        try:
            with open(makefile, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _MAKE_TARGET_RE.match(line)
                    if m and m.group(1) != ".PHONY" and m.group(1) not in targets:
                        targets.append(m.group(1))
                    if len(targets) >= _TASK_CAP:
                        break
        except OSError:
            targets = []
        if targets:
            parts.append("make: " + ", ".join(targets))
    return "tasks — " + "; ".join(parts) if parts else None


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
