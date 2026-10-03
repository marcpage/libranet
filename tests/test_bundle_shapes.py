"""Tests for the rules the bundle shapes hold their own values to when built."""

from __future__ import annotations

from pytest import mark, raises

from libranet.bundle.errors import MalformedBundleError, UnsupportedBundleError
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    FileBundle,
    Metadata,
    Symlink,
    XattrValue,
    ancestors,
    is_entry_path,
)
from libranet.cas.content_id import ContentId

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
    assert Metadata(size_bytes=size).size_bytes == size


def test_metadata_refuses_a_negative_size() -> None:
    with raises(MalformedBundleError, match='"size"'):
        Metadata(size_bytes=-1)


def test_metadata_keeps_a_whole_file_hash() -> None:
    metadata = Metadata(algorithm="sha256", hash=WHOLE_HASH)

    assert (metadata.algorithm, metadata.hash) == ("sha256", WHOLE_HASH)


@mark.parametrize(("algorithm", "hash_value"), [("sha256", None), (None, WHOLE_HASH)])
def test_metadata_needs_algorithm_and_hash_together(
    algorithm: str | None, hash_value: str | None
) -> None:
    with raises(MalformedBundleError, match="together"):
        Metadata(algorithm=algorithm, hash=hash_value)


def test_the_whole_file_id_is_the_hash_checked_and_lower_cased() -> None:
    metadata = Metadata(algorithm="SHA256", hash=WHOLE_HASH.upper())

    assert metadata.whole_file_id() == ContentId("sha256", WHOLE_HASH)
    assert Metadata().whole_file_id() is None


@mark.parametrize(
    ("algorithm", "hash_value", "error"),
    [("md5", "0" * 32, UnsupportedBundleError), ("sha256", "c" * 63, MalformedBundleError)],
)
def test_a_whole_file_hash_this_node_cannot_check_has_no_id(
    algorithm: str, hash_value: str, error: type[Exception]
) -> None:
    metadata = Metadata(algorithm=algorithm, hash=hash_value)

    with raises(error, match="Whole-file hash"):
        metadata.whole_file_id()


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


@mark.parametrize(
    ("sizes", "size"),
    [(None, 4096), ((3072, 1024), 4096), ((3072, 1024), None), ((0, 4096), 4096)],
)
def test_a_file_keeps_part_sizes_one_for_each_part_adding_up_to_its_size(
    sizes: tuple[int, ...] | None, size: int | None
) -> None:
    bundle = FileBundle((PART, OTHER_PART), Metadata(size_bytes=size), part_sizes_bytes=sizes)

    assert bundle.part_sizes_bytes == sizes


def test_a_file_without_parts_may_record_that_it_has_no_part_sizes() -> None:
    assert FileBundle((), Metadata(size_bytes=0), part_sizes_bytes=()).part_sizes_bytes == ()


@mark.parametrize("sizes", [(), (4096,), (2048, 1024, 1024)])
def test_a_file_refuses_part_sizes_not_one_for_each_part(sizes: tuple[int, ...]) -> None:
    with raises(MalformedBundleError, match="one size for each of the 2 parts"):
        FileBundle((PART, OTHER_PART), part_sizes_bytes=sizes)


def test_a_file_refuses_a_negative_part_size() -> None:
    with raises(MalformedBundleError, match="non-negative"):
        FileBundle((PART, OTHER_PART), part_sizes_bytes=(4097, -1))


def test_a_file_refuses_part_sizes_that_do_not_add_up_to_its_size() -> None:
    with raises(MalformedBundleError, match="add up to 4095, not the file's size, 4096"):
        FileBundle((PART, OTHER_PART), Metadata(size_bytes=4096), part_sizes_bytes=(3072, 1023))


def test_a_file_with_only_what_its_bytes_decide_keeps_its_parts_their_sizes_and_its_hash() -> None:
    recorded = FileBundle(
        (PART, OTHER_PART),
        Metadata(
            created="2001-02-03T04:05:06Z",
            modified="2026-09-01T08:30:00Z",
            size_bytes=4096,
            writable=True,
            executable=True,
            algorithm="sha256",
            hash=WHOLE_HASH,
            xattrs={"user.tag": "dGFn"},
        ),
        versions=((OTHER_PART,),),
        part_sizes_bytes=(3072, 1024),
    )

    assert recorded.content_only() == FileBundle(
        (PART, OTHER_PART),
        Metadata(size_bytes=4096, algorithm="sha256", hash=WHOLE_HASH),
        part_sizes_bytes=(3072, 1024),
    )
