"""Reassembling a file from its parts (BundleSpecification §2).

Parts are written out in ``contents`` order, a chunk at a time, since a
file has no size limit. Each part is checked against its own identifier as
it is read, and the whole file against the hash and size its metadata
gives (§2.3), which catches what a part-level check alone could not.

A whole-file hash under an algorithm this node lacks makes the file
unsupported rather than unchecked.
"""

from __future__ import annotations
from typing import Protocol

from libranet.bundle.content import ContentSource, content_chunks, parse_cas_path
from libranet.bundle.errors import (
    BundleVerificationError,
    MalformedBundleError,
    MissingContentError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.algorithms import DEFAULT_REGISTRY
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError


class ByteSink(Protocol):
    """Where a reassembled file is written, such as a temporary file."""

    def write(self, data: bytes, /) -> object:
        """Append ``data``."""
        ...


def write_file(bundle: FileBundle, source: ContentSource, output: ByteSink) -> int:
    """Write the file ``bundle`` describes to ``output``, returning its size.

    Every part is checked to be held before anything is written. The file is
    checked against its metadata as it is written, so a caller discards what
    was written if this raises.

    Raises:
        MissingContentError: some parts are not held locally; all are named.
        BundleVerificationError: a part, or the file as a whole, does not
            match the hash or size the bundle gives it.
        UnsupportedBundleError: a part or the whole-file hash uses a feature
            this node lacks.
        MalformedBundleError: a part is not a CAS path, or the whole-file
            hash is not valid for its algorithm.
    """
    parts = [parse_cas_path(part) for part in bundle.parts]
    expected = _whole_file_id(bundle.metadata)
    missing = tuple(part for part in dict.fromkeys(parts) if not source.exists(part))

    if missing:
        raise MissingContentError(missing)

    hasher = DEFAULT_REGISTRY.get(expected.algorithm).hasher() if expected else None
    expected_size = bundle.metadata.size
    size = 0

    for part in parts:
        for chunk in content_chunks(source, part):
            size += len(chunk)

            if expected_size is not None and size > expected_size:
                raise BundleVerificationError(f"File is larger than its size, {expected_size}")

            if hasher is not None:
                hasher.update(chunk)

            output.write(chunk)

    if expected_size is not None and size != expected_size:
        raise BundleVerificationError(f"File is {size} bytes, not its size, {expected_size}")

    if hasher is not None and expected is not None and hasher.hexdigest() != expected.hash:
        raise BundleVerificationError(f"File does not match its whole-file hash, {expected}")

    return size


def _whole_file_id(metadata: Metadata) -> ContentId | None:
    """The hash the reassembled file must have, if its metadata gives one."""
    if metadata.algorithm is None or metadata.hash is None:
        return None

    try:
        return ContentId.create(metadata.algorithm, metadata.hash)

    except UnknownAlgorithmError as error:
        raise UnsupportedBundleError(f"Whole-file hash: {error}") from None

    except InvalidContentIdError as error:
        raise MalformedBundleError(f"Whole-file hash: {error}") from None
