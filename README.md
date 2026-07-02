# TerminalGhost

```
 .-.    TerminalGhost
(o o)   ask your terminal anything — just type ??
|=#=|   local-first · ollama · v0.2.0
 '-'
```

A local-first, terminal-resident AI assistant. A small background daemon watches your
shell commands; when you type `??` at the prompt it assembles context (recent commands,
the directory tree, the last error) and streams an answer back into your terminal.

- **Local-first.** Defaults to [Ollama](https://ollama.com) — fully offline, no API key,
  nothing leaves your machine. Claude and any OpenAI-compatible API are optional.
- **Ambient.** Lives in the shell you already use; it doesn't take the terminal over.
- **Cross-platform.** Linux, macOS, and Windows.

## Install

```bash
pipx install terminalghost      # or: uv tool install terminalghost
terminalghost init              # interactive setup — does everything below for you
```

`terminalghost init` detects your shell, helps you pick/verify an LLM, writes a config,
installs the shell hook, and offers to start the daemon. Then restart your shell and:

```
$ make build        # → exits non-zero
$ ??                # → streamed, contextual fix
$ ?? fix            # → just the corrected command
$ ?? explain the error in detail
```

> Prefer Claude? `pipx install "terminalghost[cloud]"` and choose Claude in `init`
> (or set `ANTHROPIC_API_KEY`).

If anything misbehaves, run **`terminalghost doctor`** — it checks the daemon, the port,
the LLM, your shell hooks, and the data dir, and prints the exact fix for each.

## Using `??`

| You type | What happens |
|---|---|
| `??` | Answer about your most recent failure / recent commands in this directory |
| `?? <text>` | Same, plus your note (e.g. `?? why does this fail on Apple Silicon`) |
| `?? fix <text>` | Reply with the corrected command first, minimal prose |
| `?? explain <text>` | A thorough explanation |
| follow-up `??` | A `??` within ~5 min remembers the previous answer for follow-ups |

On **PowerShell 7+**, `??` is the null-coalescing operator — use `qq` there (Windows
PowerShell 5.1 gets both). In **bash**, `??` can glob; `tg` is an unambiguous alias.

Add `-c`/`--copy` (e.g. `tg --copy fix`) to copy the answer to your clipboard.

## Capture output & apply fixes

By default the model sees your command and exit code but not its output. To give
it the real error text on **any platform**, run the command through the capture
wrapper:

```
tgr make build      # runs it normally, mirrors output, captures it for the next qq
qq                  # now reasons from the actual error, not just the command
```

When the answer suggests a command, run it without retyping:

```
tga                 # shows the suggested command, asks y/N, then runs it (also captured)
```

`tgr` = `terminalghost exec`, `tga` = `terminalghost apply`. The applied command
runs through the same capture path, so you can immediately `qq` again if it fails.
(For zero-effort ambient capture on Linux/macOS, see the experimental
`TG_CAPTURE_OUTPUT` option below.)

**Destructive suggestions are gated.** If the suggested command looks dangerous
(`rm -rf`, `git push --force`, `git reset --hard`, `dd`, piping a download into a
shell, `DROP TABLE`, …), TerminalGhost shows a plain-language warning of what it
would do and requires you to type `yes` — a single keypress or `y` won't run it.

## Commands

```
terminalghost init        # interactive first-time setup
terminalghost doctor      # diagnose the install; print fixes
terminalghost start       # start the daemon in the background
terminalghost stop        # stop it
terminalghost status      # running / stopped
terminalghost restart
terminalghost log [-n N]  # show recently captured commands
                          #   filter: --failed, --cwd [DIR], --since 2h, --grep <text>
terminalghost clear       # forget captured history (--last N for just the newest N)
terminalghost export      # dump history as JSON (pipe or -o FILE) to move machines
terminalghost import <f>  # load an exported snapshot into this machine's history
terminalghost redact-check <cmd>  # preview what would be stored — nothing is saved
terminalghost enable      # start the daemon automatically at login
terminalghost disable     # undo enable
terminalghost uninstall   # remove the shell hook block from your profile
terminalghost exec <cmd>  # run a command, capturing its output for ?? (alias: tgr)
terminalghost apply       # run the command ?? last suggested (alias: tga)
terminalghost dashboard   # full-screen view: status, recent commands, output
terminalghost explain     # explain piped output / a file: make 2>&1 | tg explain
terminalghost cheatsheet  # everything you can do, at a glance
terminalghost use <b>     # switch LLM: use ollama:mistral | use claude | use openai
terminalghost theme <t>   # dark | light | high-contrast
terminalghost --version
```

(`ask`, `hint`, `hook-path`, `run` also exist; they're what the shell integration and
service files call.)

When you `qq`, the answer renders in a card and — if there's a fix — shows an inline
**[R]un / [C]opy / [E]dit** bar so you can apply it in one keystroke. `terminalghost
dashboard` opens a full-screen view of what's been captured.

## LLM backends

- **Ollama (default).** Install from [ollama.com](https://ollama.com), then
  `ollama pull llama3` (or any model — set `[llm.ollama] model` to match). Free, private,
  offline.
- **Claude.** `pipx install "terminalghost[cloud]"`, set `ANTHROPIC_API_KEY`, and
  `backend = "claude"`.
- **OpenAI-compatible.** Set `backend = "openai"`. Point `[llm.openai] base_url` at OpenAI,
  Groq, LM Studio, llama.cpp, or Ollama's `/v1` endpoint. Local servers need no key.

## Configuration

Config lives at `~/.config/terminalghost/config.toml` (see
[`config.example.toml`](src/terminalghost/_assets/config.example.toml) for every option).
Any key can be overridden by an env var of the form `TG_SECTION__KEY` (double underscore),
e.g. `TG_LLM__BACKEND=claude`, `TG_LLM__OLLAMA__MODEL=mistral`, `TG_UI__COLOR=never`.

Highlights:
- `[ui] color` = `auto | always | never`, `theme` = `dark | light | high-contrast`
  (or `terminalghost theme <name>`), `markdown` = render answers as live markdown.
- `[general] port = 0` picks a free port automatically.
- `[llm] followup_seconds` controls the conversational follow-up window.

## Output capture (experimental, opt-in, POSIX)

By default TerminalGhost sees your commands and exit codes but **not** their output.
To let it read the actual error text, set `capture.capture_output = true` and export
`TG_CAPTURE_OUTPUT=1` before the hooks load. The bash/zsh hooks then run your shell inside
`script` to transcribe output. It's off by default and POSIX-only (Windows needs ConPTY).

## Proactive hints (experimental, opt-in)

Export `TG_HINTS=1` and the hooks will print a one-line suggestion after a failed command,
without you asking. It blocks briefly for the model; it stays silent if nothing's useful.

## Privacy

Everything is local by default. Command text is scanned for obvious secrets
(`TOKEN=…`, `--password …`, credentials in URLs) and redacted before storage;
`capture.blocked_commands` never has its output stored. Switching to a cloud backend
sends assembled context to that provider — your choice, off by default.

To make TerminalGhost forget what it captured, run **`terminalghost clear`** (everything)
or `terminalghost clear --last N` (just the last N commands — handy right after typing
a secret). The database is vacuumed so deleted text actually leaves the file.

Don't take redaction on faith — check it: **`terminalghost redact-check "export TOKEN=abc123"`**
shows exactly what would be stored for any command, without saving anything. Rows where
redaction fired are highlighted in `terminalghost log`, and history snapshots
(`export`/`import`) only ever contain the already-redacted text.

## Development

```bash
git clone https://github.com/thrid3v/TerminalGhost.git
cd TerminalGhost
python -m venv .venv && source .venv/bin/activate    # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev,cloud]"

pytest                  # run the test suite
ruff check src tests    # lint
```

## How it works

```
shell hook ──TCP──> daemon ──> SQLite rolling buffer
   (cmd, exit, cwd, duration; output if enabled)

?? ─> `terminalghost ask` ──TCP──> daemon: assemble context ─> LLM ─> stream answer back
```

| Module | Responsibility |
|---|---|
| `capture` | TCP hook receiver (events + `??`/hint queries) |
| `storage` | SQLite rolling buffer; schema; CRUD |
| `context` | Assembles the LLM prompt from buffer + tree + last error |
| `llm` | Backend interface + Ollama + Claude + OpenAI-compatible |
| `trigger` | Detects `??`, runs context→LLM→streamed output |
| `daemon` | Background process, socket server, PID/port files, CLI |
| `cli` | `init` / `doctor` / `log` / autostart / profile management |
| `ui` | Rich theme, banner, streamed-answer rendering |
| `config` | TOML loader, frozen Config dataclasses, `TG_*` overrides |

## License

MIT — see [LICENSE](LICENSE).
