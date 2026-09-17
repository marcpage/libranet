"""Checking bytes against the content id they claim (HighLevelDesign §4.1.1).

Bytes are valid for an identifier if they hash to it directly, or if they are
a zlib stream whose decompressed content does (HttpApi §8). The raw check
comes first. Decompressed output is hashed as it is produced rather than
collected, so a small compressed body cannot expand into an unbounded amount
of memory.
"""

from __future__ import annotations
from typing import Final
from zlib import decompressobj, error as ZlibError

from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry, Hasher
from libranet.cas.content_id import ContentId

# Most decompressed bytes held in memory at once.
_CHUNK_BYTES: Final = 64 * 1024


def content_matches(
    content_id: ContentId, data: bytes, registry: AlgorithmRegistry = DEFAULT_REGISTRY
) -> bool:
    """Whether ``data`` is ``content_id``'s content, as-is or zlib-compressed.

    A compressed form must be exactly one complete zlib stream: truncated
    streams and trailing bytes are rejected.
    """
    algorithm = registry.get(content_id.algorithm)

    if algorithm.hexdigest(data) == content_id.hash:
        return True

    return _decompressed_hexdigest(data, algorithm.hasher()) == content_id.hash


def _decompressed_hexdigest(data: bytes, hasher: Hasher) -> str | None:
    """The digest of ``data`` decompressed, or ``None`` if it is not a zlib stream."""
    decompressor = decompressobj()

    try:
        chunk = decompressor.decompress(data, _CHUNK_BYTES)

        while chunk:
            hasher.update(chunk)
            chunk = decompressor.decompress(decompressor.unconsumed_tail, _CHUNK_BYTES)

    except ZlibError:
        return None

    if not decompressor.eof or decompressor.unused_data:
        return None

    return hasher.hexdigest()
