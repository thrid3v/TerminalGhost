# terminalghost.cli.tips
#
# Rotating "did you know" one-liners surfaced in `doctor` and `start` output.
# TerminalGhost has a lot of surface area; most of it is invisible unless
# something points at it. Kept to the low-traffic commands so it informs
# without nagging (never shown after ?? answers).

from __future__ import annotations

import random

TIPS: tuple[str, ...] = (
    "run a command with `tgr <cmd>` and the next ?? sees its actual output",
    "pipe anything into `tg` to have it explained: make 2>&1 | tg",
    "`?? fix` answers with just the corrected command — `tga` runs it",
    "`terminalghost log --failed` shows only the commands that broke",
    "search your history: terminalghost log --grep docker --since 2h",
    "`terminalghost clear --last 1` forgets the last captured command",
    "check what gets stored: terminalghost redact-check \"export TOKEN=abc\"",
    "moving machines? terminalghost export > snap.json, then import it there",
    "switch models on the fly: terminalghost use ollama:mistral",
    "add -c to ?? / qq to copy the answer straight to your clipboard",
    "a ?? within 5 minutes remembers the previous answer — ask follow-ups",
    "export TG_HINTS=1 to get a one-line fix suggestion after every failure",
    "`terminalghost dashboard` is a live full-screen view of your history",
    "themes: terminalghost theme light (or high-contrast)",
)


def random_tip() -> str:
    """One tip, chosen at random (uniform — no state to maintain)."""
    return random.choice(TIPS)
