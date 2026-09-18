"""Tests for the rules the bundle shapes hold their own values to when built."""

from __future__ import annotations

from pytest import mark, raises

from libranet.bundle.errors import MalformedBundleError
from libranet.bundle.shapes import DirectoryBundle, DirectoryMarker, Metadata, Symlink

WHOLE_HASH = "c" * 64


@mark.parametrize("target", ["Specification.md", "../README.md", "./x", "a//b", ".."])
def test_symlink_keeps_a_relative_target(target: str) -> None:
    assert Symlink(target).target == target


@mark.parametrize("target", ["", "/etc/hosts", "a\0b"])
def test_symlink_refuses_a_target_that_is_not_relative(target: str) -> None:
    with raises(MalformedBundleError, match="Symlink target"):
        Symlink(target)


def test_directory_keeps_relative_entry_paths() -> None:
    entries = {"a": None, "docs/spec.md": Symlink("x"), ".hidden": DirectoryMarker()}

    assert DirectoryBundle(entries=entries).entries == entries


@mark.parametrize("path", ["", "/etc/hosts", "docs/", "a//b", "./a", "a/../b", "..", "a\0b"])
def test_directory_refuses_an_entry_path_that_could_leave_it(path: str) -> None:
    with raises(MalformedBundleError, match="Entry path"):
        DirectoryBundle(entries={"fine.txt": None, path: None})


@mark.parametrize("size", [None, 0, 4096])
def test_metadata_keeps_a_size_that_is_not_negative(size: int | None) -> None:
    assert Metadata(size=size).size == size


def test_metadata_refuses_a_negative_size() -> None:
    with raises(MalformedBundleError, match='"size"'):
        Metadata(size=-1)


def test_metadata_keeps_a_whole_file_hash() -> None:
    metadata = Metadata(algorithm="sha256", hash=WHOLE_HASH)

    assert (metadata.algorithm, metadata.hash) == ("sha256", WHOLE_HASH)


@mark.parametrize(("algorithm", "hash_value"), [("sha256", None), (None, WHOLE_HASH)])
def test_metadata_needs_algorithm_and_hash_together(
    algorithm: str | None, hash_value: str | None
) -> None:
    with raises(MalformedBundleError, match="together"):
        Metadata(algorithm=algorithm, hash=hash_value)


def test_broken_rule_is_a_value_error() -> None:
    with raises(ValueError):
        Symlink("")
