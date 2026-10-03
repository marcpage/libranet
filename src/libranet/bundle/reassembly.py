"""Reassembling a file from its parts (BundleSpecification §2).

Parts are written out in ``contents`` order, a chunk at a time, since a
file has no size limit. Each part is checked against its own identifier, and
its size if the bundle records one (§2.1), as it is read, and decrypted if
it is encrypted (§7), and the whole file against the hash and size its
metadata gives (§2.3), which catches what a part-level check alone could
not. :class:`WholeFileCheck` makes those last checks, for anything that
reads a file whole, as the web server does in streaming one (Phase 3 Step
65).

A whole-file hash under an algorithm this node lacks makes the file
unsupported rather than unchecked.
"""

from __future__ import annotations
from typing import Protocol

from libranet.bundle.content import ContentSource, check_held
from libranet.bundle.errors import BundleVerificationError
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.algorithms import DEFAULT_REGISTRY


class ByteSink(Protocol):
    """Where a reassembled file is written, such as a temporary file."""

    def write(self, data: bytes, /) -> object:
        """Append ``data``."""
        ...


class WholeFileCheck:
    """A file's bytes checked against its size and whole-file hash (§2.3), as they are read.

    Raises:
        UnsupportedBundleError: the whole-file hash uses an algorithm this
            node lacks.
        MalformedBundleError: the whole-file hash is not valid for its
            algorithm.
    """

    def __init__(self, metadata: Metadata) -> None:
        expected = metadata.whole_file_id()
        self._expected = expected
        self._hasher = DEFAULT_REGISTRY.get(expected.algorithm).hasher() if expected else None
        self._expected_bytes = metadata.size_bytes
        self._size_bytes = 0

    @property
    def size_bytes(self) -> int:
        """How many bytes of the file have been taken in."""
        return self._size_bytes

    def update(self, data: bytes) -> None:
        """Take in the file's next ``data``.

        Raises:
            BundleVerificationError: the file is now larger than its size.
        """
        self._size_bytes += len(data)
        expected_bytes = self._expected_bytes

        if expected_bytes is not None and self._size_bytes > expected_bytes:
            raise BundleVerificationError(f"File is larger than its size, {expected_bytes}")

        if self._hasher is not None:
            self._hasher.update(data)

    def finish(self) -> None:
        """Check the file taken in, now that all of it has been.

        Raises:
            BundleVerificationError: the file is not its size, or does not
                match its whole-file hash.
        """
        expected_bytes = self._expected_bytes

        if expected_bytes is not None and self._size_bytes != expected_bytes:
            raise BundleVerificationError(
                f"File is {self._size_bytes} bytes, not its size, {expected_bytes}"
            )

        expected = self._expected

        if self._hasher is not None and expected is not None:
            actual = self._hasher.hexdigest()

            if actual != expected.hash:
                raise BundleVerificationError(
                    f"File does not match its whole-file hash, {expected} vs {actual}"
                )


def write_file(bundle: FileBundle, source: ContentSource, output: ByteSink) -> int:
    """Write the file ``bundle`` describes to ``output``, returning its size.

    Every part is checked to be held before anything is written. The file is
    checked against its metadata as it is written, so a caller discards what
    was written if this raises.

    Raises:
        MissingContentError: some parts are not held locally; all are named.
        BundleVerificationError: a part, or the file as a whole, does not
            match the hash or size the bundle gives it, or an encrypted part
            does not decrypt under its key.
        UnsupportedBundleError: a part or the whole-file hash uses a feature
            this node lacks.
        MalformedBundleError: a part is not a CAS path, or the whole-file
            hash is not valid for its algorithm.
    """
    parts = [PartPath.parse(part) for part in bundle.parts]
    check = WholeFileCheck(bundle.metadata)
    check_held((part.content_id for part in parts), source)
    sizes_bytes = bundle.part_sizes_bytes or (None,) * len(parts)

    for part, part_bytes in zip(parts, sizes_bytes):
        for chunk in part.chunks(source, part_bytes):
            check.update(chunk)
            output.write(chunk)

    check.finish()
    return check.size_bytes
