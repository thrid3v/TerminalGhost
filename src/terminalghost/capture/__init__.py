# terminalghost.capture — shell capture layer
#
# Responsibility:
#   Re-exports the two public entry points for the capture layer so callers
#   can do `from terminalghost.capture import PTYCapture, HookReceiver`
#   without caring about the internal file split.
#
# Submodules:
#   pty_capture   — PTY-based low-level interposition wrapper
#   shell_hooks   — Unix-socket receiver for data sent by zsh/bash hook scripts
#
# What calls this:
#   daemon.process imports both classes to set up capture on daemon start.

# TODO: from terminalghost.capture.pty_capture import PTYCapture
# TODO: from terminalghost.capture.shell_hooks import HookReceiver
