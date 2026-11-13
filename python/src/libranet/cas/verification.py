"""Checking bytes against the content id they claim (HighLevelDesign §4.1.1).

Bytes are valid for an identifier if they hash to it directly, or if they are
a zlib stream whose decompressed content does (HttpApi §8). The raw check
comes first. Decompressed output is hashed as it is produced rather than
collected, so a small compressed body cannot expand into an unbounded amount
of memory.

The same check reads every object a bundle names, and a part decrypted
under its key, which is the part's own hash (BundleSpecification §7.2), so
it is made here alone (:func:`matching_chunks`).
"""

from __future__ import annotations
from typing import Iterator

from libranet.cas.algorithms import DEFAULT_REGISTRY, AlgorithmRegistry, HashAlgorithm
from libranet.cas.compression import decompressed_chunks
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentMismatchError, NotZlibStreamError


def content_matches(
    content_id: ContentId, data: bytes, registry: AlgorithmRegistry = DEFAULT_REGISTRY
) -> bool:
    """Whether ``data`` is ``content_id``'s content, as-is or zlib-compressed.

    A compressed form must be exactly one complete zlib stream: truncated
    streams and trailing bytes are rejected.
    """
    try:
        for _ in matching_chunks(data, registry.get(content_id.algorithm), content_id.hash):
            pass

    except ContentMismatchError:
        # Not logged: data that does not match is the answer, as callers log.
        return False

    return True


def matching_chunks(data: bytes, algorithm: HashAlgorithm, expected: str) -> Iterator[bytes]:
    """The content ``data`` holds, which ``algorithm`` must hash to the hex ``expected``.

    That is ``data`` itself, if it hashes so, and otherwise what it
    decompresses to, produced a chunk at a time. That is checked only once
    all of it has been produced, so a caller discards what it consumed if
    this raises.

    Raises:
        ContentMismatchError: ``data`` is neither the content nor one
            complete zlib stream of it.
    """
    if algorithm.hexdigest(data) == expected:
        yield data
        return

    hasher = algorithm.hasher()

    try:
        for chunk in decompressed_chunks(data):
            hasher.update(chunk)
            yield chunk

    except NotZlibStreamError:
        raise ContentMismatchError("Neither the content nor a zlib stream of it") from None

    if hasher.hexdigest() != expected:
        raise ContentMismatchError("Decompresses to other content")
