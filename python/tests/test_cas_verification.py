"""Tests for checking content against its identifier, raw or zlib-compressed."""

from __future__ import annotations
from os import urandom
from zlib import compress, compressobj

from pytest import mark, raises

from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.compression import CHUNK_BYTES
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentMismatchError
from libranet.cas.verification import content_matches, matching_chunks

CONTENT = b"libranet content " * 64
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")


def test_raw_content_matches() -> None:
    assert content_matches(CONTENT_ID, CONTENT)


def test_empty_content_matches_its_own_id() -> None:
    assert content_matches(ContentId.for_data(b"", "sha256"), b"")


@mark.parametrize("level", [0, 1, 9])
def test_zlib_compressed_content_matches(level: int) -> None:
    assert content_matches(CONTENT_ID, compress(CONTENT, level))


@mark.parametrize(
    "content",
    [
        b"\0" * (4 << 20),  # far larger than one decompression chunk
        urandom(200_000),  # incompressible
        (urandom(1000) + b"a" * 70_000) * 4,
        b"",
    ],
)
def test_compressed_content_of_any_shape_matches(content: bytes) -> None:
    assert content_matches(ContentId.for_data(content, "sha256"), compress(content, 9))


def test_other_content_does_not_match() -> None:
    assert not content_matches(CONTENT_ID, b"something else")


def test_compressed_other_content_does_not_match() -> None:
    assert not content_matches(CONTENT_ID, compress(b"something else"))


def test_truncated_zlib_stream_does_not_match() -> None:
    assert not content_matches(CONTENT_ID, compress(CONTENT)[:-4])


def test_zlib_stream_with_trailing_bytes_does_not_match() -> None:
    assert not content_matches(CONTENT_ID, compress(CONTENT) + b"extra")


def test_raw_deflate_and_gzip_are_not_zlib() -> None:
    for wbits in (-15, 31):
        encoder = compressobj(wbits=wbits)
        encoded = encoder.compress(CONTENT) + encoder.flush()

        assert not content_matches(CONTENT_ID, encoded), wbits


def test_compressing_twice_does_not_match() -> None:
    assert not content_matches(CONTENT_ID, compress(compress(CONTENT)))


def test_content_as_it_is_is_produced_whole() -> None:
    algorithm = DEFAULT_REGISTRY.get("sha256")

    assert list(matching_chunks(CONTENT, algorithm, CONTENT_ID.hash)) == [CONTENT]


def test_compressed_content_is_produced_decompressed_a_chunk_at_a_time() -> None:
    content = b"\0" * (CHUNK_BYTES * 3)
    algorithm = DEFAULT_REGISTRY.get("sha256")

    chunks = list(matching_chunks(compress(content), algorithm, algorithm.hexdigest(content)))

    assert b"".join(chunks) == content
    assert max(map(len, chunks)) <= CHUNK_BYTES


@mark.parametrize(
    "data",
    [b"something else", compress(b"something else"), compress(CONTENT)[:-4]],
    ids=["other content", "other content compressed", "truncated stream"],
)
def test_data_that_is_not_the_content_raises_once_produced(data: bytes) -> None:
    with raises(ContentMismatchError):
        list(matching_chunks(data, DEFAULT_REGISTRY.get("sha256"), CONTENT_ID.hash))
