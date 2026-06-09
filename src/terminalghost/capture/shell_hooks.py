# terminalghost.capture.shell_hooks
#
# Responsibility:
#   Receive structured command-event data from the shell hook scripts
#   (scripts/zsh_hooks.sh, scripts/bash_hooks.sh) over a Unix domain socket.
#   This is the "lightweight capture" path — the shell hooks are responsible
#   for collecting command text, exit code, CWD, and timing; this module just
#   receives and persists that data.
#
# Protocol:
#   Each hook sends a single newline-terminated JSON object per command:
#     {
#       "cmd":      "make build",
#       "exit":     1,
#       "cwd":      "/home/user/project",
#       "duration": 4231,          # milliseconds
#       "ts":       1718000000.123 # Unix timestamp float
#     }
#   The socket is a SOCK_STREAM Unix domain socket at config.general.socket_path.
#   Multiple shell sessions can connect simultaneously.
#
# Key classes / functions to implement:
#
#   class HookReceiver:
#       """
#       Async Unix-socket server that accepts connections from shell hook
#       scripts and deserializes incoming CommandEvent records.
#
#       Args:
#           socket_path (str): Path to the Unix domain socket file.
#           on_event (Callable[[CommandEvent], Awaitable[None]]): async
#               callback fired for each complete, validated event received.
#           config (Config): for validation limits (max_output_bytes, etc.)
#
#       Notes:
#           - Must handle multiple concurrent connections (one per shell session).
#           - A partial JSON payload across two TCP segments must be buffered
#             until the newline delimiter arrives.
#           - On daemon shutdown the socket file must be unlinked.
#       """
#
#       async def start(self) -> None:
#           """
#           Bind and listen on socket_path, accept connections in a loop.
#           This is an asyncio coroutine; run it with asyncio.create_task().
#           Remove a stale socket file (from a previous crashed daemon) before
#           binding if it already exists.
#           """
#           pass  # TODO: implement
#
#       async def stop(self) -> None:
#           """
#           Stop accepting new connections, close active connections, unlink
#           the socket file.
#           """
#           pass  # TODO: implement
#
#       async def _handle_connection(
#           self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
#       ) -> None:
#           """
#           Read newline-delimited JSON from one connected shell session.
#           Validate each payload, convert to CommandEvent, call on_event.
#           Log and skip malformed packets rather than crashing.
#
#           Args:
#               reader: asyncio stream reader for this connection
#               writer: asyncio stream writer (used only to close cleanly)
#           """
#           pass  # TODO: implement
#
#       def _parse_payload(self, raw: str) -> "CommandEvent":
#           """
#           Parse and validate a raw JSON string into a CommandEvent.
#
#           Args:
#               raw (str): single JSON line from the socket
#           Returns:
#               CommandEvent: validated dataclass
#           Raises:
#               ValueError: if required fields are missing or types are wrong
#
#           Validation rules:
#             - "cmd" must be a non-empty string
#             - "exit" must be an int 0-255
#             - "cwd" must be a non-empty string
#             - "duration" must be a non-negative int (ms)
#             - "ts" must be a positive float
#             - "cmd" length capped at 4096 chars to prevent abuse
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - Socket file left over from a crashed daemon: remove it before bind().
#   - Shell sends an empty line / keep-alive: ignore silently.
#   - Connection closed mid-payload (shell killed): discard the partial buffer.
#   - Very long commands (heredocs, etc.): cap at 4096 bytes before storing.
#   - The trigger word "??" will arrive as a command too; let the trigger
#     module handle it — the hook receiver just stores it like any other cmd.
#   - On macOS, Unix socket paths have a 104-byte limit; validate path length.
#
# Imports needed:
#   import asyncio, json, os, logging
#   from terminalghost.storage.db import CommandEvent
#   from terminalghost.config.loader import Config

# TODO: implement HookReceiver class
