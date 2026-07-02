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
#   TG_PORT — daemon port. When unset, the port the daemon recorded in its
#             runtime file (~/.local/share/terminalghost/port) is used, so a
#             custom or ephemeral (port = 0) config just works; else 48632.
#
# Requirements: bash 4.4+, python3 on PATH, a running daemon
# (`terminalghost start`).

export TG_HOST="${TG_HOST:-127.0.0.1}"

# --- Optional output capture (EXPERIMENTAL, opt-in) -------------------------
# When TG_CAPTURE_OUTPUT=1, re-exec this interactive shell inside `script` so
# command output is logged to a per-session transcript the precmd hook can
# tail. `script` keeps a real PTY, so the terminal behaves normally. Off by
# default (and POSIX-only — Windows needs ConPTY, not yet supported).
if [[ "$TG_CAPTURE_OUTPUT" == "1" && -z "$_TG_IN_SCRIPT" && $- == *i* ]] \
   && command -v script >/dev/null 2>&1; then
  export _TG_IN_SCRIPT=1
  export _TG_LOG="${TMPDIR:-/tmp}/tg-capture.$$.log"
  : > "$_TG_LOG" 2>/dev/null
  if script --version 2>/dev/null | grep -qi util-linux; then
    exec script -qf -c "exec $SHELL" "$_TG_LOG"   # util-linux
  else
    exec script -q "$_TG_LOG" "$SHELL"            # BSD / macOS
  fi
fi

# Send one command event. Args: cmd exit_code cwd duration_ms [output]
_tg_send_event() {
  python3 - "$1" "$2" "$3" "$4" "${5:-}" <<'PY' >/dev/null 2>&1 &
import json, os, socket, sys, time

cmd, exit_code, cwd, duration = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4])
output = sys.argv[5] if len(sys.argv) > 5 else ""
payload = {
    "cmd": cmd,
    "exit": max(0, min(255, exit_code)),
    "cwd": cwd,
    "duration": max(0, duration),
    "ts": time.time(),
    "pid": os.getppid(),
    "shell": "bash",
}
if output:
    payload["output"] = output
try:
    with open(os.path.expanduser("~/.local/share/terminalghost/token")) as _tf:
        payload["token"] = _tf.read().strip()
except OSError:
    pass
# Port: explicit TG_PORT wins; else the daemon's runtime port file; else 48632.
port = os.environ.get("TG_PORT", "")
if not port:
    try:
        with open(os.path.expanduser("~/.local/share/terminalghost/port")) as _pf:
            port = _pf.read().strip()
    except OSError:
        pass
line = json.dumps(payload) + "\n"
try:
    with socket.create_connection(
        (os.environ.get("TG_HOST", "127.0.0.1"), int(port or "48632")),
        timeout=0.5,
    ) as sock:
        sock.sendall(line.encode("utf-8"))
except OSError:
    pass  # daemon not running — stay silent
PY
  disown 2>/dev/null
}

# Read the transcript written since the last command (stripped of ANSI), or "".
_tg_capture_output() {
  [[ -n "$_TG_LOG" && -f "$_TG_LOG" ]] || { echo ""; return; }
  tail -c "+$(( ${_TG_LOG_OFF:-0} + 1 ))" "$_TG_LOG" 2>/dev/null \
    | sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g' | tail -n 40 | tail -c 4000
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
  # Mark where this command's output will start in the transcript.
  [[ -n "$_TG_LOG" && -f "$_TG_LOG" ]] && _TG_LOG_OFF=$(wc -c < "$_TG_LOG" 2>/dev/null)
}

_tg_precmd() {
  local exit_code=$?
  _TG_PREEXEC_DONE=0
  [[ -z "$_TG_CMD" ]] && return
  local duration=$(( $(_tg_now_ms) - ${_TG_START:-$(_tg_now_ms)} ))
  local output=""
  [[ -n "$_TG_LOG" ]] && output="$(_tg_capture_output)"
  _tg_send_event "$_TG_CMD" "$exit_code" "$PWD" "$duration" "$output"
  unset _TG_CMD _TG_START
  # Opt-in proactive hint after a failure (TG_HINTS=1). Blocks briefly.
  if [[ "$TG_HINTS" == "1" && "$exit_code" != 0 ]]; then
    terminalghost hint 2>/dev/null
  fi
  return $exit_code  # don't clobber $? for the user's prompt
}

trap '_tg_preexec' DEBUG
PROMPT_COMMAND="_tg_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"

# `??` — query TerminalGhost via the ask client (streams the answer here).
# Note: bash expands ?? as a glob first; if a two-character file exists in
# the cwd it wins. `tg` is provided as an unambiguous fallback.
function ?? { terminalghost ask "$@"; }
# tg: ask normally, but explain piped input (e.g. `make 2>&1 | tg`).
function tg { if [ -t 0 ]; then terminalghost ask "$@"; else terminalghost explain; fi; }

# tgr <cmd> — run a command with its output captured for the next ??.
# tga — run the command TerminalGhost last suggested (asks first).
function tgr { terminalghost exec "$@"; }
function tga { terminalghost apply "$@"; }
