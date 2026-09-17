"""XDG-style default locations for Libranet's config, data, and log files.

Resolution is delegated to :mod:`platformdirs` so that each platform's own
convention is honored (``~/.config/libranet`` and ``~/.local/share/libranet``
on Linux, ``~/Library/...`` on macOS, ``%LOCALAPPDATA%`` on Windows).

These are *defaults only*: every path they produce can be overridden by the
YAML config file or by the supervisor's command line.
"""

from __future__ import annotations
from pathlib import Path
from platformdirs import PlatformDirs

APP_NAME = "libranet"

CONFIG_FILE_NAME = "libranet.yaml"

_DIRS = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)


def default_config_dir() -> Path:
    """Directory holding the human-edited YAML configuration."""
    return Path(_DIRS.user_config_dir)


def default_config_file() -> Path:
    """Full path of the YAML config file loaded when none is given."""
    return default_config_dir() / CONFIG_FILE_NAME


def default_data_dir() -> Path:
    """Root of the node's persistent state: CAS, database, identity keys."""
    return Path(_DIRS.user_data_dir)


def default_log_dir() -> Path:
    """Directory the rotating log files are written to."""
    return Path(_DIRS.user_log_dir)


def default_cache_dir() -> Path:
    """Directory for regenerable files such as cached search results."""
    return Path(_DIRS.user_cache_dir)
