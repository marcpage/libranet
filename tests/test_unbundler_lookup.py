"""Tests for finding what an entry path names in a directory bundle."""

from __future__ import annotations
from typing import Mapping

from pytest import mark

from libranet.bundle.shapes import DirectoryMarker, Entry, FileBundle, Symlink
from libranet.unbundler.lookup import (
    MAX_SYMLINK_HOPS,
    FoundDirectory,
    FoundFile,
    ResolvedDirectory,
    look_up,
)

INDEX = FileBundle(parts=("sha256/" + "1" * 64,))
SPEC = FileBundle(parts=("sha256/" + "2" * 64,))
README = FileBundle(parts=("sha256/" + "3" * 64,))

ENTRIES: Mapping[str, Entry] = {
    "index.html": INDEX,
    "README": README,
    "docs/spec/v1.html": SPEC,
    "docs/latest": Symlink("spec/v1.html"),
    "docs/spec-dir": Symlink("spec"),
    "docs/up": Symlink("../README"),
    "docs/chain": Symlink("latest"),
    "home": Symlink("."),
    "escape": Symlink("../outside"),
    "dot-escape": Symlink("home/.."),
    "loop-a": Symlink("loop-b"),
    "loop-b": Symlink("loop-a"),
    "to-file-dir": Symlink("README/"),
    "empty": DirectoryMarker(),
    "README/shadowed.txt": SPEC,
}


def look(path: str) -> FoundFile | FoundDirectory | None:
    return look_up(ResolvedDirectory.of(ENTRIES), path)


def test_directories_are_every_path_leading_to_an_entry_and_every_marker() -> None:
    directory = ResolvedDirectory.of(ENTRIES)

    assert directory.directories == {"", "docs", "docs/spec", "empty", "README"}


@mark.parametrize(
    "path, found",
    [
        ("index.html", FoundFile("index.html", INDEX)),
        ("docs/spec/v1.html", FoundFile("docs/spec/v1.html", SPEC)),
        ("docs", FoundDirectory("docs")),
        ("docs/spec", FoundDirectory("docs/spec")),
        ("empty", FoundDirectory("empty")),
        ("", FoundDirectory("")),
    ],
)
def test_a_path_without_symlinks_is_found_where_it_is(
    path: str, found: FoundFile | FoundDirectory
) -> None:
    assert look(path) == found


@mark.parametrize(
    "path, found",
    [
        ("docs/latest", FoundFile("docs/spec/v1.html", SPEC)),
        ("docs/spec-dir/v1.html", FoundFile("docs/spec/v1.html", SPEC)),
        ("docs/spec-dir", FoundDirectory("docs/spec")),
        ("docs/up", FoundFile("README", README)),
        ("docs/chain", FoundFile("docs/spec/v1.html", SPEC)),
        ("home", FoundDirectory("")),
        ("home/home/docs/latest", FoundFile("docs/spec/v1.html", SPEC)),
        ("docs/spec-dir/../../index.html", FoundFile("index.html", INDEX)),
    ],
)
def test_symlinks_are_followed_to_where_they_lead(
    path: str, found: FoundFile | FoundDirectory
) -> None:
    assert look(path) == found


def test_dot_dot_after_a_symlink_climbs_from_where_it_led() -> None:
    # "docs/spec-dir" is "docs/spec", so ".." from it is "docs", not the root.
    assert look("docs/spec-dir/../latest") == FoundFile("docs/spec/v1.html", SPEC)


@mark.parametrize(
    "path",
    [
        "missing.html",
        "docs/missing.html",
        "missing/index.html",
        "index.html/more",
        "README/shadowed.txt",
        "to-file-dir",
        "..",
        "docs/../../index.html",
        "escape",
        "dot-escape",
        "loop-a",
    ],
)
def test_a_path_naming_nothing_is_not_found(path: str) -> None:
    assert look(path) is None


def test_following_too_many_symlinks_finds_nothing() -> None:
    within = "/".join(["home"] * MAX_SYMLINK_HOPS + ["index.html"])
    beyond = "/".join(["home"] * (MAX_SYMLINK_HOPS + 1) + ["index.html"])

    assert look(within) == FoundFile("index.html", INDEX)
    assert look(beyond) is None


def test_an_empty_directory_has_only_its_root() -> None:
    directory = ResolvedDirectory.of({})

    assert look_up(directory, "") == FoundDirectory("")
    assert look_up(directory, "index.html") is None
