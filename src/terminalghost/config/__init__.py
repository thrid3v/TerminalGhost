# terminalghost.config — configuration loading and validation
#
# Re-exports the public interface.

from terminalghost.config.loader import Config, ConfigError, load_config

__all__ = ["Config", "ConfigError", "load_config"]
