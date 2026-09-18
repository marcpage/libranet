"""Tests for loading a bundle held in CAS."""

from __future__ import annotations
from json import dumps
from pathlib import Path
from zlib import compress

from pytest import fixture, raises

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.loading import load_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle, Symlink
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

PART = "sha256/" + "a" * 64
BUNDLE_JSON = dumps({"contents": {"index.html": {"contents": [PART]}}}).encode()
BUNDLE_ID = ContentId.for_data(BUNDLE_JSON, "sha256")
EXPECTED = DirectoryBundle(entries={"index.html": FileBundle(parts=(PART,))})


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path, prefix_length=4)


def test_bundle_stored_as_is_is_loaded(store: CasStore) -> None:
    store.write(BUNDLE_ID, BUNDLE_JSON)

    assert load_bundle(BUNDLE_ID, store) == EXPECTED


def test_bundle_stored_compressed_is_loaded(store: CasStore) -> None:
    store.write(BUNDLE_ID, compress(BUNDLE_JSON))

    assert load_bundle(BUNDLE_ID, store) == EXPECTED


def test_any_kind_of_bundle_is_loaded(store: CasStore) -> None:
    data = b'{"contents": "target"}'
    content_id = ContentId.for_data(data, "sha256")
    store.write(content_id, data)

    assert load_bundle(content_id, store) == Symlink("target")


def test_bundle_not_held_is_missing(store: CasStore) -> None:
    with raises(MissingContentError) as caught:
        load_bundle(BUNDLE_ID, store)

    assert caught.value.content_ids == (BUNDLE_ID,)


def test_stored_bytes_that_do_not_match_fail_verification(store: CasStore) -> None:
    store.write(BUNDLE_ID, b'{"contents": {}}')

    with raises(BundleVerificationError):
        load_bundle(BUNDLE_ID, store)


def test_bundle_of_exactly_the_limit_is_loaded(store: CasStore) -> None:
    store.write(BUNDLE_ID, compress(BUNDLE_JSON))

    assert load_bundle(BUNDLE_ID, store, max_bytes=len(BUNDLE_JSON)) == EXPECTED


def test_bundle_past_the_limit_as_stored_is_unsupported(store: CasStore) -> None:
    store.write(BUNDLE_ID, BUNDLE_JSON)

    with raises(UnsupportedBundleError, match="larger than"):
        load_bundle(BUNDLE_ID, store, max_bytes=len(BUNDLE_JSON) - 1)


def test_bundle_past_the_limit_once_decompressed_is_unsupported(store: CasStore) -> None:
    padded = dumps({"contents": {}, "padding": " " * (4 << 20)}).encode()
    content_id = ContentId.for_data(padded, "sha256")
    store.write(content_id, compress(padded, 9))

    with raises(UnsupportedBundleError, match="larger than 1048576 bytes"):
        load_bundle(content_id, store, max_bytes=1 << 20)


def test_password_protected_bundle_is_told_apart(store: CasStore) -> None:
    payload = b"\x8f\x02ciphertext\0PW-SHA256-AES256-CBC"
    content_id = ContentId.for_data(payload, "sha256")
    store.write(content_id, payload)

    with raises(PasswordProtectedBundleError):
        load_bundle(content_id, store)


def test_content_that_is_not_a_bundle_is_malformed(store: CasStore) -> None:
    data = b"just a file"
    content_id = ContentId.for_data(data, "sha256")
    store.write(content_id, data)

    with raises(MalformedBundleError):
        load_bundle(content_id, store)
