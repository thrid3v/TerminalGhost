#!/usr/bin/env zsh
# scripts/zsh_hooks.sh
#
# Shell integration for zsh.
# Source this file in your ~/.zshrc:
#   source /path/to/terminalghost/scripts/zsh_hooks.sh
#
# What this installs:
# ─────────────────────────────────────────────────────────────────────────────
#
# 1. preexec hook  — called by zsh BEFORE each command executes.
#    Captures: the raw command string typed by the user.
#    Also records: current timestamp (start time for duration calc).
#    Pseudocode:
#      function _tg_preexec() {
#        _TG_CMD="$1"                    # $1 is the command string
#        _TG_START=$(date +%s%3N)        # milliseconds since epoch
#      }
#      add-zsh-hook preexec _tg_preexec
#
# 2. precmd hook  — called by zsh AFTER each command, just before the next
#    prompt is displayed. At this point $? holds the exit code of the
#    previous command.
#    Captures: exit code, working directory, duration, and sends everything
#    to the daemon over the Unix socket.
#    Pseudocode:
#      function _tg_precmd() {
#        local exit_code=$?
#        local end_ts=$(date +%s%3N)
#        local duration=$(( end_ts - _TG_START ))
#        local payload=$(printf '{"cmd":"%s","exit":%d,"cwd":"%s","duration":%d,"ts":%s}\n' \
#          "$(echo $_TG_CMD | sed 's/"/\\"/g')" \
#          "$exit_code" \
#          "$(pwd)" \
#          "$duration" \
#          "$(date +%s.%N)")
#        echo "$payload" | socat - UNIX-CONNECT:$_TG_SOCKET 2>/dev/null
#        unset _TG_CMD _TG_START
#      }
#      add-zsh-hook precmd _tg_precmd
#
# 3. ?? function / alias — allows the user to type ?? at the prompt.
#    The function sends the literal string "??" as a command through the
#    normal preexec/precmd pipeline. The daemon's TriggerHandler detects it.
#    Pseudocode:
#      function ??() {
#        local inline="${@}"
#        local cmd="??"
#        [[ -n "$inline" ]] && cmd="?? $inline"
#        # Execute as a no-op so preexec/precmd fire with this command text.
#        # Actually, zsh does NOT run preexec for shell functions directly;
#        # we need to send directly to the socket here instead.
#        local payload=$(printf '{"cmd":"%s","exit":0,"cwd":"%s","duration":0,"ts":%s}\n' \
#          "$cmd" "$(pwd)" "$(date +%s.%N)")
#        echo "$payload" | socat - UNIX-CONNECT:$_TG_SOCKET
#      }
#
# Configuration:
#   _TG_SOCKET — path to the daemon's Unix socket.
#   Defaults to /tmp/terminalghost.sock; override before sourcing:
#     export TG_SOCKET=/custom/path.sock
#
# Requirements:
#   - `socat` must be installed (brew install socat / apt install socat)
#   - OR replace socat with `nc -U $socket` on systems that support it
#   - The terminalghost daemon must be running (`terminalghost start`)
#
# Notes:
#   - preexec/precmd are zsh-specific. For bash, see bash_hooks.sh.
#   - The hook silently does nothing if the socket is not available
#     (2>/dev/null suppresses socat errors). This is intentional: the shell
#     should never fail or slow down because the daemon is stopped.
#   - Special characters in $_TG_CMD (quotes, backslashes, newlines) must be
#     JSON-escaped before embedding in the payload. The pseudocode above uses
#     a simplistic sed; a real implementation should use Python or jq for
#     robust escaping.
#   - Password-containing commands: the hook sends the full command string.
#     Mitigation: the daemon's capture layer redacts lines matching password
#     patterns. However, for extra safety, the hooks could be extended to
#     strip commands matching a local blocklist before sending.

# TODO: replace the pseudocode above with real shell implementation
_TG_SOCKET="${TG_SOCKET:-/tmp/terminalghost.sock}"
