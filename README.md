# TerminalGhost

A local-first, terminal-resident AI assistant. It lives as a persistent background
process, watches your shell commands, and answers `??` with a contextual suggestion.

## How it works

1. **Capture** — a shell hook (zsh `preexec`/`precmd`, bash `PROMPT_COMMAND`, or a
   PowerShell `prompt` wrapper) sends each command + exit code + timing to the
   daemon over a TCP loopback socket (fire-and-forget; the prompt never blocks).

2. **Store** — the daemon writes every event into a rolling SQLite buffer (last ~200
   commands).

3. **Context assembly** — when `??` fires, the context module reads the rolling
   buffer, the current directory tree, and the most recent non-zero exit, and
   assembles a structured prompt.

4. **LLM query** — `??` runs the `terminalghost ask` client, which sends the query
   to the daemon and stays connected; the daemon sends the prompt to the configured
   backend (Ollama by default; Claude as the cloud alternative) and streams the
   response back over the same connection into your terminal.

## Quick start

```bash
# 1. Create a venv and install
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2. (Optional) copy and edit config — defaults work out of the box
mkdir -p ~/.config/terminalghost
cp config.example.toml ~/.config/terminalghost/config.toml

# 3. Source the shell integration
echo 'source /path/to/terminalghost/scripts/zsh_hooks.sh' >> ~/.zshrc    # zsh
echo 'source /path/to/terminalghost/scripts/bash_hooks.sh' >> ~/.bashrc  # bash
# PowerShell: add to $PROFILE:
#   . C:\path\to\terminalghost\scripts\powershell_hooks.ps1

# 4. Start the daemon
terminalghost start

# 5. Use it
cd /some/project && make build   # → exit 1
??                               # → streamed suggestion from your local LLM
```

See `scripts/shell_integration.md` for details and troubleshooting.

## CLI

```
terminalghost start     # start the daemon in the background
terminalghost stop      # stop it
terminalghost status    # running / stopped
terminalghost restart
terminalghost run       # run in the foreground (debugging)
terminalghost ask [..]  # send a ?? query directly (what the ?? function calls)
```

## Module overview

| Module | Responsibility |
|---|---|
| `capture` | TCP hook receiver (+ planned PTY interposition, Unix-only) |
| `storage` | SQLite rolling buffer; schema; CRUD |
| `context` | Assembles the LLM prompt from buffer + tree + last error |
| `llm` | Model-agnostic backend interface + Ollama + Claude |
| `trigger` | Detects `??`, orchestrates context→LLM→streamed output |
| `daemon` | Background process, socket server, PID management, CLI |
| `config` | TOML loader, frozen Config dataclasses, TG_* env overrides |

## Configuration

Config lives at `~/.config/terminalghost/config.toml` (see `config.example.toml`).
Every key can be overridden with a `TG_SECTION__KEY` environment variable, e.g.
`TG_LLM__BACKEND=claude`, `TG_GENERAL__PORT=49000`, `TG_LLM__OLLAMA__MODEL=mistral`.

For the Claude backend, set `ANTHROPIC_API_KEY` in your environment (preferred
over putting the key in the config file).

## Requirements

- Python 3.11+
- Ollama running locally (default) or an Anthropic API key
- Linux / macOS / Windows (the transport is TCP loopback; the optional PTY
  deep-capture layer, when implemented, will be Unix-only)
