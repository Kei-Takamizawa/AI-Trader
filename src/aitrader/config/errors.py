"""Configuration errors."""


class ConfigError(Exception):
    """Invalid configuration. The message names the offending field and value."""


class LiveDisabledError(ConfigError):
    """LIVE mode was requested but is disabled in this build."""
