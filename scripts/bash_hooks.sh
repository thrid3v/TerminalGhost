#!/usr/bin/env bash
# scripts/bash_hooks.sh — TerminalGhost shell integration for bash.
#
# Source this file in your ~/.bashrc:
#   source /path/to/terminalghost/scripts/bash_hooks.sh
#
# Bash has no native preexec/precmd, so this uses the DEBUG trap (fires
# before each command; $BASH_COMMAND holds the command text) plus
# PROMPT_COMMAND (fires after, where $? is the exit code). A guard variable
# prevents the DEBUG trap from re-arming inside the same prompt cycle, and
# BASH_SUBSHELL filters out subshell invocations.
#
# If you already use bash-preexec (https://github.com/rcaloras/bash-preexec),
# its preexec/precmd hooks are more robust — adapt _tg_preexec/_tg_precmd to
# it; the payload format is identical.
#
# Configuration (export before sourcing):
#   TG_HOST — daemon host, default 127.0.0.1
#   TG_PORT — daemon port, default 48632
#
# Requirements: bash 4.4+, python3 on PATH, a running daemon
# (`terminalghost start`).

export TG_HOST="${TG_HOST:-127.0.0.1}"
export TG_PORT="${TG_PORT:-48632}"

# Send one command event. Args: cmd exit_code cwd duration_ms
_tg_send_event() {
  python3 - "$1" "$2" "$3" "$4" <<'PY' >/dev/null 2>&1 &
import json, os, socket, sys, time

cmd, exit_code, cwd, duration = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
payload = json.dumps({
    "cmd": cmd,
    "exit": max(0, min(255, exit_code)),
    "cwd": cwd,
    "duration": max(0, duration),
    "ts": time.time(),
    "pid": os.getppid(),
    "shell": "bash",
}) + "\n"
try:
    with socket.create_connection(
        (os.environ.get("TG_HOST", "127.0.0.1"), int(os.environ.get("TG_PORT", "48632"))),
        timeout=0.5,
    ) as sock:
        sock.sendall(payload.encode("utf-8"))
except OSError:
    pass  # daemon not running — stay silent
PY
  disown 2>/dev/null
}

_tg_now_ms() {
  if [[ -n "$EPOCHREALTIME" ]]; then          # bash 5+
    local t="${EPOCHREALTIME/./}"
    echo "${t:0:${#t}-3}"
  else
    date +%s%3N 2>/dev/null || echo $(( $(date +%s) * 1000 ))
  fi
}

_tg_preexec() {
  # Skip subshells and re-fires within the same prompt cycle.
  [[ "$BASH_SUBSHELL" != 0 ]] && return
  [[ "$_TG_PREEXEC_DONE" == 1 ]] && return
  # Ignore our own PROMPT_COMMAND machinery.
  [[ "$BASH_COMMAND" == _tg_precmd* ]] && return
  _TG_CMD="$BASH_COMMAND"
  _TG_START="$(_tg_now_ms)"
  _TG_PREEXEC_DONE=1
}

_tg_precmd() {
  local exit_code=$?
  _TG_PREEXEC_DONE=0
  [[ -z "$_TG_CMD" ]] && return
  local duration=$(( $(_tg_now_ms) - ${_TG_START:-$(_tg_now_ms)} ))
  _tg_send_event "$_TG_CMD" "$exit_code" "$PWD" "$duration"
  unset _TG_CMD _TG_START
}

trap '_tg_preexec' DEBUG
PROMPT_COMMAND="_tg_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"

# `??` — query TerminalGhost via the ask client (streams the answer here).
# Note: bash expands ?? as a glob first; if a two-character file exists in
# the cwd it wins. `tg` is provided as an unambiguous fallback.
function ?? { terminalghost ask "$@"; }
function tg { terminalghost ask "$@"; }
