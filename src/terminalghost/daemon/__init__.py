# terminalghost.daemon — background process management
#
# Re-exports the daemon entrypoint so `terminalghost start` works.

from terminalghost.daemon.process import main

__all__ = ["main"]
