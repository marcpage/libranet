"""Exceptions raised reading a node's configuration and its seed list."""


class ConfigError(ValueError):
    """Raised when a config file cannot be read, parsed, or validated."""


class SeedError(ValueError):
    """Raised when a seed list cannot be read or does not match the schema."""
