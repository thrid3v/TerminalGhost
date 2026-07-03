# terminalghost.context.source
#
# Error-driven source context: the "debug from the real code, not blindly"
# piece. Given the text of a failing command's output, find the file:line
# references a stack trace / compiler error names, and pull a small window of
# the actual source around each one.
#
# Guardrails (this reads your files and can ship them to an LLM):
#   - only files INSIDE the project root are read (never an absolute path the
#     output happens to mention, like /etc/passwd);
#   - .gitignore'd files and obvious secret files (.env, *.pem, id_rsa, ...)
#     are skipped;
#   - every snippet is run through secret redaction before it leaves;
#   - hard caps on files, lines, and total characters keep the token budget.
# Whether this runs at all (and for which backends) is decided by the caller.

from __future__ import annotations

import logging
import os
import re
import subprocess

from terminalghost.redaction import redact_output

log = logging.getLogger(__name__)

# Window of context lines on each side of a referenced line.
WINDOW = 6
MAX_FILES = 4          # distinct file:line snippets included
MAX_CHARS = 1500       # hard cap on the whole "## Relevant source" section
MAX_FILE_BYTES = 512 * 1024
_GIT_TIMEOUT = 1.0

# Root markers, in priority order, when there's no .git.
_ROOT_MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml",
                 "go.mod", "Makefile", ".hg", ".svn")

# Python traceback frames: File "path", line N  (paths may contain spaces).
_PY_REF_RE = re.compile(r'File "([^"]+)", line (\d+)')
# Generic path.ext:line[:col] — Node, gcc/clang, rust (-->), go, jest, java, ...
_GENERIC_REF_RE = re.compile(
    r"([A-Za-z0-9_./\\+-]+\.[A-Za-z][A-Za-z0-9]{0,7}):(\d+)(?::\d+)?"
)

# Files we never read, even if referenced (secrets, private keys, creds).
_SECRET_FILE_RE = re.compile(
    r"(?:^|[/\\])(?:"
    r"\.env(?:\.[\w.-]+)?|\.envrc"
    r"|[\w.-]*\.pem|[\w.-]*\.key|[\w.-]*\.p12|[\w.-]*\.pfx"
    r"|id_rsa|id_dsa|id_ecdsa|id_ed25519"
    r"|credentials|secrets?\.(?:ya?ml|json|toml|ini)"
    r")$",
    re.IGNORECASE,
)


def find_project_root(cwd: str) -> str:
    """Nearest ancestor holding a project marker (.git, package.json, ...).

    Falls back to `cwd` itself when nothing is found.
    """
    current = os.path.abspath(cwd)
    for _ in range(40):  # generous walk-up bound
        for marker in _ROOT_MARKERS:
            if os.path.exists(os.path.join(current, marker)):
                return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return os.path.abspath(cwd)


def extract_file_refs(text: str) -> list[tuple[str, int]]:
    """Return (path, line) references found in `text`, in first-seen order.

    Deduplicated on (path, line); does not touch the filesystem.
    """
    refs: list[tuple[str, int]] = []
    seen: set[tuple[str, int]] = set()

    def add(path: str, line_s: str) -> None:
        path = path.strip()
        try:
            line = int(line_s)
        except ValueError:
            return
        if not path or line <= 0:
            return
        key = (path, line)
        if key not in seen:
            seen.add(key)
            refs.append(key)

    for m in _PY_REF_RE.finditer(text):
        add(m.group(1), m.group(2))
    for m in _GENERIC_REF_RE.finditer(text):
        add(m.group(1), m.group(2))
    return refs


def _within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:  # different drives on Windows
        return False


def _resolve(path: str, cwd: str, root: str) -> str | None:
    """Resolve a referenced path to a real file inside `root`, or None."""
    candidates = [path] if os.path.isabs(path) else [
        os.path.join(cwd, path), os.path.join(root, path)
    ]
    for cand in candidates:
        try:
            real = os.path.realpath(cand)
        except OSError:
            continue
        if os.path.isfile(real) and _within(real, os.path.realpath(root)):
            return real
    return None


def _gitignored(relpaths: list[str], root: str) -> set[str]:
    """Subset of `relpaths` ignored by git (empty if git/repo unavailable)."""
    if not relpaths:
        return set()
    try:
        result = subprocess.run(
            ["git", "-C", root, "check-ignore", "--stdin"],
            input="\n".join(relpaths), capture_output=True, text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return set()
    # Exit 0 = some ignored, 1 = none ignored, 128 = not a repo. Any stdout is
    # the ignored subset.
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _read_window(real: str, line: int) -> str | None:
    """The source around `line` (1-based) with a marker, or None if unreadable."""
    try:
        if os.path.getsize(real) > MAX_FILE_BYTES:
            return None
        with open(real, "rb") as fh:
            head = fh.read(1024)
            if b"\x00" in head:  # binary
                return None
            rest = fh.read()
        text = (head + rest).decode("utf-8", errors="replace")
    except OSError:
        return None
    lines = text.splitlines()
    if not lines:
        return None
    lo = max(0, line - 1 - WINDOW)
    hi = min(len(lines), line + WINDOW)
    width = len(str(hi))
    out = []
    for i in range(lo, hi):
        n = i + 1
        marker = ">" if n == line else " "
        out.append(f"{marker} {n:>{width}} | {lines[i]}")
    return redact_output("\n".join(out))


def collect_source_context(text: str, cwd: str, *, redact: bool = True) -> str | None:
    """Build a '## Relevant source' section from files the error text names.

    Returns None when nothing relevant/readable is found. `redact` is accepted
    for symmetry; snippets are always redacted (a source file may hold a key).
    """
    root = find_project_root(cwd)
    root_real = os.path.realpath(root)
    refs = extract_file_refs(text)
    if not refs:
        return None

    # Resolve first, so we only ask git about real in-root files.
    resolved: list[tuple[str, int, str]] = []  # (real, line, relpath)
    for path, line in refs:
        real = _resolve(path, cwd, root)
        if real is None:
            continue
        rel = os.path.relpath(real, root_real).replace("\\", "/")
        if _SECRET_FILE_RE.search(rel):
            continue
        resolved.append((real, line, rel))
        if len(resolved) >= MAX_FILES * 2:  # gather a few extra; git may drop some
            break
    if not resolved:
        return None

    ignored = _gitignored([r for _, _, r in resolved], root_real)

    blocks: list[str] = []
    used = 0
    for real, line, rel in resolved:
        if rel in ignored:
            continue
        snippet = _read_window(real, line)
        if not snippet:
            continue
        block = f"### {rel}:{line}\n{snippet}"
        if used + len(block) > MAX_CHARS and blocks:
            break
        blocks.append(block)
        used += len(block)
        if len(blocks) >= MAX_FILES:
            break
    if not blocks:
        return None
    return "## Relevant source (files the error points at)\n" + "\n\n".join(blocks)
