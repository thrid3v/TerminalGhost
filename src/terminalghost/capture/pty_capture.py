# terminalghost.capture.pty_capture
#
# Responsibility:
#   Wrap the user's interactive shell in a pseudo-terminal (PTY) so that
#   stdin, stdout, and stderr streams can be observed before being forwarded
#   to the real terminal. This is the "deep capture" path — it sees raw
#   bytes before the shell processes them.
#
# Key classes / functions to implement:
#
#   class PTYCapture:
#       """
#       Spawns a child shell process attached to a PTY master/slave pair.
#       Sits in a select() loop forwarding bytes between the real terminal
#       and the child PTY while tee-ing copies to the capture pipeline.
#
#       Args:
#           shell (str): Path to the shell binary, e.g. "/bin/zsh"
#           config (Config): Loaded config object
#           on_command (Callable[[CommandEvent], None]): callback fired when
#               a complete command + exit code pair is detected
#           on_trigger (Callable[[str], None]): callback fired when "??"
#               is detected as standalone input
#
#       Notes:
#           - Uses `ptyprocess.PtyProcess` (or stdlib `pty.openpty` + fork)
#             to create the PTY pair.
#           - Must resize the PTY when the outer terminal resizes (SIGWINCH).
#           - Must NOT store or forward ANSI escape sequences that clear the
#             screen; strip them before passing to the output buffer.
#       """
#
#       def start(self) -> None:
#           """
#           Fork the child shell, enter the forwarding loop.
#           Blocks until the child exits. Should be called in its own thread
#           or process so the daemon event loop stays responsive.
#           """
#           pass  # TODO: implement
#
#       def stop(self) -> None:
#           """
#           Send SIGHUP to child shell, drain remaining output, close PTY fds.
#           Safe to call from another thread.
#           """
#           pass  # TODO: implement
#
#       def _detect_trigger(self, line: bytes) -> bool:
#           """
#           Return True if `line` is exactly b"??" (plus optional trailing
#           whitespace/newline). Must NOT match:
#             - "echo ??"  (part of a larger command)
#             - "??" typed inside a running REPL such as python or node
#             - "??" typed while an editor (vim, nano, emacs) is open
#           Strategy: track whether a "safe" interactive prompt is currently
#           displayed (by watching for shell PS1 sequences) and only arm the
#           trigger detection in that state.
#           """
#           pass  # TODO: implement
#
#       def _redact_sensitive(self, line: bytes) -> bytes:
#           """
#           Detect lines that look like password prompts (contain the word
#           "password", "passphrase", "token", "secret" case-insensitively,
#           or match sudo/ssh prompt patterns) and replace with a redaction
#           placeholder. The NEXT line after a password prompt should also be
#           suppressed (it would be the typed password echoed or blank).
#
#           Args:
#               line (bytes): raw output line from the PTY
#           Returns:
#               bytes: the original line, or b"<redacted>" if sensitive
#           """
#           pass  # TODO: implement
#
#       def _handle_special_process(self, command: str) -> bool:
#           """
#           Detect when the child has launched a process that "takes over"
#           the terminal in a way that makes line-level parsing meaningless:
#           editors (vim, nano, emacs, micro), REPLs (python, node, irb,
#           psql), pagers (less, more), ssh sessions, tmux/screen.
#
#           While such a process is active, the PTY capture layer should
#           pass bytes through unchanged without attempting to parse them
#           into commands or detect "??".
#
#           Returns:
#               bool: True if the process is "terminal-consuming"
#           """
#           pass  # TODO: implement
#
# Edge cases to handle:
#   - Partial writes: a single `write()` call from the child may deliver a
#     partial line; buffer incomplete lines across iterations.
#   - SIGWINCH: when the outer terminal resizes, propagate the new window
#     size to the PTY via fcntl(TIOCSWINSZ).
#   - PTY vs. pipe: some programs detect non-TTY stdout and change behavior
#     (e.g. disable color, buffer differently). The PTY preserves TTY
#     semantics so this should be transparent.
#   - Binary / non-UTF8 output: don't crash; skip non-decodable lines for
#     storage but still forward bytes to the real terminal.
#   - Exit code capture: the PTY layer alone cannot reliably capture exit
#     codes. Delegate exit-code capture to the shell hooks layer; PTY layer
#     only captures raw output and command text.
#
# Imports needed:
#   import os, select, signal, fcntl, struct, termios
#   from ptyprocess import PtyProcess   # or stdlib pty
#   from terminalghost.config.loader import Config
#   from terminalghost.storage.db import CommandEvent

# TODO: implement PTYCapture class
