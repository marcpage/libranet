"""Tests for the rules the bundle shapes hold their own values to when built."""

from __future__ import annotations

from pytest import mark, raises

from libranet.bundle.errors import MalformedBundleError
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    Metadata,
    Symlink,
    XattrValue,
    ancestors,
    is_entry_path,
)

WHOLE_HASH = "c" * 64
PART = "sha256/" + "a" * 64
OTHER_PART = "sha256/" + "b" * 64


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


@mark.parametrize("path", ["a", "docs/spec.md", ".hidden", "a..b/c.", "caf\u00e9"])
def test_an_entry_path_is_relative_with_named_segments(path: str) -> None:
    assert is_entry_path(path)


@mark.parametrize("path", ["", "/etc/hosts", "docs/", "a//b", "./a", "a/../b", "..", "a\0b"])
def test_an_entry_path_has_no_empty_dot_or_dot_dot_segment(path: str) -> None:
    assert not is_entry_path(path)


def test_the_ancestors_of_entry_paths_are_every_directory_above_them() -> None:
    assert ancestors(["a/b/c.txt", "a/d", "top.txt"]) == {"a", "a/b"}
    assert ancestors([]) == set()


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


def test_metadata_keeps_extended_attributes_inline_and_as_parts() -> None:
    xattrs: dict[str, XattrValue] = {
        "user.origin": "aHR0cHM6Ly9leGFtcGxlLm9yZy8=",
        "user.fork": (PART, OTHER_PART),
    }

    assert Metadata(xattrs=xattrs).xattrs == xattrs


@mark.parametrize("name", ["", "user.a\0b", "user.\ud800"])
def test_metadata_refuses_an_extended_attribute_name_that_cannot_be_one(name: str) -> None:
    with raises(MalformedBundleError, match="Extended attribute name"):
        Metadata(xattrs={name: "MQ=="})


@mark.parametrize("value", ["MQ", "M Q==", "not base64!", "\u00e9Q=="])
def test_metadata_refuses_an_inline_value_that_is_not_padded_base64(value: str) -> None:
    with raises(MalformedBundleError, match="not padded base64"):
        Metadata(xattrs={"user.bad": value})


def test_an_inline_value_may_be_empty() -> None:
    assert Metadata(xattrs={"user.empty": ""}).xattrs == {"user.empty": ""}


def test_extended_attribute_parts_are_listed_in_order() -> None:
    metadata = Metadata(xattrs={"user.a": (PART, OTHER_PART), "user.b": "MQ==", "user.c": (PART,)})

    assert metadata.xattr_parts() == (PART, OTHER_PART, PART)
    assert metadata.xattr_parts(lambda name: name != "user.a") == (PART,)
