"""Reading the CAS content a bundle names.

A bundle names content by CAS path, ``{algorithm}/{hash}`` (BundleSpecification
§2). What is stored under it may be that content or a zlib stream of it
(HttpApi §8), and only its hash tells which, so every read is checked against
its identifier. Decompressed content is produced a chunk at a time, since the
protocol sets no limit on its size.
"""

from __future__ import annotations
from typing import Final, Iterator, Protocol
from zlib import decompressobj, error as ZlibError

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError, InvalidContentIdError, UnknownAlgorithmError

# Most decompressed bytes held in memory at once.
_CHUNK_BYTES: Final = 64 * 1024

_SEPARATOR: Final = "/"
_PLAIN_PATH_SEGMENTS: Final = 2

# A per-entry encrypted path adds two segments to the stored ciphertext's
# (BundleSpecification §7):
# {hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}.
_ENCRYPTED_PATH_SEGMENTS: Final = 4


class ContentSource(Protocol):
    """Where a bundle's content is read from, such as the source of truth."""

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is held."""
        ...

    def read(self, content_id: ContentId) -> bytes:
        """The bytes stored for ``content_id``.

        Raises:
            ContentNotFoundError: ``content_id`` is not held.
        """
        ...


def parse_cas_path(path: str) -> ContentId:
    """The content a bundle names by the CAS path ``path``.

    Raises:
        UnsupportedBundleError: ``path`` is per-entry encrypted (§7), or names
            a hash algorithm this node lacks.
        MalformedBundleError: ``path`` is not a CAS path.
    """
    segments = path.split(_SEPARATOR)

    try:
        content_id = ContentId.parse(_SEPARATOR.join(segments[:_PLAIN_PATH_SEGMENTS]))

    except UnknownAlgorithmError as error:
        raise UnsupportedBundleError(str(error)) from None

    except InvalidContentIdError as error:
        raise MalformedBundleError(str(error)) from None

    if len(segments) == _ENCRYPTED_PATH_SEGMENTS:
        raise UnsupportedBundleError(f"Per-entry encryption (§7) is not supported: {path!r}")

    if len(segments) != _PLAIN_PATH_SEGMENTS:
        raise MalformedBundleError(f"Not a CAS path: {path!r}")

    return content_id


def content_chunks(source: ContentSource, content_id: ContentId) -> Iterator[bytes]:
    """The content held for ``content_id``, decompressed if stored compressed.

    Compressed content is checked only once all of it has been produced, so
    a caller discards what it consumed if this raises.

    Raises:
        MissingContentError: ``source`` does not hold ``content_id``.
        BundleVerificationError: what is stored is neither the content nor
            one complete zlib stream of it.
    """
    try:
        data = source.read(content_id)

    except ContentNotFoundError:
        raise MissingContentError((content_id,)) from None

    algorithm = DEFAULT_REGISTRY.get(content_id.algorithm)

    if algorithm.hexdigest(data) == content_id.hash:
        yield data
        return

    hasher = algorithm.hasher()
    decompressor = decompressobj()
    pending = data

    while True:
        try:
            chunk = decompressor.decompress(pending, _CHUNK_BYTES)

        except ZlibError:
            raise BundleVerificationError(f"Stored content does not match {content_id}") from None

        if not chunk:
            break

        hasher.update(chunk)
        yield chunk
        pending = decompressor.unconsumed_tail

    if not decompressor.eof or decompressor.unused_data or hasher.hexdigest() != content_id.hash:
        raise BundleVerificationError(f"Stored content does not match {content_id}")
