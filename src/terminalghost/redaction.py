# terminalghost.redaction
#
# Secret redaction, shared by every layer that ships text off the machine:
# the daemon (command text + captured output before storage) and the context
# assembler (source snippets before they reach the LLM). Kept dependency-free
# so low-level modules can reuse it without importing the daemon.

from __future__ import annotations

import re

# Secret-bearing patterns, redacted so they never reach the database or an LLM.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:PASSWORD|PASSWD|TOKEN|SECRET|API[_-]?KEY)[A-Z0-9_]*)=(\S+)"
)
_SECRET_FLAG_RE = re.compile(
    r"(?i)(--?(?:password|passwd|pass|token|secret|api[_-]?key)[=\s])(\S+)"
)
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{8,})")
_URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.\-]*://[^:@/\s]+):([^@/\s]+)@")
# Well-known token literals (GitHub, OpenAI/Anthropic, Slack, AWS access keys).
_TOKEN_LITERAL_RE = re.compile(
    r"\b("
    r"gh[opsu]_[A-Za-z0-9]{20,}"
    r"|sk-(?:ant-)?[A-Za-z0-9_\-]{16,}"
    r"|xox[baprs]-[A-Za-z0-9\-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r")\b"
)

_SENSITIVE_LINE_RE = re.compile(
    r"(?i)\b(password|passphrase|token|secret|api[-_]?key)\b"
)


def redact_secrets(text: str) -> str:
    """Redact obvious secrets (assignments, flags, bearer tokens, URL creds,
    well-known key literals) anywhere in `text`."""
    text = _SECRET_ASSIGN_RE.sub(r"\1=<redacted>", text)
    text = _SECRET_FLAG_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    text = _TOKEN_LITERAL_RE.sub("<redacted>", text)
    text = _URL_CRED_RE.sub(r"\1:<redacted>@", text)
    return text


def redact_command(cmd: str) -> str:
    """Redact secrets in a single command line."""
    return redact_secrets(cmd)


def redact_output(output: str) -> str:
    """Mask secret-looking value lines, then redact token literals everywhere."""
    redacted = []
    suppress_next = False
    for line in output.splitlines():
        if suppress_next:
            redacted.append("<redacted>")
            suppress_next = False
        elif _SENSITIVE_LINE_RE.search(line):
            redacted.append("<redacted>")
            suppress_next = True  # the following line is often the echoed value
        else:
            redacted.append(line)
    return redact_secrets("\n".join(redacted))
