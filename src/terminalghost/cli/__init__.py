# terminalghost.cli
#
# User-facing setup commands (init / doctor / hook-path / uninstall) and the
# shell-profile plumbing they share. Kept separate from daemon.process so the
# long-running daemon path stays lean and these import Rich/psutil freely.
