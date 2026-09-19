"""Splitting a directory's entries into chunks, to be stored as extensions (BundleSpecification §4).

A directory bundle too large to store as one object is split, with each
chunk stored as an extension of its own. Entries are taken in order of path,
so each chunk holds neighbouring paths. A chunk ends after an entry whose
path hashes low enough, with odds in proportion to the entry's size, so
chunks average about half of ``max_bytes``. An entry as large as that always
ends its chunk. A chunk also ends early rather than grow past ``max_bytes``.

Whether an entry ends its chunk depends on that entry alone. Storing a
changed directory again therefore changes only the chunk holding each
change, and, when a chunk that ended early grows or shrinks, the chunks
after it up to the next entry that ends one. Every other chunk encodes to
the same bytes as before, and so dedups in CAS.

Sizes are those of the entries' JSON, before compression, so where chunks
end does not depend on how well entries compress, or on which zlib
compressed them.
"""

from __future__ import annotations
from hashlib import sha256
from json import dumps
from typing import Final, Mapping

from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import Entry

_HASH_BITS: Final = 64
_NULL: Final = b"null"

# A key and its entry are joined by ":" and followed by "," (or "}").
_ENTRY_PUNCTUATION_BYTES: Final = 2


def split_entries(
    entries: Mapping[str, Entry | None], max_bytes: int
) -> list[dict[str, Entry | None]]:
    """``entries`` as chunks of at most ``max_bytes`` of JSON each, in order of path.

    An entry larger than ``max_bytes`` on its own is a chunk by itself.
    """
    target = max_bytes // 2
    chunks: list[dict[str, Entry | None]] = []
    chunk: dict[str, Entry | None] = {}
    chunk_bytes = 0

    for path in sorted(entries):
        entry = entries[path]
        entry_bytes = _encoded_bytes(path, entry)

        if chunk and chunk_bytes + entry_bytes > max_bytes:
            chunks.append(chunk)
            chunk, chunk_bytes = {}, 0

        chunk[path] = entry
        chunk_bytes += entry_bytes

        if _ends_chunk(path, entry_bytes, target):
            chunks.append(chunk)
            chunk, chunk_bytes = {}, 0

    if chunk:
        chunks.append(chunk)

    return chunks


def _encoded_bytes(path: str, entry: Entry | None) -> int:
    """The bytes ``path`` and its ``entry`` take in a directory bundle's JSON."""
    encoded = _NULL if entry is None else encode_bundle(entry)
    return len(dumps(path)) + len(encoded) + _ENTRY_PUNCTUATION_BYTES


def _ends_chunk(path: str, entry_bytes: int, target: int) -> bool:
    """Whether a chunk ends after ``path``: at odds of ``entry_bytes`` in ``target``."""
    digest = sha256(path.encode("utf-8", "surrogatepass")).digest()
    fraction = int.from_bytes(digest[: _HASH_BITS // 8], "big")
    return fraction * target < entry_bytes << _HASH_BITS
