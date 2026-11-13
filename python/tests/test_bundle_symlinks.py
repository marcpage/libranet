"""Tests for following an entry path through a bundle's symlinks."""

from __future__ import annotations
from typing import Mapping

from pytest import mark

from libranet.bundle.shapes import DirectoryMarker, Entry, FileBundle, Symlink, ancestors
from libranet.bundle.symlinks import PathEnd, path_reached

INDEX = FileBundle(parts=("sha256/" + "1" * 64,))
SPEC = FileBundle(parts=("sha256/" + "2" * 64,))
README = FileBundle(parts=("sha256/" + "3" * 64,))

ENTRIES: Mapping[str, Entry] = {
    "index.html": INDEX,
    "README": README,
    "docs/spec/v1.html": SPEC,
    "docs/latest": Symlink("spec/v1.html"),
    "home": Symlink("."),
    "escape": Symlink("../outside"),
    "loop-a": Symlink("loop-b"),
    "loop-b": Symlink("loop-a"),
    "to-file-dir": Symlink("README/"),
    "empty": DirectoryMarker(),
    "README/shadowed.txt": SPEC,
}

# Every directory: the root, every path leading to an entry, and the marker.
DIRECTORIES = frozenset({""} | ancestors(ENTRIES) | {"empty"})


@mark.parametrize(
    "path, end",
    [
        ("..", PathEnd.OUTSIDE),
        ("escape", PathEnd.OUTSIDE),
        ("loop-a", PathEnd.TOO_MANY_LINKS),
        ("index.html/more", PathEnd.BENEATH_FILE),
        ("to-file-dir", PathEnd.BENEATH_FILE),
        ("missing.html", PathEnd.NOT_FOUND),
        ("missing/index.html", PathEnd.NOT_FOUND),
    ],
)
def test_a_path_that_reaches_nothing_says_why(path: str, end: PathEnd) -> None:
    assert path_reached(ENTRIES, (), path, DIRECTORIES) is end


def test_a_path_is_followed_from_the_directory_it_starts_in() -> None:
    assert path_reached(ENTRIES, ("docs",), "spec/v1.html") == "docs/spec/v1.html"
    assert path_reached(ENTRIES, ("docs",), "../index.html") == "index.html"
    assert path_reached(ENTRIES, ("docs", "spec"), "../..") == ""
    assert path_reached(ENTRIES, ("docs",), "../..") is PathEnd.OUTSIDE


def test_a_path_in_no_entry_is_a_directory_unless_the_directories_are_given() -> None:
    assert path_reached(ENTRIES, (), "missing/deeper") == "missing/deeper"
    assert path_reached(ENTRIES, (), "missing/deeper", frozenset({""})) is PathEnd.NOT_FOUND


def test_a_path_spelled_like_an_ending_is_still_a_path() -> None:
    assert path_reached({}, (), "outside") == "outside"
    assert path_reached({}, (), "outside") != PathEnd.OUTSIDE
