"""Tests for what ``POST /data/bundles`` accepts."""

from __future__ import annotations
from base64 import b64encode
from hashlib import sha256

from pytest import mark, raises

from libranet.bundle.errors import UnsupportedBundleError
from libranet.bundle.parts import CIPHER, PartPath
from libranet.cas.content_id import ContentId
from libranet.protocol.bundle_requests import (
    BundleEditRequest,
    BytesSource,
    CopySource,
    FileSource,
)
from libranet.protocol.errors import InvalidConfigRequestError

PLAIN = ContentId.for_data(b"a bundle", "sha256")
KEY = sha256(b"a playlist").digest()
ENCRYPTED = PartPath(ContentId.for_data(b"a playlist's ciphertext", "sha256"), KEY)
FILE = ContentId.for_data(b"a film's file bundle", "sha256")


def test_every_source_is_read() -> None:
    asked = BundleEditRequest.from_value(
        {
            "base": str(ENCRYPTED),
            "encrypted": True,
            "add": {
                "Film (2001)": {"from": str(PLAIN), "path": ""},
                "Film (2001)/info.json": {"text": '{"title": "Film"}'},
                "Film (2001)/poster.jpg": {"base64": b64encode(b"\xff\xd8").decode()},
                "Other.mp4": {"file": str(FILE)},
            },
            "remove": ["Old Film (1999)"],
        }
    )

    assert asked == BundleEditRequest(
        ENCRYPTED,
        True,
        {
            "Film (2001)": CopySource(PartPath(PLAIN), ""),
            "Film (2001)/info.json": BytesSource(b'{"title": "Film"}'),
            "Film (2001)/poster.jpg": BytesSource(b"\xff\xd8"),
            "Other.mp4": FileSource(PartPath(FILE)),
        },
        ("Old Film (1999)",),
    )


def test_an_empty_object_asks_for_a_new_empty_bundle() -> None:
    assert BundleEditRequest.from_value({}) == BundleEditRequest()


@mark.parametrize(
    ("base", "encrypted", "encrypts"),
    [
        (None, None, False),
        (PartPath(PLAIN), None, False),
        (ENCRYPTED, None, True),
        (ENCRYPTED, False, False),
        (PartPath(PLAIN), True, True),
        (None, True, True),
    ],
)
def test_a_bundle_is_encrypted_as_asked_or_else_as_its_base_is(
    base: PartPath | None, encrypted: bool | None, encrypts: bool
) -> None:
    assert BundleEditRequest(base, encrypted).encrypts is encrypts


def test_a_source_names_a_path_in_a_bundle_by_its_encrypted_id() -> None:
    asked = BundleEditRequest.from_value(
        {"add": {"Film": {"from": str(ENCRYPTED), "path": "Film (2001)/film.mp4"}}}
    )

    assert asked.add == {"Film": CopySource(ENCRYPTED, "Film (2001)/film.mp4")}


@mark.parametrize(
    "value",
    [
        [],
        {"base": 7},
        {"base": "not an id"},
        {"base": f"{PLAIN}/{CIPHER}/not hex"},
        {"encrypted": "yes"},
        {"add": []},
        {"add": {"film": "sha256/…"}},
        {"add": {"film": {}}},
        {"add": {"film": {"file": 7}}},
        {"add": {"film": {"file": str(FILE), "text": ""}}},
        {"add": {"film": {"from": str(PLAIN)}}},
        {"add": {"film": {"from": str(PLAIN), "path": 7}}},
        {"add": {"film": {"from": str(PLAIN), "path": "../escape"}}},
        {"add": {"film": {"from": str(PLAIN), "path": "a/"}}},
        {"add": {"film": {"text": "\ud800"}}},
        {"add": {"film": {"base64": "not base64!"}}},
        {"add": {"film": {"text": ""}, "": {"text": ""}}},
        {"add": {"a/../b": {"text": ""}}},
        {"add": {"/film": {"text": ""}}},
        {"remove": "film"},
        {"remove": [7]},
        {"remove": ["film/"]},
        {"add": {"film": {"text": ""}}, "remove": ["film"]},
    ],
)
def test_an_unusable_edit_is_refused(value: object) -> None:
    with raises(InvalidConfigRequestError):
        BundleEditRequest.from_value(value)


@mark.parametrize(
    "value",
    [
        {"base": "sha999/00"},
        {"base": f"{PLAIN}/AES128-CBC/{KEY.hex()}"},
        {"add": {"film": {"file": "sha999/00"}}},
    ],
)
def test_an_id_this_node_cannot_read_is_refused_as_unsupported(value: object) -> None:
    with raises(UnsupportedBundleError):
        BundleEditRequest.from_value(value)


def test_a_refusal_names_no_key() -> None:
    with raises(InvalidConfigRequestError) as refused:
        BundleEditRequest.from_value({"base": f"{PLAIN}/{CIPHER}/{KEY.hex()}00"})

    assert KEY.hex() not in str(refused.value)
