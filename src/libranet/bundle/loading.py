"""Loading a bundle held in CAS.

A bundle is content like any other, so it may be stored zlib-compressed and
fits the 1 MiB object limit only as stored (HighLevelDesign §4.3). Its JSON
has to be held whole to be parsed, so decompression stops at a local cap: the
protocol sets no limit on decompressed size, but a small object must not
expand without bound (HttpApi §21).
"""

from __future__ import annotations
from typing import Final

from libranet.bundle.content import ContentSource, content_chunks
from libranet.bundle.errors import UnsupportedBundleError
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.shapes import Bundle
from libranet.cas.content_id import ContentId

# Provisional default. Bundle JSON is mostly paths and hex hashes, which
# compress about 2-4 times, so 1 MiB as stored is well within it.
DEFAULT_MAX_BUNDLE_BYTES: Final = 16 * 1024 * 1024


def load_bundle(
    content_id: ContentId, source: ContentSource, max_bytes: int = DEFAULT_MAX_BUNDLE_BYTES
) -> Bundle:
    """The bundle ``source`` holds as ``content_id``.

    Raises:
        MissingContentError: ``source`` does not hold ``content_id``.
        BundleVerificationError: what is stored does not match ``content_id``.
        UnsupportedBundleError: the bundle is larger than ``max_bytes`` once
            decompressed, or is one this node cannot read.
        PasswordProtectedBundleError: the bundle is password-protected.
        MalformedBundleError: the content is not a well-formed bundle.
    """
    chunks: list[bytes] = []
    size = 0

    for chunk in content_chunks(source, content_id):
        size += len(chunk)

        if size > max_bytes:
            raise UnsupportedBundleError(f"Bundle {content_id} is larger than {max_bytes} bytes")

        chunks.append(chunk)

    return decode_bundle(b"".join(chunks))
