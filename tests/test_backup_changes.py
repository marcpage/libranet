"""Tests for noticing that a backed-up directory may have changed."""

from __future__ import annotations
from os import chmod, geteuid, stat, symlink, utime
from pathlib import Path
from shutil import copytree
from typing import Callable

from pytest import fixture, mark, raises

from libranet.backup.changes import ChangeDetector, PollingDetector

# 2026-09-01T08:30:00Z
WHOLE_SECOND_NS = 1_788_251_400 * 1_000_000_000

needs_permissions = mark.skipif(geteuid() == 0, reason="root lists directories regardless of mode")


@fixture
def detector() -> ChangeDetector:
    return PollingDetector()


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "docs" / "deep").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "readme.txt").write_bytes(b"read me")
    (root / "docs" / "notes.txt").write_bytes(b"some notes")
    (root / "docs" / "deep" / "leaf.txt").write_bytes(b"a leaf")
    symlink("docs/notes.txt", root / "link")
    return root


def test_an_unchanged_directory_keeps_its_fingerprint(detector: ChangeDetector, tree: Path) -> None:
    assert detector.fingerprint(tree) == detector.fingerprint(tree)


def test_the_fingerprint_describes_the_tree_not_where_it_is(
    detector: ChangeDetector, tree: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "copy"
    copytree(tree, copy, symlinks=True, copy_function=_copy_with_times)
    utime(copy / "docs" / "deep", ns=_times(tree / "docs" / "deep"))
    utime(copy / "docs", ns=_times(tree / "docs"))
    utime(copy / "empty", ns=_times(tree / "empty"))
    utime(copy / "link", ns=_times(tree / "link"), follow_symlinks=False)

    assert detector.fingerprint(copy) == detector.fingerprint(tree)


def _copy_with_times(source: str, destination: str) -> None:
    Path(destination).write_bytes(Path(source).read_bytes())
    utime(destination, ns=_times(Path(source)))


def _times(path: Path) -> tuple[int, int]:
    status = stat(path, follow_symlinks=False)
    return status.st_atime_ns, status.st_mtime_ns


def _grow(tree: Path) -> None:
    (tree / "readme.txt").write_bytes(b"read me, now longer")


def _retime(tree: Path) -> None:
    utime(tree / "docs" / "deep" / "leaf.txt", ns=(WHOLE_SECOND_NS, WHOLE_SECOND_NS))


def _add(tree: Path) -> None:
    (tree / "docs" / "added.txt").write_bytes(b"new")


def _remove(tree: Path) -> None:
    (tree / "docs" / "deep" / "leaf.txt").unlink()


def _rename(tree: Path) -> None:
    (tree / "readme.txt").rename(tree / "README.txt")


def _make_executable(tree: Path) -> None:
    chmod(tree / "readme.txt", 0o755)


def _add_empty_directory(tree: Path) -> None:
    (tree / "empty" / "emptier").mkdir()


def _repoint_link(tree: Path) -> None:
    (tree / "link").unlink()
    symlink("readme.txt", tree / "link")


@mark.parametrize(
    "change",
    [
        _grow,
        _retime,
        _add,
        _remove,
        _rename,
        _make_executable,
        _add_empty_directory,
        _repoint_link,
    ],
)
def test_a_change_changes_the_fingerprint(
    detector: ChangeDetector, tree: Path, change: Callable[[Path], None]
) -> None:
    before = detector.fingerprint(tree)
    change(tree)

    assert detector.fingerprint(tree) != before


def test_contents_changed_at_the_same_size_and_time_go_unnoticed(
    detector: ChangeDetector, tree: Path
) -> None:
    leaf = tree / "docs" / "deep" / "leaf.txt"
    times = _times(leaf)
    before = detector.fingerprint(tree)
    leaf.write_bytes(b"A LEAF")
    utime(leaf, ns=times)

    assert detector.fingerprint(tree) == before


@needs_permissions
def test_no_file_is_read(detector: ChangeDetector, tree: Path) -> None:
    chmod(tree / "readme.txt", 0)

    assert detector.fingerprint(tree) == detector.fingerprint(tree)


def test_symlinks_are_not_followed(detector: ChangeDetector, tree: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    symlink(outside, tree / "elsewhere")
    before = detector.fingerprint(tree)
    (outside / "unrelated.txt").write_bytes(b"not in the tree")

    assert detector.fingerprint(tree) == before


@needs_permissions
def test_a_directory_that_cannot_be_listed_is_noticed_once_it_can_be(
    detector: ChangeDetector, tree: Path
) -> None:
    docs = tree / "docs"
    listed = detector.fingerprint(tree)
    chmod(docs, 0)

    try:
        unlisted = detector.fingerprint(tree)

    finally:
        chmod(docs, 0o755)

    assert unlisted != listed
    assert detector.fingerprint(tree) == listed


@needs_permissions
def test_entries_that_cannot_be_looked_at_are_fingerprinted_as_such(
    detector: ChangeDetector, tree: Path
) -> None:
    docs = tree / "docs"
    chmod(docs, 0o444)  # Its names can be listed, but nothing in it looked at.

    try:
        first = detector.fingerprint(tree)
        second = detector.fingerprint(tree)

    finally:
        chmod(docs, 0o755)

    assert first == second


def test_an_ignored_directory_is_fingerprinted_as_though_absent(tree: Path) -> None:
    before = PollingDetector().fingerprint(tree)
    (tree / "node").mkdir()
    (tree / "node" / "file").write_bytes(b"node's own")

    assert PollingDetector([tree / "node"]).fingerprint(tree) == before


def test_nothing_changing_within_an_ignored_directory_changes_the_fingerprint(
    tree: Path,
) -> None:
    detector = PollingDetector([tree / "docs"])
    before = detector.fingerprint(tree)
    (tree / "docs" / "notes.txt").write_bytes(b"many more notes")
    (tree / "docs" / "added.txt").write_bytes(b"new")

    assert detector.fingerprint(tree) == before


def test_a_directory_within_an_ignored_one_cannot_be_fingerprinted(tree: Path) -> None:
    with raises(FileNotFoundError, match="Ignored"):
        PollingDetector([tree]).fingerprint(tree / "docs")


def test_a_missing_directory_cannot_be_fingerprinted(
    detector: ChangeDetector, tmp_path: Path
) -> None:
    with raises(OSError):
        detector.fingerprint(tmp_path / "missing")
