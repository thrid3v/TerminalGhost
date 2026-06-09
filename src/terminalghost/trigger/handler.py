# terminalghost.trigger.handler
#
# Responsibility:
#   Detects when the user types `??` (optionally followed by extra context)
#   as a standalone shell command, then orchestrates the full query pipeline:
#     capture detection → context assembly → LLM query → streamed output.
#
#   This is the "main loop" of the user-facing feature. Everything else in
#   the project exists to support this module.
#
# Key classes / functions to implement:
#
#   class TriggerHandler:
#       """
#       Listens for `??` commands delivered via the capture layer and runs
#       the query pipeline when one is detected.
#
#       Args:
#           db (Database): open database handle
#           assembler (ContextAssembler): prompt builder
#           backend (LLMBackend): configured LLM backend
#           config (Config): for inline_context setting and output preferences
#       """
#
#       async def handle(self, cmd: str, cwd: str) -> None:
#           """
#           Entry point called by the capture layer when a command event
#           arrives. Checks whether `cmd` is a trigger; if so, runs the
#           full pipeline. If not, returns immediately (no-op).
#
#           Pipeline steps:
#             1. is_trigger(cmd) — guard; return early if False
#             2. extract_inline_context(cmd) — parse optional text after ??
#             3. backend.is_available() — fast pre-check; print error and
#                return if backend is down (avoid assembling a big prompt
#                only to fail at the HTTP call)
#             4. assembler.assemble(cwd, inline_context)
#             5. Print a header line to the terminal (e.g. "TerminalGhost ▶")
#             6. backend.stream_query(prompt) — stream to terminal via Rich
#             7. Print a trailing newline / separator
#
#           Args:
#               cmd (str): the command text that was just executed
#               cwd (str): the working directory at the time of the command
#           """
#           pass  # TODO: implement
#
#       def is_trigger(self, cmd: str) -> bool:
#           """
#           Return True if `cmd` should activate the query pipeline.
#
#           Rules:
#             - Strip leading/trailing whitespace from cmd.
#             - The trigger is the string "??" optionally followed by a
#               space and arbitrary text: re.match(r'^\?\?(\s+.*)?$', cmd)
#             - Must be False for:
#                 "echo ??"          (part of a larger command — but note the
#                                     shell hook would send "echo ??" as the
#                                     full command text, so this actually
#                                     depends on whether the hook sees the
#                                     evaluated or literal command)
#                 "" or "? ?"        (wrong spacing)
#             - The PTY layer should only call this when a shell prompt is
#               active (not inside a REPL or editor); the hook layer always
#               sends complete commands so context is already correct.
#
#           Args:
#               cmd (str): raw command text
#           Returns:
#               bool
#           """
#           pass  # TODO: implement
#
#       def extract_inline_context(self, cmd: str) -> str:
#           """
#           Extract the optional free-text portion after "??".
#           E.g.: "?? why does make fail on ARM" → "why does make fail on ARM"
#                 "??"                            → ""
#
#           Args:
#               cmd (str): trigger command text
#           Returns:
#               str: the inline context portion (stripped), or ""
#           """
#           pass  # TODO: implement
#
#       async def _stream_to_terminal(
#           self, stream: "AsyncIterator[str]"
#       ) -> None:
#           """
#           Consume an async text stream and print each chunk to stdout using
#           Rich's Live display or direct sys.stdout.write for low-latency
#           streaming. Ensure the terminal is left in a clean state even if
#           the user hits Ctrl-C mid-stream.
#
#           Args:
#               stream: async iterator of text chunks from backend.stream_query
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - Ctrl-C during streaming: catch KeyboardInterrupt / asyncio.CancelledError,
#     print a newline, and return cleanly rather than printing a traceback.
#   - Backend unavailable: print a friendly one-line error to stderr and
#     return; do NOT crash the daemon.
#   - "??" typed inside a running Python REPL or node session: the PTY layer
#     should suppress triggers in this state (handled upstream); TriggerHandler
#     itself just validates the command string.
#   - Extremely long LLM response: no hard limit; streaming keeps memory low.
#   - User types "??" as part of a shell script: the hook sends the evaluated
#     command; "??" as a script token would not normally be sent by the hook.
#   - Race condition: two "??" commands queued rapidly. Use an asyncio.Lock
#     to prevent overlapping pipeline runs.
#
# Imports needed:
#   import asyncio, re, sys, logging
#   from rich.console import Console
#   from terminalghost.storage.db import Database
#   from terminalghost.context.assembler import ContextAssembler
#   from terminalghost.llm.base import LLMBackend, LLMError

# TODO: implement TriggerHandler class
