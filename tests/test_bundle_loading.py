"""Tests for loading a bundle held in CAS."""

from __future__ import annotations
from json import dumps
from pathlib import Path
from zlib import compress

from pytest import fixture, raises

from libranet.bundle.errors import (
    BundleVerificationError,
    IncorrectPasswordError,
    MalformedBundleError,
    MissingContentError,
    PasswordProtectedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.loading import load_bundle
from libranet.bundle.protection import protect
from libranet.bundle.shapes import DirectoryBundle, FileBundle, Symlink
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

PART = "sha256/" + "a" * 64
BUNDLE_JSON = dumps({"contents": {"index.html": {"contents": [PART]}}}).encode()
BUNDLE_ID = ContentId.for_data(BUNDLE_JSON, "sha256")
EXPECTED = DirectoryBundle(entries={"index.html": FileBundle(parts=(PART,))})
PASSWORD = b"secret"
PROTECTED = protect(BUNDLE_JSON, PASSWORD)
PROTECTED_ID = ContentId.for_data(PROTECTED, "sha256")


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


def test_password_protected_bundle_is_read_with_its_password(store: CasStore) -> None:
    store.write(PROTECTED_ID, PROTECTED)

    assert load_bundle(PROTECTED_ID, store, password=PASSWORD) == EXPECTED


def test_password_protected_bundle_is_refused_with_another_password(store: CasStore) -> None:
    store.write(PROTECTED_ID, PROTECTED)

    with raises(IncorrectPasswordError):
        load_bundle(PROTECTED_ID, store, password=b"wrong")


def test_password_protected_bundle_is_capped_once_decrypted(store: CasStore) -> None:
    padded = dumps({"contents": {}, "padding": " " * (4 << 20)}).encode()
    protected = protect(padded, PASSWORD)
    content_id = ContentId.for_data(protected, "sha256")
    store.write(content_id, protected)

    with raises(UnsupportedBundleError, match="larger than 1048576 bytes"):
        load_bundle(content_id, store, max_bytes=1 << 20, password=PASSWORD)


def test_plain_bundle_is_read_even_with_a_password(store: CasStore) -> None:
    store.write(BUNDLE_ID, BUNDLE_JSON)

    assert load_bundle(BUNDLE_ID, store, password=PASSWORD) == EXPECTED


def test_plain_drop_is_read_without_its_placement_bytes(store: CasStore) -> None:
    drop = BUNDLE_JSON + b"\0nonce"
    content_id = ContentId.for_data(drop, "sha256")
    store.write(content_id, drop)

    assert load_bundle(content_id, store, targeted=True) == EXPECTED


def test_password_protected_drop_is_read_without_its_placement_bytes(store: CasStore) -> None:
    drop = PROTECTED + b"\0nonce"
    content_id = ContentId.for_data(drop, "sha256")
    store.write(content_id, drop)

    assert load_bundle(content_id, store, password=PASSWORD, targeted=True) == EXPECTED


def test_drop_read_as_if_it_were_not_one_is_malformed(store: CasStore) -> None:
    drop = PROTECTED + b"\0nonce"
    content_id = ContentId.for_data(drop, "sha256")
    store.write(content_id, drop)

    with raises(MalformedBundleError):
        load_bundle(content_id, store, password=PASSWORD)
