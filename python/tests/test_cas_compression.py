"""Tests for reading zlib streams, whole up to a limit or a chunk at a time."""

from __future__ import annotations
from os import urandom
from zlib import compress, compressobj

from pytest import mark, raises

from libranet.cas.compression import CHUNK_BYTES, decompressed, decompressed_chunks
from libranet.cas.errors import NotZlibStreamError, StreamTooLargeError

CONTENT = b"libranet content " * 64


def test_a_stream_is_decompressed_whole() -> None:
    assert decompressed(compress(CONTENT), len(CONTENT)) == CONTENT


def test_an_empty_stream_decompresses_to_nothing() -> None:
    assert decompressed(compress(b""), 0) == b""


def test_a_stream_past_the_limit_is_too_large() -> None:
    with raises(StreamTooLargeError, match=str(len(CONTENT) - 1)):
        decompressed(compress(CONTENT), len(CONTENT) - 1)


def test_a_small_stream_is_never_expanded_far_past_the_limit() -> None:
    bomb = compress(b"\0" * (64 << 20), 9)

    with raises(StreamTooLargeError):
        decompressed(bomb, 1024)


@mark.parametrize(
    "data",
    [
        b"not a zlib stream",
        b"",
        compress(CONTENT)[:-4],
        compress(CONTENT) + b"extra",
    ],
)
def test_anything_but_one_complete_stream_is_refused(data: bytes) -> None:
    with raises(NotZlibStreamError):
        decompressed(data, len(CONTENT))

    with raises(NotZlibStreamError):
        list(decompressed_chunks(data))


def test_raw_deflate_and_gzip_are_not_zlib() -> None:
    for wbits in (-15, 31):
        encoder = compressobj(wbits=wbits)
        encoded = encoder.compress(CONTENT) + encoder.flush()

        with raises(NotZlibStreamError):
            decompressed(encoded, len(CONTENT))


@mark.parametrize(
    "content",
    [
        b"\0" * (4 * CHUNK_BYTES + 1),
        urandom(3 * CHUNK_BYTES),
        (urandom(1000) + b"a" * 70_000) * 4,
        CONTENT,
        b"",
    ],
)
def test_chunks_are_the_content_in_order_and_none_is_larger_than_a_chunk(content: bytes) -> None:
    chunks = list(decompressed_chunks(compress(content, 9)))

    assert b"".join(chunks) == content
    assert all(0 < len(chunk) <= CHUNK_BYTES for chunk in chunks)


def test_chunks_are_produced_before_a_truncated_stream_is_refused() -> None:
    content = urandom(3 * CHUNK_BYTES)
    chunks = decompressed_chunks(compress(content)[:-4])

    assert next(chunks) == content[:CHUNK_BYTES]

    with raises(NotZlibStreamError):
        list(chunks)
