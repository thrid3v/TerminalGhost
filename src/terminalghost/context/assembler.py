# terminalghost.context.assembler
#
# Responsibility:
#   Assembles the structured prompt that is sent to the LLM when the user
#   types `??`. Pulls data from three sources:
#     1. Rolling command history (from storage.Database)
#     2. Current directory tree (filesystem walk)
#     3. Most recent error command (from storage.Database.get_last_error)
#   Formats everything into a single string within a configurable token budget.
#
# Key classes / functions to implement:
#
#   class ContextAssembler:
#       """
#       Builds an LLM prompt from shell context.
#
#       Args:
#           db (Database): open database handle
#           config (Config): for tree_depth, tree_ignore, token_budget
#       """
#
#       def assemble(self, cwd: str, inline_context: str = "") -> str:
#           """
#           Build and return the complete prompt string.
#
#           Steps (in order):
#             1. Get the last error from db.get_last_error()
#             2. Get recent commands from db.get_recent_commands()
#             3. Walk the directory tree rooted at `cwd`
#             4. Format each section (see _format_* helpers below)
#             5. Combine sections, enforce token_budget by trimming oldest
#                commands first until the estimated token count is within budget
#             6. Append inline_context (the text after "??", if any)
#
#           Args:
#               cwd (str): current working directory at time of ?? trigger
#               inline_context (str): optional extra text the user typed after ??
#           Returns:
#               str: the complete prompt ready to send to the LLM backend
#           """
#           pass  # TODO: implement
#
#       def _format_command_history(
#           self, commands: list["CommandEvent"], last_error: "CommandEvent | None"
#       ) -> str:
#           """
#           Format the command history as a numbered list, marking the last
#           error entry with a visual indicator. Oldest command first.
#
#           Example output:
#             ## Recent commands
#             1. [cwd: ~/project] $ make build  (exit 1, 4231ms)
#                output: make: *** [Makefile:12: all] Error 1
#             2. [cwd: ~/project] $ cat Makefile  (exit 0)
#
#           Args:
#               commands (list[CommandEvent]): ordered oldest → newest
#               last_error (CommandEvent | None): highlighted failing command
#           Returns:
#               str: formatted section
#           """
#           pass  # TODO: implement
#
#       def _format_directory_tree(self, cwd: str) -> str:
#           """
#           Walk `cwd` up to config.tree_depth levels deep and return a
#           tree-formatted string similar to the `tree` Unix command.
#           Skip directories in config.tree_ignore.
#           Cap total entries at 200 to avoid enormous prompts for large
#           repos (show a "(truncated)" notice if capped).
#
#           Args:
#               cwd (str): root directory to walk
#           Returns:
#               str: formatted tree section, or "(directory not found)" if
#                    cwd does not exist
#           """
#           pass  # TODO: implement
#
#       def _format_last_error(self, event: "CommandEvent") -> str:
#           """
#           Format the most recent error command as a highlighted section
#           at the TOP of the prompt so the LLM immediately sees what failed.
#
#           Args:
#               event (CommandEvent): the failing command
#           Returns:
#               str: formatted error section
#           """
#           pass  # TODO: implement
#
#       def _estimate_tokens(self, text: str) -> int:
#           """
#           Rough token count estimate (no tokenizer dependency).
#           A common approximation: 1 token ≈ 4 characters for English text.
#           Used to check whether the assembled prompt fits within token_budget
#           before trimming.
#
#           Args:
#               text (str): text to estimate
#           Returns:
#               int: estimated token count
#           """
#           pass  # TODO: implement
#
# Edge cases:
#   - cwd does not exist (user cd'd somewhere that was deleted): show a
#     warning in the tree section instead of raising.
#   - Symlink loops in the directory tree: use os.walk(followlinks=False)
#     or track visited inodes.
#   - No commands in history (fresh session): produce a minimal prompt with
#     just the CWD tree and inline context.
#   - No last error (all commands succeeded): omit the "last error" section.
#   - token_budget exceeded even with 0 history commands: truncate the tree.
#   - Binary files in the directory: only list file names, never read content.
#   - Very deep or large repos (e.g. node_modules not in ignore list): the
#     200-entry cap prevents runaway prompt sizes.
#   - inline_context can be empty string; in that case omit the "User note"
#     section rather than adding an empty heading.
#
# Imports needed:
#   import os, logging
#   from terminalghost.storage.db import Database, CommandEvent
#   from terminalghost.config.loader import Config

# TODO: implement ContextAssembler class
