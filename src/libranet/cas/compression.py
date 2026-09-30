"""Decompressing what is held or sent as a zlib stream (HttpApi §8).

Content may be stored or sent as itself or as a zlib stream of itself, and a
posted list or a protected bundle may be compressed too. Each is read as
exactly one complete stream: a truncated stream, and bytes after its end,
are refused.

Nothing limits how far a stream expands, so it is never decompressed all at
once without a cap: it is produced a chunk at a time, or whole up to a limit
the caller sets.
"""

from __future__ import annotations
from typing import Final, Iterator
from zlib import decompressobj, error as ZlibError

from libranet.cas.errors import NotZlibStreamError, StreamTooLargeError

# Most decompressed bytes held in memory at once.
CHUNK_BYTES: Final = 64 * 1024

_NOT_A_STREAM: Final = "Not one complete zlib stream"


def decompressed(data: bytes, max_bytes: int) -> bytes:
    """``data``, one complete zlib stream, decompressed whole.

    Decompressing stops just past ``max_bytes``, so a small stream cannot
    expand without bound.

    Raises:
        NotZlibStreamError: ``data`` is not one complete zlib stream.
        StreamTooLargeError: it decompresses to more than ``max_bytes``.
    """
    decompressor = decompressobj()

    try:
        result = decompressor.decompress(data, max_bytes + 1)

    except ZlibError:
        raise NotZlibStreamError(_NOT_A_STREAM) from None

    if len(result) > max_bytes:
        raise StreamTooLargeError(f"A zlib stream decompresses to more than {max_bytes} bytes")

    if not decompressor.eof or decompressor.unused_data:
        raise NotZlibStreamError(_NOT_A_STREAM)

    return result


def decompressed_chunks(data: bytes) -> Iterator[bytes]:
    """``data``, one complete zlib stream, decompressed :data:`CHUNK_BYTES` at a time.

    Whether it is one complete stream is known only once all of it has been
    produced, so a caller discards what it consumed if this raises.

    Raises:
        NotZlibStreamError: ``data`` is not one complete zlib stream.
    """
    decompressor = decompressobj()
    pending = data

    while True:
        try:
            chunk = decompressor.decompress(pending, CHUNK_BYTES)

        except ZlibError:
            raise NotZlibStreamError(_NOT_A_STREAM) from None

        if not chunk:
            break

        yield chunk
        pending = decompressor.unconsumed_tail

    if not decompressor.eof or decompressor.unused_data:
        raise NotZlibStreamError(_NOT_A_STREAM)
