"""Tests for the default folders offered to local clients, as each platform names them."""

from __future__ import annotations
from pathlib import Path

from platformdirs.macos import MacOS
from platformdirs.unix import Unix
from pytest import MonkeyPatch, fixture

from libranet.config.paths import APP_NAME, default_local_folders

# What the XDG user directories may name, each overriding user-dirs.dirs.
XDG_FOLDER_VARIABLES = (
    "XDG_DESKTOP_DIR",
    "XDG_DOCUMENTS_DIR",
    "XDG_DOWNLOAD_DIR",
    "XDG_MUSIC_DIR",
    "XDG_PICTURES_DIR",
    "XDG_VIDEOS_DIR",
)


@fixture
def home(tmp_path: Path, monkeypatch: MonkeyPatch) -> Path:
    """A home directory of the test's own, with no XDG settings but where they are kept."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))

    for variable in XDG_FOLDER_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    return tmp_path


def test_the_default_folders_on_macos_are_the_users_own(home: Path) -> None:
    dirs = MacOS(appname=APP_NAME, appauthor=False)

    assert default_local_folders(dirs) == tuple(
        home / name for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Movies")
    )


def test_the_default_folders_on_linux_without_user_dirs_are_in_the_home_directory(
    home: Path,
) -> None:
    dirs = Unix(appname=APP_NAME, appauthor=False)

    assert default_local_folders(dirs) == tuple(
        home / name for name in ("Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos")
    )


def test_the_default_folders_on_linux_are_those_user_dirs_name(home: Path) -> None:
    (home / ".config").mkdir()
    (home / ".config" / "user-dirs.dirs").write_text(
        "# Written by xdg-user-dirs-update\n"
        'XDG_DESKTOP_DIR="$HOME/Schreibtisch"\n'
        'XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n'
        'XDG_DOWNLOAD_DIR="$HOME/Downloads"\n'
        'XDG_MUSIC_DIR="$HOME/Musik"\n'
        'XDG_PICTURES_DIR="$HOME/Bilder"\n'
        'XDG_VIDEOS_DIR="/media/videos"\n',
        encoding="utf-8",
    )
    dirs = Unix(appname=APP_NAME, appauthor=False)

    assert default_local_folders(dirs) == (
        home / "Schreibtisch",
        home / "Dokumente",
        home / "Downloads",
        home / "Musik",
        home / "Bilder",
        Path("/media/videos"),
    )
