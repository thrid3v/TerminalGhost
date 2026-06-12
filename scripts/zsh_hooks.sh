#!/usr/bin/env zsh
# scripts/zsh_hooks.sh — TerminalGhost shell integration for zsh.
#
# Source this file in your ~/.zshrc:
#   source /path/to/terminalghost/scripts/zsh_hooks.sh
#
# What this installs:
#   1. preexec hook — records the command text + start time before it runs.
#   2. precmd hook  — after the command, sends {cmd, exit, cwd, duration, ts}
#      to the daemon over TCP loopback (fire-and-forget, backgrounded so the
#      prompt is never delayed; silently a no-op when the daemon is down).
#   3. `??` function — runs `terminalghost ask`, which sends the query to the
#      daemon and streams the LLM answer back to this terminal.
#
# Configuration (export before sourcing):
#   TG_HOST — daemon host, default 127.0.0.1
#   TG_PORT — daemon port, default 48632
#
# Requirements: python3 on PATH (used for robust JSON escaping + the TCP
# send; avoids a socat/nc dependency), and a running daemon
# (`terminalghost start`).

export TG_HOST="${TG_HOST:-127.0.0.1}"
export TG_PORT="${TG_PORT:-48632}"

zmodload zsh/datetime 2>/dev/null
autoload -Uz add-zsh-hook

# Send one command event to the daemon. Args: cmd exit_code cwd duration_ms
# Runs python3 in a disowned background job so the shell never blocks.
_tg_send_event() {
  python3 - "$1" "$2" "$3" "$4" <<'PY' >/dev/null 2>&1 &!
import json, os, socket, sys, time

cmd, exit_code, cwd, duration = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
payload = json.dumps({
    "cmd": cmd,
    "exit": max(0, min(255, exit_code)),
    "cwd": cwd,
    "duration": max(0, duration),
    "ts": time.time(),
    "pid": os.getppid(),
    "shell": "zsh",
}) + "\n"
try:
    with socket.create_connection(
        (os.environ.get("TG_HOST", "127.0.0.1"), int(os.environ.get("TG_PORT", "48632"))),
        timeout=0.5,
    ) as sock:
        sock.sendall(payload.encode("utf-8"))
except OSError:
    pass  # daemon not running — never bother the shell about it
PY
}

_tg_preexec() {
  _TG_CMD="$1"
  _TG_START="$EPOCHREALTIME"
}

_tg_precmd() {
  local exit_code=$?
  [[ -z "$_TG_CMD" ]] && return
  local duration=0
  if [[ -n "$_TG_START" && -n "$EPOCHREALTIME" ]]; then
    duration=$(( (EPOCHREALTIME - _TG_START) * 1000 ))
    duration=${duration%.*}
  fi
  _tg_send_event "$_TG_CMD" "$exit_code" "$PWD" "$duration"
  unset _TG_CMD _TG_START
}

add-zsh-hook preexec _tg_preexec
add-zsh-hook precmd _tg_precmd

# `??` (optionally followed by extra context) — query TerminalGhost.
# The ask client stays connected and streams the answer to this terminal.
'??'() {
  terminalghost ask "$@"
}
