"""Storing content and bundles in CAS within the object limit (HighLevelDesign §4.3).

Every object is stored under its SHA-256, zlib-compressed at the highest
level unless that does not make it smaller (HttpApi §8), and only if it is
not held already. Storing what has not changed therefore writes nothing, and
costs no compression.

A bundle is stored as its JSON, password-protected when a password is given
(BundleSpecification §6). A directory bundle too large for one object has
its entries split into chunks (:mod:`libranet.bundle.splitting`). Each chunk
is stored as a directory bundle of its own, protected alike. The bundle
stored in its place keeps its metadata and versions, holds no entries, and
lists the chunks as extensions ahead of any it had. Chunks hold disjoint
paths, so their order does not matter. Placed first, they rank above the
bundle's own extensions, as its entries did (§4.1).
"""

from __future__ import annotations
from typing import Final, Protocol
from zlib import compress

from libranet.bundle.errors import BundleTooLargeError
from libranet.bundle.extensions import DEFAULT_MAX_EXTENSIONS
from libranet.bundle.protection import protect
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Bundle, DirectoryBundle
from libranet.bundle.splitting import split_entries
from libranet.cas.content_id import ContentId
from libranet.config.models import MIB

HASH_ALGORITHM: Final = "sha256"

# Storage compression changes no identifier, so its level is free to change.
_COMPRESSION_LEVEL: Final = 9

# What storing a chunk may add to its entries' JSON: the object around them,
# zlib's worst case for incompressible data (about 1/3,300 plus 13 bytes),
# and password protection's padding and descriptor (under 40 bytes).
_SPLIT_MARGIN_BYTES: Final = 64
_SPLIT_MARGIN_SHIFT: Final = 10


class ContentSink(Protocol):
    """Where new content is stored, such as the source of truth."""

    def exists(self, content_id: ContentId) -> bool:
        """Whether ``content_id`` is held."""
        ...

    def write(self, content_id: ContentId, data: bytes) -> object:
        """Store ``data``, the content of ``content_id`` as is or zlib-compressed."""
        ...


def store_object(data: bytes, sink: ContentSink, max_object_bytes: int = MIB) -> ContentId:
    """Store ``data`` in ``sink`` unless it is held, and return its identifier.

    Raises:
        BundleTooLargeError: ``data`` is not held, and is larger than
            ``max_object_bytes`` even compressed.
    """
    content_id = _store_if_it_fits(data, sink, max_object_bytes)

    if content_id is None:
        raise BundleTooLargeError(
            f"{len(data)} bytes do not fit in {max_object_bytes} bytes, even compressed"
        )

    return content_id


def store_bundle(
    bundle: Bundle,
    sink: ContentSink,
    password: bytes | None = None,
    max_object_bytes: int = MIB,
    max_extensions: int = DEFAULT_MAX_EXTENSIONS,
) -> ContentId:
    """Store ``bundle`` in ``sink``, and return the identifier it is read back by.

    It is password-protected with ``password``, if one is given. A directory
    bundle is split across extensions if it does not fit in one object.

    Raises:
        BundleTooLargeError: ``bundle`` does not fit in one object, and is
            not a directory bundle, or is one with an entry that does not
            fit in one alone, or with more chunks and extensions than
            ``max_extensions``, which readers do not follow.
    """
    data = _encoded(bundle, password)

    if not isinstance(bundle, DirectoryBundle):
        return store_object(data, sink, max_object_bytes)

    content_id = _store_if_it_fits(data, sink, max_object_bytes)

    if content_id is not None:
        return content_id

    margin = (max_object_bytes >> _SPLIT_MARGIN_SHIFT) + _SPLIT_MARGIN_BYTES
    chunks = split_entries(bundle.entries, max_object_bytes - margin)

    if len(chunks) + len(bundle.extensions) > max_extensions:
        raise BundleTooLargeError(
            f"Directory needs {len(chunks)} chunks and {len(bundle.extensions)} extensions, "
            f"more than the {max_extensions} extensions a reader follows"
        )

    stored_chunks = tuple(
        str(store_object(_encoded(DirectoryBundle(chunk), password), sink, max_object_bytes))
        for chunk in chunks
    )
    top = DirectoryBundle({}, bundle.metadata, bundle.versions, stored_chunks + bundle.extensions)

    return store_object(_encoded(top, password), sink, max_object_bytes)


def _store_if_it_fits(data: bytes, sink: ContentSink, max_object_bytes: int) -> ContentId | None:
    """Store ``data`` unless it is held; ``None`` if it is not held and does not fit."""
    content_id = ContentId.for_data(data, HASH_ALGORITHM)

    if sink.exists(content_id):
        return content_id

    compressed = compress(data, _COMPRESSION_LEVEL)
    stored = compressed if len(compressed) < len(data) else data

    if len(stored) > max_object_bytes:
        return None

    sink.write(content_id, stored)
    return content_id


def _encoded(bundle: Bundle, password: bytes | None) -> bytes:
    """``bundle`` as it is stored: its JSON, password-protected if there is a password."""
    encoded = encode_bundle(bundle)
    return encoded if password is None else protect(encoded, password)
