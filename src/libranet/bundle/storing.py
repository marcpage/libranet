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

A directory bundle may instead be stored with per-entry encryption (§7), as
a part is, by a writer such as :class:`~libranet.bundle.parts.PartWriter`,
so that what names it carries its key. Each chunk is then stored so too, and
named with its key among the extensions (Phase 3 Step 72).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Final, Protocol
from zlib import compress

from libranet.bundle.errors import BundleTooLargeError
from libranet.bundle.protection import protect
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DEFAULT_MAX_EXTENSIONS, Bundle, DirectoryBundle
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


class StoredObject(Protocol):
    """An object as a bundle names it, such as a :class:`~libranet.bundle.parts.PartPath`.

    Its text is the CAS path a bundle names it by, which carries its key if
    it is stored encrypted (§7).
    """

    @property
    def content_id(self) -> ContentId:
        """What is stored: the object itself, or its ciphertext."""
        ...

    @property
    def key(self) -> bytes | None:
        """The key the object is encrypted under, with the default IV, or ``None`` if it is not."""
        ...


#: Stores the bytes of one object, unless they are held, and gives what a
#: bundle names it by, as :meth:`~libranet.bundle.parts.PartWriter.store`
#: does. It raises :class:`BundleTooLargeError` for bytes not held that do
#: not fit in one object.
ObjectWriter = Callable[[bytes], StoredObject]


def store_object(data: bytes, sink: ContentSink, max_object_bytes: int = MIB) -> ContentId:
    """Store ``data`` in ``sink`` unless it is held, and return its identifier.

    Raises:
        BundleTooLargeError: ``data`` is not held, and is larger than
            ``max_object_bytes`` even compressed.
    """
    content_id = ContentId.for_data(data, HASH_ALGORITHM)

    if sink.exists(content_id):
        return content_id

    compressed = compress(data, _COMPRESSION_LEVEL)
    stored = compressed if len(compressed) < len(data) else data

    if len(stored) > max_object_bytes:
        raise BundleTooLargeError(
            f"{len(data)} bytes do not fit in {max_object_bytes} bytes, even compressed"
        )

    sink.write(content_id, stored)
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
    if not isinstance(bundle, DirectoryBundle):
        return _store(bundle, sink, password, max_object_bytes)

    return StoredDirectory.store(
        bundle, sink, password, max_object_bytes, max_extensions
    ).content_id


@dataclass(frozen=True)
class StoredDirectory:
    """A directory bundle as stored: what it is read back by, and the chunks it was split into.

    ``key`` is the key it is encrypted under (§7), with the default IV, if
    it is stored encrypted.
    """

    content_id: ContentId
    chunks: int = 0
    key: bytes | None = None

    @classmethod
    def store(
        cls,
        bundle: DirectoryBundle,
        sink: ContentSink,
        password: bytes | None = None,
        max_object_bytes: int = MIB,
        max_extensions: int = DEFAULT_MAX_EXTENSIONS,
        *,
        write: ObjectWriter | None = None,
    ) -> StoredDirectory:
        """Store ``bundle`` in ``sink``, split across extensions if it does not fit in one object.

        It is password-protected with ``password``, if one is given. Each
        object, the bundle and each chunk, is stored by ``write`` in place
        of ``sink``, if it is given, and named as it says.

        Raises:
            BundleTooLargeError: ``bundle`` does not fit in one object, and
                has an entry that does not fit in one alone, or more chunks
                and extensions than ``max_extensions``, which readers do not
                follow.
        """

        def stored(directory: DirectoryBundle) -> StoredObject | ContentId:
            """``directory`` stored as one object, by ``write`` if it is given."""
            if write is None:
                return _store(directory, sink, password, max_object_bytes)

            return write(_encoded(directory, password, max_object_bytes))

        try:
            return cls._of(stored(bundle))

        except BundleTooLargeError:
            pass  # Not logged: split below, outside the handler, so errors splitting raise alone.

        margin_bytes = (max_object_bytes >> _SPLIT_MARGIN_SHIFT) + _SPLIT_MARGIN_BYTES
        chunks = split_entries(bundle.entries, max_object_bytes - margin_bytes)

        if len(chunks) + len(bundle.extensions) > max_extensions:
            raise BundleTooLargeError(
                f"Directory needs {len(chunks)} chunks and {len(bundle.extensions)} extensions, "
                f"more than the {max_extensions} extensions a reader follows"
            )

        stored_chunks = tuple(str(stored(DirectoryBundle(chunk))) for chunk in chunks)
        top = DirectoryBundle(
            {}, bundle.metadata, bundle.versions, stored_chunks + bundle.extensions
        )

        return cls._of(stored(top), len(stored_chunks))

    @classmethod
    def _of(cls, stored: StoredObject | ContentId, chunks: int = 0) -> StoredDirectory:
        """The directory stored as ``stored``, by its id or as a writer named it, in ``chunks``."""
        if isinstance(stored, ContentId):
            return cls(stored, chunks)

        return cls(stored.content_id, chunks, stored.key)


def _store(
    bundle: Bundle, sink: ContentSink, password: bytes | None, max_object_bytes: int
) -> ContentId:
    """Store ``bundle`` as one object: its JSON, password-protected if there is a password.

    Returns:
        The identifier it is read back by.

    Raises:
        BundleTooLargeError: it does not fit in one object.
    """
    return store_object(_encoded(bundle, password, max_object_bytes), sink, max_object_bytes)


def _encoded(bundle: Bundle, password: bytes | None, max_object_bytes: int) -> bytes:
    """``bundle``'s JSON, password-protected if there is a password.

    Raises:
        BundleTooLargeError: it is protected, and does not fit in one object.
    """
    encoded = encode_bundle(bundle)

    if password is not None:
        encoded = protect(encoded, password, max_object_bytes)

    return encoded
