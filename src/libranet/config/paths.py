"""XDG-style default locations for Libranet's config, data, and log files.

Resolution is delegated to :mod:`platformdirs` so that each platform's own
convention is honored (``~/.config/libranet`` and ``~/.local/share/libranet``
on Linux, ``~/Library/...`` on macOS, ``%LOCALAPPDATA%`` on Windows).

The folders offered to local clients come from it too, as each platform
names the user's own: ``~/Movies`` on macOS, and on Linux whatever the XDG
user directories (``user-dirs.dirs``) say, ``~/Videos`` if they say nothing.

These are *defaults only*: every path they produce can be overridden by the
YAML config file or by the supervisor's command line.
"""

from __future__ import annotations
from pathlib import Path
from typing import Final

from platformdirs import PlatformDirs
from platformdirs.api import PlatformDirsABC

APP_NAME: Final = "libranet"

CONFIG_FILE_NAME: Final = "libranet.yaml"

_DIRS: Final = PlatformDirs(appname=APP_NAME, appauthor=False, roaming=False)


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


def default_local_folders(dirs: PlatformDirsABC = _DIRS) -> tuple[Path, ...]:
    """The folders offered to local clients: the user's own, as ``dirs`` names them.

    That is their desktop, documents, downloads, music, pictures, and videos
    folders (HttpApi §12.2, Phase 3 Step 68).
    """
    return (
        Path(dirs.user_desktop_dir),
        Path(dirs.user_documents_dir),
        Path(dirs.user_downloads_dir),
        Path(dirs.user_music_dir),
        Path(dirs.user_pictures_dir),
        Path(dirs.user_videos_dir),
    )
