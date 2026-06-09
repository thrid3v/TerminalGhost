#!/usr/bin/env bash
# scripts/bash_hooks.sh
#
# Shell integration for bash.
# Source this file in your ~/.bashrc:
#   source /path/to/terminalghost/scripts/bash_hooks.sh
#
# What this installs:
# ─────────────────────────────────────────────────────────────────────────────
#
# Bash does not have preexec/precmd hooks built in. Two approaches:
#
# APPROACH A — DEBUG trap + PROMPT_COMMAND (no extra dependency)
#   The DEBUG trap fires before each command; PROMPT_COMMAND fires after.
#
#   Pseudocode:
#     function _tg_preexec() {
#       # Called via DEBUG trap — $BASH_COMMAND holds the command about to run
#       # Only capture if this is a "real" interactive command, not an internal
#       # shell evaluation. Guard: _TG_PREEXEC_DONE prevents double-firing.
#       if [[ "$_TG_PREEXEC_DONE" != "1" ]]; then
#         _TG_CMD="$BASH_COMMAND"
#         _TG_START=$(date +%s%3N)
#         _TG_PREEXEC_DONE=1
#       fi
#     }
#     trap '_tg_preexec' DEBUG
#
#     function _tg_precmd() {
#       local exit_code=$?
#       _TG_PREEXEC_DONE=0   # reset for next command
#       local end_ts=$(date +%s%3N)
#       local duration=$(( end_ts - ${_TG_START:-$end_ts} ))
#       # ... same payload + socat send as zsh version ...
#     }
#     export PROMPT_COMMAND="_tg_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
#
# APPROACH B — bash-preexec (recommended, easier, more robust)
#   Install the bash-preexec.sh library (https://github.com/rcaloras/bash-preexec)
#   and use the same preexec / precmd hook names as zsh. This is the recommended
#   approach because the DEBUG trap fires for every shell command including
#   internal ones, making it tricky to filter correctly.
#   Pseudocode:
#     source ~/bash-preexec.sh
#     preexec() { ... same as zsh preexec ... }
#     precmd()  { ... same as zsh precmd  ... }
#
# The ?? function is identical to the zsh version:
#   Pseudocode:
#     function '??'() {
#       # Send ?? payload directly to socket (same as zsh version)
#     }
#
# Caveats vs. zsh:
#   - The DEBUG trap approach has a known problem: it also fires for commands
#     inside pipelines, $(subshells), and `backtick` subshells. Use the
#     BASH_SUBSHELL variable to filter out subshell invocations.
#   - $BASH_COMMAND does not always match what the user typed (aliases are
#     not expanded, history expansion may or may not apply). Use `history 1`
#     as a fallback to get the "typed" command text.
#   - bash versions < 4.4 have quirks with the DEBUG trap and functions;
#     test on the target bash version.
#
# Requirements: same as zsh_hooks.sh (socat, running daemon).

# TODO: replace pseudocode with real shell implementation
_TG_SOCKET="${TG_SOCKET:-/tmp/terminalghost.sock}"
