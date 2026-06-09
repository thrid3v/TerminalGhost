# terminalghost.daemon.process
#
# Responsibility:
#   The long-running background process that ties all modules together.
#   Handles:
#     - CLI entrypoint (start / stop / status / restart subcommands)
#     - PID file creation and stale-PID detection
#     - Unix signal handling (SIGTERM, SIGHUP for config reload)
#     - Opening the database
#     - Starting the HookReceiver (Unix socket server)
#     - Optionally starting the PTYCapture wrapper
#     - Wiring CommandEvents from capture → storage → TriggerHandler
#     - Clean shutdown sequence
#
# Key classes / functions to implement:
#
#   class Daemon:
#       """
#       Orchestrator for all runtime services.
#       Not responsible for business logic — only lifecycle and wiring.
#
#       Args:
#           config (Config): loaded config object
#       """
#
#       async def run(self) -> None:
#           """
#           Main async event loop. Opens the database, starts the socket
#           receiver, installs signal handlers, and runs until a shutdown
#           signal is received.
#
#           Steps:
#             1. Write PID file at config.general.pid_file
#             2. Open Database
#             3. Instantiate ContextAssembler, get_backend(config), TriggerHandler
#             4. Start HookReceiver as an asyncio task
#             5. Optionally start PTYCapture in a thread (if config.capture.use_pty)
#             6. Wait for shutdown event (asyncio.Event set by signal handler)
#             7. Call stop() on all services in reverse order
#             8. Remove PID file
#           """
#           pass  # TODO: implement
#
#       def _on_event(self, event: "CommandEvent") -> None:
#           """
#           Callback wired to HookReceiver.on_event.
#           1. Persist event to database via db.insert_command(event)
#           2. Schedule TriggerHandler.handle(event.cmd, event.cwd) as an
#              asyncio task if the event looks like a trigger (avoids blocking
#              the receiver coroutine on LLM latency)
#           """
#           pass  # TODO: implement
#
#       def _install_signal_handlers(
#           self, loop: "asyncio.AbstractEventLoop", shutdown_event: "asyncio.Event"
#       ) -> None:
#           """
#           Register SIGTERM and SIGINT handlers that set shutdown_event.
#           Register SIGHUP handler that reloads config (hot-reload).
#           Use loop.add_signal_handler() (Unix only).
#
#           Args:
#               loop: the running asyncio event loop
#               shutdown_event: event to set on SIGTERM/SIGINT
#           """
#           pass  # TODO: implement
#
#       def _write_pid_file(self, path: str) -> None:
#           """
#           Write the current process PID to `path`.
#           Raise RuntimeError if a PID file already exists AND the process
#           it references is still running (another daemon instance is active).
#           If the PID file exists but the process is gone, overwrite it.
#
#           Args:
#               path (str): absolute path to the PID file
#           """
#           pass  # TODO: implement
#
#       def _remove_pid_file(self, path: str) -> None:
#           """
#           Remove the PID file. Ignore FileNotFoundError (already removed).
#
#           Args:
#               path (str): absolute path to the PID file
#           """
#           pass  # TODO: implement
#
#   def main() -> None:
#       """
#       CLI entrypoint registered in pyproject.toml as `terminalghost`.
#       Parse sys.argv for subcommands: start, stop, status, restart.
#
#       Subcommand behavior:
#         start   — fork to background (double-fork daemonize pattern on Unix),
#                   load config, instantiate Daemon, run asyncio.run(daemon.run())
#         stop    — read PID file, send SIGTERM, wait for process to exit
#         status  — check if PID is alive, print running/stopped
#         restart — stop then start
#
#       Args: none (reads sys.argv)
#       Returns: None (exits with sys.exit code 0 or 1)
#       """
#       pass  # TODO: implement
#
#   def _daemonize() -> None:
#       """
#       Double-fork daemonization (Unix only):
#         1. Fork; parent exits
#         2. setsid() — detach from controlling terminal
#         3. Fork again; first child exits
#         4. Redirect stdin/stdout/stderr to /dev/null
#       After this call, the process is a proper Unix daemon.
#       Skip this on platforms that don't support os.fork() (Windows).
#       """
#       pass  # TODO: implement
#
# Edge cases:
#   - Double-start: if daemon is already running (PID file + live process),
#     print an error and exit 1 rather than starting a second instance.
#   - Stop a non-running daemon: print a helpful message rather than crashing.
#   - asyncio event loop already running (e.g. during testing): use
#     asyncio.get_event_loop() carefully; prefer asyncio.run() for the top level.
#   - Signal handling on Windows: os.fork() and SIGHUP don't exist. The daemon
#     command should print a clear "not supported on Windows" message and exit.
#   - Config reload (SIGHUP): re-read the config file but do NOT restart the
#     database connection or socket server — only update LLM backend settings.
#   - Crash during startup (e.g. DB open fails): ensure PID file is not written
#     (or is cleaned up) so a subsequent `terminalghost start` works.
#
# Imports needed:
#   import asyncio, os, sys, signal, logging, argparse
#   import psutil
#   from terminalghost.config.loader import load_config
#   from terminalghost.storage.db import Database
#   from terminalghost.context.assembler import ContextAssembler
#   from terminalghost.llm.base import get_backend
#   from terminalghost.trigger.handler import TriggerHandler
#   from terminalghost.capture.shell_hooks import HookReceiver
#   from terminalghost.capture.pty_capture import PTYCapture  # optional

# TODO: implement Daemon class and main() entrypoint
