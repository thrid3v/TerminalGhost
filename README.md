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

## Installation

### 1. Prerequisites

- **Python 3.11+** — `python --version` to check
- **Git** (to clone the repo)

### 2. Install TerminalGhost

```bash
git clone https://github.com/thrid3v/TerminalGhost.git
cd TerminalGhost

# Linux / macOS
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Windows (PowerShell)
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -e .
```

(Add `".[dev]"` instead of `.` if you want to run the test suite.)

### 3. Install an LLM backend

**Option A — Ollama (default: free, fully local, no API key)**

```bash
# Windows
winget install Ollama.Ollama

# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

Then start the server and pull a model:

```bash
ollama serve          # skip if the Ollama app/service is already running
ollama pull llama3.2  # small + fast (~2 GB); good for trying it out
```

Larger models give better answers — `ollama pull llama3` (~4.7 GB) — just match
the `model` value in your config (step 4).

**Option B — Claude (cloud, needs an Anthropic API key)**

```bash
export ANTHROPIC_API_KEY=sk-ant-...   # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
```

and set `backend = "claude"` in your config (step 4).

### 4. Configure (optional)

Defaults work out of the box with Ollama + `llama3`. To customize:

```bash
mkdir -p ~/.config/terminalghost
cp config.example.toml ~/.config/terminalghost/config.toml
```

Edit `[llm]` `backend`, and `[llm.ollama]` `model` to match what you pulled
(e.g. `llama3.2`).

### 5. Hook up your shell

```bash
# zsh — add to ~/.zshrc:
source /path/to/TerminalGhost/scripts/zsh_hooks.sh

# bash — add to ~/.bashrc:
source /path/to/TerminalGhost/scripts/bash_hooks.sh
```

```powershell
# PowerShell — add to $PROFILE (notepad $PROFILE):
$env:Path = "C:\path\to\TerminalGhost\.venv\Scripts;$env:Path"
. C:\path\to\TerminalGhost\scripts\powershell_hooks.ps1
```

See `scripts/shell_integration.md` for details and troubleshooting.

### 6. Start and use

```bash
terminalghost start

cd /some/project && make build   # → exit 1
??                               # → streamed suggestion from your local LLM
?? give me the exact fix         # inline context works too
```

On PowerShell 7+ use `qq` instead of `??` (which is the null-coalescing operator
there); Windows PowerShell 5.1 supports both. The first `??` after a cold start
takes a few extra seconds while Ollama loads the model into memory.

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

## Configuration reference

Config lives at `~/.config/terminalghost/config.toml` (see `config.example.toml`).
Every key can be overridden with a `TG_SECTION__KEY` environment variable, e.g.
`TG_LLM__BACKEND=claude`, `TG_GENERAL__PORT=49000`, `TG_LLM__OLLAMA__MODEL=mistral`.

For the Claude backend, set `ANTHROPIC_API_KEY` in your environment (preferred
over putting the key in the config file).

## Development

```bash
pip install -e ".[dev]"
pytest                  # run the test suite
ruff check src tests    # lint
```

## Requirements

- Python 3.11+
- Ollama running locally (default) or an Anthropic API key
- Linux / macOS / Windows (the transport is TCP loopback; the optional PTY
  deep-capture layer, when implemented, will be Unix-only)
