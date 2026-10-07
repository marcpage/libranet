"""Reading the CAS content a bundle names.

A bundle names content by CAS path, ``{algorithm}/{hash}`` (BundleSpecification
§2). What is stored under it may be that content or a zlib stream of it
(HttpApi §8), and only its hash tells which, so every read is checked against
its identifier. Decompressed content is produced a chunk at a time, since the
protocol sets no limit on its size.
"""

from __future__ import annotations
from typing import Final, Iterable, Iterator, Protocol

from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.cas.errors import (
    ContentMismatchError,
    ContentNotFoundError,
    InvalidContentIdError,
    UnknownAlgorithmError,
)
from libranet.cas.verification import matching_chunks

#: What separates a CAS path's segments, plain or encrypted.
CAS_PATH_SEPARATOR: Final = "/"

#: A plain CAS path's segments: ``{hash algorithm}/{hash}``.
PLAIN_PATH_SEGMENTS: Final = 2

#: A per-entry encrypted path adds two segments to the stored ciphertext's
#: (BundleSpecification §7):
#: ``{hash algorithm}/{encrypted data hash}/{encryption algorithm}/{encryption key}``.
ENCRYPTED_PATH_SEGMENTS: Final = 4


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


def normalize_cas_path(path: str) -> str:
    """``path`` with its hash algorithm and hash lower-cased (HttpApi §5.4).

    Nothing is checked, since a path is parsed only when followed. The
    segments a per-entry encrypted path adds (§7) are kept as written, since
    the cipher's name is not a hash.
    """
    segments = path.split(CAS_PATH_SEPARATOR, PLAIN_PATH_SEGMENTS)
    address = [segment.lower() for segment in segments[:PLAIN_PATH_SEGMENTS]]
    return CAS_PATH_SEPARATOR.join(address + segments[PLAIN_PATH_SEGMENTS:])


def parse_cas_path(path: str) -> ContentId:
    """The content a bundle names by the plain CAS path ``path``.

    A per-entry encrypted path (§7), whether it names a part or a bundle, is
    read with its key, by :class:`~libranet.bundle.parts.PartPath`.

    Raises:
        UnsupportedBundleError: ``path`` is per-entry encrypted, or names a
            hash algorithm this node lacks.
        MalformedBundleError: ``path`` is not a CAS path.
    """
    segments = path.split(CAS_PATH_SEPARATOR)

    try:
        content_id = ContentId.parse(CAS_PATH_SEPARATOR.join(segments[:PLAIN_PATH_SEGMENTS]))

    except UnknownAlgorithmError as error:
        raise UnsupportedBundleError(str(error)) from None

    except InvalidContentIdError as error:
        raise MalformedBundleError(str(error)) from None

    if len(segments) == ENCRYPTED_PATH_SEGMENTS:
        # The path is not shown, since its key is what keeps the content unread.
        raise UnsupportedBundleError(
            f"Per-entry encryption (§7) is read with its key, not as plain {content_id}"
        )

    if len(segments) != PLAIN_PATH_SEGMENTS:
        raise MalformedBundleError(f"Not a CAS path: {path!r}")

    return content_id


def check_held(content_ids: Iterable[ContentId], source: ContentSource) -> None:
    """Raise unless ``source`` holds every one of ``content_ids``.

    Raises:
        MissingContentError: some are not held; each is named once, in the
            order given.
    """
    lacked = tuple(part for part in dict.fromkeys(content_ids) if not source.exists(part))

    if lacked:
        raise MissingContentError(lacked)


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

    try:
        yield from matching_chunks(
            data, DEFAULT_REGISTRY.get(content_id.algorithm), content_id.hash
        )

    except ContentMismatchError:
        raise BundleVerificationError(f"Stored content does not match {content_id}") from None
