# TerminalGhost

A local-first, terminal-resident AI assistant. It lives as a persistent background
process, watches your shell commands, and answers `??` with a contextual suggestion.

## How it works

1. **Capture** — a shell hook (zsh `preexec`/`precmd` or bash `PROMPT_COMMAND`) sends
   each command + exit code + truncated output to the daemon over a Unix socket.
   Optionally a PTY wrapper captures output at a lower level.

2. **Store** — the daemon writes every event into a rolling SQLite buffer (last ~200
   commands).

3. **Context assembly** — when `??` fires, the context module reads the rolling
   buffer, the current directory tree, and the most recent non-zero exit, and
   assembles a structured prompt.

4. **LLM query** — the assembled prompt is sent to the configured backend (Ollama by
   default; Claude or other cloud APIs as alternatives) and the streamed response is
   printed to the terminal.

## Quick start

```bash
# 1. Create a venv and install
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 2. Copy and edit config
mkdir -p ~/.config/terminalghost
cp config.example.toml ~/.config/terminalghost/config.toml

# 3. Source the shell integration
echo 'source /path/to/terminalghost/scripts/zsh_hooks.sh' >> ~/.zshrc

# 4. Start the daemon
terminalghost start

# 5. Use it
cd /some/project && make build   # → exit 1
??                               # → streamed suggestion from your local LLM
```

## Module overview

| Module | Responsibility |
|---|---|
| `capture` | PTY interposition + shell hook receiver |
| `storage` | SQLite rolling buffer; schema; CRUD |
| `context` | Assembles the LLM prompt from buffer + tree + last error |
| `llm` | Model-agnostic backend interface + Ollama + Claude stubs |
| `trigger` | Detects `??`, orchestrates context→LLM→output |
| `daemon` | Background process, socket server, PID/signal management |
| `config` | TOML loader, Config dataclass, validation |

## Requirements

- Python 3.11+
- Ollama running locally (default) or an Anthropic API key
- Linux / macOS (the PTY and socket layers are Unix-only)
