"""Tests for writing bundles back out as JSON."""

from __future__ import annotations
from json import loads

from pytest import mark

from libranet.bundle.parsing import decode_bundle, parse_bundle
from libranet.bundle.serialization import bundle_value, encode_bundle
from libranet.bundle.shapes import (
    Bundle,
    DirectoryBundle,
    DirectoryMarker,
    FileBundle,
    Metadata,
    Symlink,
)

PART = "sha256/" + "a" * 64
OTHER_PART = "sha256/" + "b" * 64
WHOLE = "c" * 64
FULL_METADATA = Metadata(
    created="2026-08-01T12:00:00Z",
    modified="2026-09-01T08:30:00Z",
    size=4096,
    writable=True,
    executable=True,
    algorithm="sha256",
    hash=WHOLE,
)
FILE = FileBundle((PART, OTHER_PART), FULL_METADATA, ((PART,), (PART, OTHER_PART)))
DIRECTORY = DirectoryBundle(
    {
        "README.md": FileBundle((PART,), Metadata(size=128)),
        "docs/spec.md": FILE,
        "docs/old-spec-link": Symlink("spec.md"),
        "docs": DirectoryMarker(Metadata(created="2026-01-01T00:00:00Z")),
        "empty": DirectoryMarker(),
        "removed.txt": None,
        "café/\ud800.txt": FileBundle(()),
    },
    Metadata(created="2026-01-01T00:00:00Z"),
    (PART,),
    (OTHER_PART,),
)


@mark.parametrize(
    "bundle",
    [
        FILE,
        FileBundle(()),
        DIRECTORY,
        DirectoryBundle({}),
        Symlink("../elsewhere"),
        DirectoryMarker(FULL_METADATA),
        DirectoryMarker(),
    ],
)
def test_a_bundle_reads_back_as_itself(bundle: Bundle) -> None:
    assert parse_bundle(bundle_value(bundle)) == bundle
    assert decode_bundle(encode_bundle(bundle)) == bundle


def test_defaults_are_left_out() -> None:
    assert bundle_value(FileBundle((PART,))) == {"contents": [PART]}
    assert bundle_value(DirectoryBundle({"a": None})) == {"contents": {"a": None}}
    assert bundle_value(DirectoryMarker()) == {}
    assert bundle_value(DirectoryMarker(Metadata(size=0))) == {"metadata": {"size": 0}}


def test_every_field_is_written_as_the_specification_names_it() -> None:
    assert bundle_value(FILE) == {
        "metadata": {
            "created": "2026-08-01T12:00:00Z",
            "modified": "2026-09-01T08:30:00Z",
            "size": 4096,
            "writable": True,
            "executable": True,
            "algorithm": "sha256",
            "hash": WHOLE,
        },
        "contents": [PART, OTHER_PART],
        "versions": [[PART], [PART, OTHER_PART]],
    }
    assert loads(encode_bundle(DIRECTORY))["extensions"] == [OTHER_PART]


def test_encoding_is_compact_sorted_ascii_and_the_same_every_time() -> None:
    reordered = DirectoryBundle(
        dict(reversed(list(DIRECTORY.entries.items()))),
        DIRECTORY.metadata,
        DIRECTORY.versions,
        DIRECTORY.extensions,
    )
    encoded = encode_bundle(DIRECTORY)

    assert encoded == encode_bundle(reordered)
    assert encoded.isascii()
    assert b" " not in encoded
    assert encoded.startswith(b'{"contents":{"README.md":')
