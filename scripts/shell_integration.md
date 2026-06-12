# Shell Integration Guide

This document explains how the shell hook scripts work and how to set them up.

## How capture works

```
User types command
       │
       ▼
  zsh preexec / bash DEBUG trap / PowerShell prompt wrapper
  records: cmd text, start timestamp
       │
       ▼
  Command runs in shell
       │
       ▼
  zsh precmd / bash PROMPT_COMMAND / PowerShell prompt
  records: exit code, duration, cwd
  sends one JSON line to the daemon over TCP loopback (127.0.0.1:48632)
       │
       ▼
  terminalghost daemon
  (HookReceiver listening on the TCP port)
       │
       ▼
  stored in SQLite rolling buffer
```

Event sends are fire-and-forget and backgrounded — the shell prompt is never
delayed, and nothing breaks when the daemon is stopped.

## How `??` works (request/response)

`??` is different from event capture: the answer has to come back to *your*
terminal. The `??` shell function runs `terminalghost ask`, a small client
that connects to the daemon, sends the query, and stays connected while the
daemon streams the LLM answer back over the same TCP connection:

```
?? why does make fail
   │  terminalghost ask → {"type": "query", "cmd": "?? why does make fail", ...}
   ▼
daemon: context assembly → LLM → streams answer chunks back over the socket
   │
   ▼
ask prints each chunk to your terminal as it arrives
```

## Quick setup

### zsh
```zsh
# Add to ~/.zshrc:
source /path/to/terminalghost/scripts/zsh_hooks.sh
```

### bash
```bash
# Native DEBUG trap (no extra dependency):
source /path/to/terminalghost/scripts/bash_hooks.sh

# If you already use bash-preexec (https://github.com/rcaloras/bash-preexec),
# its hooks are more robust — see the comments in bash_hooks.sh.
```

### PowerShell (Windows)
```powershell
# Add to your $PROFILE:
. C:\path\to\terminalghost\scripts\powershell_hooks.ps1
```
Note: in PowerShell 7+ the literal `??` collides with the null-coalescing
operator, so use `qq` there. Windows PowerShell 5.1 gets both `??` and `qq`.

## The ?? trigger

After sourcing the hooks, `??` is available at the prompt:

```
$ make build
...error output...
$ ??
TerminalGhost ▶
  The error is in Makefile line 12: the 'cc' variable is undefined.
  Fix: add CC=gcc at the top of your Makefile, or run:
    make CC=gcc build
```

You can also add context:
```
$ ?? why does this fail on Apple Silicon
```

## Requirements

- `python3` on PATH (zsh/bash hooks use it for robust JSON escaping and the
  TCP send — no socat/nc dependency). PowerShell uses .NET directly.
- The `terminalghost` daemon must be running: `terminalghost start`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TG_HOST` | `127.0.0.1` | Daemon host the hooks send to |
| `TG_PORT` | `48632` | Daemon port the hooks send to |

These must match `[general] host`/`port` in the daemon's config.toml
(overridable there via `TG_GENERAL__HOST` / `TG_GENERAL__PORT`).

## Troubleshooting

**`??` does nothing / "daemon is not reachable"**
- Check the daemon is running: `terminalghost status`
- Check the port matches between the hooks (`TG_PORT`) and the daemon config
- Check daemon logs: `~/.local/share/terminalghost/daemon.log`

**Commands not appearing in context**
- Verify the hooks are sourced: `type _tg_precmd` (zsh/bash) — should print a
  function definition
- Check daemon logs for "invalid command event" warnings
