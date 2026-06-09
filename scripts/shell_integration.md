# Shell Integration Guide

This document explains how the shell hook scripts work and how to set them up.

## How capture works

```
User types command
       │
       ▼
  zsh preexec / bash DEBUG trap
  records: cmd text, start timestamp
       │
       ▼
  Command runs in shell
       │
       ▼
  zsh precmd / bash PROMPT_COMMAND
  records: exit code, duration, cwd
  sends JSON payload to Unix socket
       │
       ▼
  terminalghost daemon
  (HookReceiver listening on socket)
       │
       ▼
  stored in SQLite rolling buffer
```

## Quick setup

### zsh
```zsh
# Add to ~/.zshrc:
source /path/to/terminalghost/scripts/zsh_hooks.sh
```

### bash
```bash
# Option 1 — native DEBUG trap (no extra dependency):
source /path/to/terminalghost/scripts/bash_hooks.sh

# Option 2 — bash-preexec (recommended):
# 1. Install: curl https://raw.githubusercontent.com/rcaloras/bash-preexec/master/bash-preexec.sh -o ~/.bash-preexec.sh
# 2. Add to ~/.bashrc:
source ~/.bash-preexec.sh
source /path/to/terminalghost/scripts/bash_hooks.sh
```

## The ?? trigger

After sourcing the hooks, `??` is available as a shell function:

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

- `socat` — used to write to the Unix socket  
  Install: `brew install socat` (macOS) or `apt install socat` (Ubuntu)
- The `terminalghost` daemon must be running:  
  `terminalghost start`

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TG_SOCKET` | `/tmp/terminalghost.sock` | Override daemon socket path |

## Troubleshooting

**"??" does nothing**
- Check daemon is running: `terminalghost status`
- Check socat is installed: `which socat`
- Check socket exists: `ls -la /tmp/terminalghost.sock`

**Commands not appearing in context**
- Verify the hooks are sourced: `type _tg_precmd` (should print function definition)
- Check daemon logs: `~/.local/share/terminalghost/daemon.log`
