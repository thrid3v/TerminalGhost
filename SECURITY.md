# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories
("Report a vulnerability" on the repo's Security tab), or by email to
offlthridev@gmail.com. Please do not open a public issue for vulnerabilities.
We aim to acknowledge within a few days.

## Threat model

TerminalGhost is a **local-first** tool. Its trust boundary is your machine.

- **Transport.** The daemon listens on a **TCP loopback** socket
  (`127.0.0.1`, default port 48632). It will **refuse to bind a non-loopback
  address** unless you explicitly set `TG_ALLOW_REMOTE=1`, so it is not exposed
  to the network by default.
- **Local trust.** The loopback socket is **not authenticated**. Any process
  running as a user who can reach loopback on your machine can send command
  events, trigger an LLM query, or read the last suggested command. On a
  single-user machine this is equivalent to that user's existing shell access.
  **On shared/multi-user hosts, treat the daemon as readable by other local
  users** (a per-user auth token is planned — see "Hardening" below).
- **Secrets.** Command text and any captured output are scanned and redacted
  before storage (assignments like `TOKEN=…`, `--password …`, `Authorization:
  Bearer …`, credentials in URLs, and well-known key formats such as
  `ghp_…`, `sk-…`, `xox…`, `AKIA…`). `capture.blocked_commands` prevents output
  of matching commands from being stored at all. Redaction is best-effort, not
  a guarantee — avoid pasting raw secrets into commands you expect to keep.
- **Cloud backends.** With `backend = "claude"` or `"openai"`, the assembled
  context (recent commands, directory tree, last error, captured output) is
  sent to that provider. The default backend (Ollama) is fully local. API keys
  are read from environment variables in preference to the config file and are
  never logged.
- **`apply` / `tga`.** Running a suggested command executes it in your shell.
  TerminalGhost shows the command and asks for confirmation first; `--yes`
  skips that prompt, so only use it when you trust the source of the
  suggestion. A suggestion is derived from the model's answer to a query, which
  in turn can be influenced by command/context text — review before running.

## Hardening on shared machines

- Keep `general.host` on a loopback address (the default).
- Set restrictive permissions on `~/.config/terminalghost/` and
  `~/.local/share/terminalghost/` if other users share the host.
- Prefer the local Ollama backend so no shell context leaves the machine.

## Planned

- A per-user authentication token for the loopback socket, so other local
  users on a shared host cannot read from or post to your daemon.
