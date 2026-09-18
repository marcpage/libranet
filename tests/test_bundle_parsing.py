"""Tests for telling bundles apart by shape and checking that shape."""

from __future__ import annotations
from json import dumps

from pytest import mark, raises

from libranet.bundle.errors import (
    MalformedBundleError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.shapes import (
    DirectoryBundle,
    DirectoryMarker,
    FileBundle,
    Metadata,
    Symlink,
)

PART_A = "sha256/" + "a" * 64
PART_B = "sha1/" + "b" * 40
WHOLE_HASH = "c" * 64

FILE_BUNDLE = {
    "metadata": {
        "created": "2026-08-01T12:00:00Z",
        "modified": "2026-09-01T08:30:00Z",
        "size": 4096,
        "writable": True,
        "executable": False,
        "algorithm": "sha256",
        "hash": WHOLE_HASH,
    },
    "contents": [PART_A, PART_B],
    "versions": [[PART_A, PART_B]],
}

DIRECTORY_BUNDLE = {
    "metadata": {"created": "2026-01-01T00:00:00Z"},
    "contents": {
        "README.md": {
            "metadata": {"size": 128, "algorithm": "sha256", "hash": WHOLE_HASH},
            "contents": [PART_A],
        },
        "docs/Specification.md": {"contents": [PART_A, PART_A]},
        "docs/old-spec-link": {"contents": "Specification.md"},
        "docs": {"metadata": {"created": "2026-01-01T00:00:00Z"}},
        "gone.txt": None,
    },
    "versions": [PART_A],
    "extensions": [PART_A, PART_B],
}


def test_file_bundle_is_told_apart_by_a_contents_array() -> None:
    assert parse_bundle(FILE_BUNDLE) == FileBundle(
        parts=(PART_A, PART_B),
        metadata=Metadata(
            created="2026-08-01T12:00:00Z",
            modified="2026-09-01T08:30:00Z",
            size=4096,
            writable=True,
            executable=False,
            algorithm="sha256",
            hash=WHOLE_HASH,
        ),
        versions=((PART_A, PART_B),),
    )


def test_file_bundle_needs_only_its_contents() -> None:
    assert parse_bundle({"contents": []}) == FileBundle(parts=())


def test_writable_and_executable_default_to_false() -> None:
    bundle = parse_bundle({"metadata": {"size": 1}, "contents": [PART_A]})

    assert isinstance(bundle, FileBundle)
    assert bundle.metadata == Metadata(size=1, writable=False, executable=False)


def test_directory_bundle_is_told_apart_by_a_contents_object() -> None:
    assert parse_bundle(DIRECTORY_BUNDLE) == DirectoryBundle(
        entries={
            "README.md": FileBundle(
                parts=(PART_A,),
                metadata=Metadata(size=128, algorithm="sha256", hash=WHOLE_HASH),
            ),
            "docs/Specification.md": FileBundle(parts=(PART_A, PART_A)),
            "docs/old-spec-link": Symlink("Specification.md"),
            "docs": DirectoryMarker(Metadata(created="2026-01-01T00:00:00Z")),
            "gone.txt": None,
        },
        metadata=Metadata(created="2026-01-01T00:00:00Z"),
        versions=(PART_A,),
        extensions=(PART_A, PART_B),
    )


def test_empty_directory_bundle() -> None:
    assert parse_bundle({"contents": {}}) == DirectoryBundle(entries={})


def test_symlink_is_told_apart_by_a_contents_string() -> None:
    assert parse_bundle({"contents": "../README.md"}) == Symlink("../README.md")


def test_object_without_contents_is_a_directory_marker() -> None:
    assert parse_bundle({}) == DirectoryMarker()
    assert parse_bundle({"metadata": {"modified": "2026-01-01T00:00:00Z"}}) == DirectoryMarker(
        Metadata(modified="2026-01-01T00:00:00Z")
    )


def test_unknown_fields_are_ignored() -> None:
    bundle = parse_bundle(
        {
            "contents": [PART_A],
            "future": {"anything": [1, 2]},
            "metadata": {"content-type": "text/html", "size": 3},
        }
    )

    assert bundle == FileBundle(parts=(PART_A,), metadata=Metadata(size=3))


def test_paths_differing_only_in_case_are_separate_entries() -> None:
    bundle = parse_bundle({"contents": {"README.md": {}, "readme.md": {"contents": "x"}}})

    assert isinstance(bundle, DirectoryBundle)
    assert bundle.entries == {"README.md": DirectoryMarker(), "readme.md": Symlink("x")}


def test_signed_bundle_is_unsupported() -> None:
    signed = {
        "signer": PART_A,
        "algorithm": "sha256",
        "hash": WHOLE_HASH,
        "signature": "MEUCIQ",
        "contents": dumps({"contents": [PART_A]}),
    }

    with raises(UnsupportedBundleError, match="Signed"):
        parse_bundle(signed)


def test_signed_directory_entry_is_unsupported() -> None:
    signed_entry = {"signature": "MEUCIQ", "contents": dumps({"contents": [PART_A]})}

    with raises(UnsupportedBundleError, match="'app.js'.*Signed"):
        parse_bundle({"contents": {"app.js": signed_entry}})


@mark.parametrize("value", [[], "text", 1, None, True])
def test_bundle_must_be_an_object(value: object) -> None:
    with raises(MalformedBundleError, match="JSON object"):
        parse_bundle(value)


@mark.parametrize("contents", [None, 1, 1.5, True])
def test_contents_of_another_type_is_malformed(contents: object) -> None:
    with raises(MalformedBundleError, match='"contents"'):
        parse_bundle({"contents": contents})


@mark.parametrize("contents", [[1], [PART_A, None], [[PART_A]]])
def test_file_parts_must_be_strings(contents: list[object]) -> None:
    with raises(MalformedBundleError, match='"contents"'):
        parse_bundle({"contents": contents})


@mark.parametrize("metadata", [None, [], "text"])
def test_metadata_must_be_an_object(metadata: object) -> None:
    with raises(MalformedBundleError, match='"metadata"'):
        parse_bundle({"contents": [], "metadata": metadata})


@mark.parametrize(
    "metadata",
    [
        {"created": 1},
        {"modified": None},
        {"size": -1},
        {"size": True},
        {"size": 1.5},
        {"size": "4096"},
        {"size": None},
        {"writable": "yes"},
        {"executable": None},
        {"executable": 1},
        {"algorithm": 1, "hash": WHOLE_HASH},
        {"algorithm": "sha256", "hash": None},
    ],
)
def test_metadata_field_of_the_wrong_type_is_malformed(metadata: dict[str, object]) -> None:
    with raises(MalformedBundleError, match="must be"):
        parse_bundle({"contents": [], "metadata": metadata})


@mark.parametrize("metadata", [{"algorithm": "sha256"}, {"hash": WHOLE_HASH}])
def test_whole_file_algorithm_and_hash_come_together(metadata: dict[str, object]) -> None:
    with raises(MalformedBundleError, match="together"):
        parse_bundle({"contents": [], "metadata": metadata})


@mark.parametrize("versions", [PART_A, [PART_A], [[1]], None])
def test_file_versions_must_be_arrays_of_cas_paths(versions: object) -> None:
    with raises(MalformedBundleError, match='"versions"'):
        parse_bundle({"contents": [], "versions": versions})


@mark.parametrize("key", ["versions", "extensions"])
@mark.parametrize("value", [PART_A, [[PART_A]], [1], None])
def test_directory_versions_and_extensions_must_be_arrays_of_cas_paths(
    key: str, value: object
) -> None:
    with raises(MalformedBundleError, match=f'"{key}"'):
        parse_bundle({"contents": {}, key: value})


@mark.parametrize("entry", ["text", [PART_A], 1, True])
def test_directory_entry_must_be_an_object_or_null(entry: object) -> None:
    with raises(MalformedBundleError, match="'index.html'.*JSON object"):
        parse_bundle({"contents": {"index.html": entry}})


def test_directory_entry_cannot_be_a_directory_bundle() -> None:
    with raises(MalformedBundleError, match="'docs'.*cannot itself be a directory"):
        parse_bundle({"contents": {"docs": {"contents": {"a.txt": {"contents": []}}}}})


def test_malformed_directory_entry_is_named() -> None:
    with raises(MalformedBundleError, match="'docs/a.txt'.*\"size\""):
        parse_bundle({"contents": {"docs/a.txt": {"contents": [], "metadata": {"size": "1"}}}})


@mark.parametrize(
    "path",
    [
        "a",
        "docs/Specification.md",
        "a/b/c",
        ".hidden",
        "..hidden",
        "a..b",
        "a b",
        "ünïcödé",
        "back\\slash",
    ],
)
def test_relative_entry_paths_are_kept_as_written(path: str) -> None:
    bundle = parse_bundle({"contents": {path: {}}})

    assert isinstance(bundle, DirectoryBundle)
    assert list(bundle.entries) == [path]


@mark.parametrize(
    "path",
    [
        "",
        "/etc/passwd",
        "docs/",
        "a//b",
        ".",
        "./a",
        "a/./b",
        "..",
        "../a",
        "a/../../b",
        "a/..",
        "a\0b",
    ],
)
def test_entry_path_that_could_leave_its_directory_is_malformed(path: str) -> None:
    with raises(MalformedBundleError, match="Entry path"):
        parse_bundle({"contents": {path: {}}})


@mark.parametrize("target", ["Specification.md", "../README.md", "./x", "a//b", ".."])
def test_relative_symlink_targets_are_kept_as_written(target: str) -> None:
    assert parse_bundle({"contents": target}) == Symlink(target)


@mark.parametrize("target", ["", "/etc/passwd", "a\0b"])
def test_symlink_target_must_be_a_non_empty_relative_path(target: str) -> None:
    with raises(MalformedBundleError, match="Symlink target"):
        parse_bundle({"contents": target})


def test_decode_reads_json_bytes() -> None:
    assert decode_bundle(dumps(FILE_BUNDLE).encode()) == parse_bundle(FILE_BUNDLE)


def test_decode_reads_utf8_paths() -> None:
    data = dumps({"contents": {"naïve.txt": {}}}, ensure_ascii=False).encode()

    assert decode_bundle(data) == DirectoryBundle(entries={"naïve.txt": DirectoryMarker()})


def test_decode_tells_apart_password_protected_bundle() -> None:
    payload = b"\x8f\x02\xa7ciphertext\xff" + b"\0" + b"PW-SHA256-AES256-CBC"

    with raises(PasswordProtectedBundleError):
        decode_bundle(payload)


def test_password_protected_bundle_is_unsupported() -> None:
    with raises(UnsupportedBundleError):
        decode_bundle(b"ciphertext\0PW-SHA256-AES256-CBC")


@mark.parametrize(
    "data",
    [
        b"",
        b"not json",
        b'{"contents": [',
        b"\xff\xfe\x8f",
        b"\xef\xbb\xbf" + dumps({"contents": []}).encode(),  # UTF-8 byte-order mark
    ],
)
def test_decode_refuses_data_that_is_neither_json_nor_protected(data: bytes) -> None:
    with raises(MalformedBundleError, match="neither JSON nor password-protected"):
        decode_bundle(data)


def test_decode_refuses_json_nested_past_the_recursion_limit() -> None:
    depth = 100_000

    with raises(MalformedBundleError):
        decode_bundle(b"[" * depth + b"]" * depth)


def test_decode_checks_the_shape_of_the_json() -> None:
    with raises(MalformedBundleError, match="JSON object"):
        decode_bundle(b"[1, 2]")
