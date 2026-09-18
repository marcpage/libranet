"""Tests for reading the CAS content a bundle names."""

from __future__ import annotations
from os import urandom
from pathlib import Path
from zlib import compress

from pytest import fixture, mark, raises

from libranet.bundle.content import content_chunks, parse_cas_path
from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore

CONTENT = b"libranet part " * 64
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")


@fixture
def store(tmp_path: Path) -> CasStore:
    return CasStore(tmp_path, prefix_length=4)


def test_cas_path_names_content() -> None:
    assert parse_cas_path(str(CONTENT_ID)) == CONTENT_ID


def test_cas_path_hash_is_lower_cased() -> None:
    assert parse_cas_path(f"sha256/{CONTENT_ID.hash.upper()}") == CONTENT_ID


def test_cas_path_under_an_unknown_algorithm_is_unsupported() -> None:
    with raises(UnsupportedBundleError, match="blake3"):
        parse_cas_path("blake3/" + "a" * 64)


def test_per_entry_encrypted_cas_path_is_unsupported() -> None:
    with raises(UnsupportedBundleError, match="§7"):
        parse_cas_path(f"{CONTENT_ID}/AES256-CBC/{'a1' * 32}")


@mark.parametrize(
    "path",
    [
        "",
        "sha256",
        "sha256/",
        "sha256/zz",
        f"/data/{CONTENT_ID}",
        f"{CONTENT_ID}/extra",
        f"{CONTENT_ID}/AES256-CBC/{'a1' * 32}/extra",
        "sha256/zz/AES256-CBC/a1",
    ],
)
def test_malformed_cas_path(path: str) -> None:
    with raises(MalformedBundleError):
        parse_cas_path(path)


def test_raw_content_is_read_as_stored(store: CasStore) -> None:
    store.write(CONTENT_ID, CONTENT)

    assert list(content_chunks(store, CONTENT_ID)) == [CONTENT]


def test_empty_content_is_read(store: CasStore) -> None:
    empty_id = ContentId.for_data(b"", "sha256")
    store.write(empty_id, b"")

    assert b"".join(content_chunks(store, empty_id)) == b""


def test_compressed_content_is_decompressed(store: CasStore) -> None:
    store.write(CONTENT_ID, compress(CONTENT))

    assert b"".join(content_chunks(store, CONTENT_ID)) == CONTENT


@mark.parametrize(
    "content",
    [
        b"\0" * (4 << 20),  # far larger than one chunk
        urandom(200_000),  # incompressible
        (urandom(1000) + b"a" * 70_000) * 4,
    ],
)
def test_large_compressed_content_arrives_in_bounded_chunks(
    store: CasStore, content: bytes
) -> None:
    content_id = ContentId.for_data(content, "sha256")
    store.write(content_id, compress(content, 9))

    chunks = list(content_chunks(store, content_id))

    assert b"".join(chunks) == content
    assert max(len(chunk) for chunk in chunks) <= 64 * 1024


def test_content_that_happens_to_be_zlib_is_read_as_stored(store: CasStore) -> None:
    zlib_file = compress(b"a file that is itself a zlib stream")
    content_id = ContentId.for_data(zlib_file, "sha256")
    store.write(content_id, zlib_file)

    assert b"".join(content_chunks(store, content_id)) == zlib_file


def test_content_not_held_is_missing(store: CasStore) -> None:
    with raises(MissingContentError) as caught:
        list(content_chunks(store, CONTENT_ID))

    assert caught.value.content_ids == (CONTENT_ID,)
    assert str(CONTENT_ID) in str(caught.value)


@mark.parametrize(
    "stored",
    [
        b"other content",
        compress(b"other content"),
        compress(CONTENT)[:-4],  # truncated
        compress(CONTENT) + b"trailing",
        compress(compress(CONTENT)),
        b"",
    ],
)
def test_stored_bytes_that_do_not_match_fail_verification(store: CasStore, stored: bytes) -> None:
    store.write(CONTENT_ID, stored)

    with raises(BundleVerificationError, match=str(CONTENT_ID)):
        b"".join(content_chunks(store, CONTENT_ID))
