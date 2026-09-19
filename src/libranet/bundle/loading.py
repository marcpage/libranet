"""Loading a bundle held in CAS.

A bundle is content like any other, so it may be stored zlib-compressed and
fits the 1 MiB object limit only as stored (HighLevelDesign §4.3). Its JSON
has to be held whole to be parsed, so decompression stops at a local cap: the
protocol sets no limit on decompressed size, but a small object must not
expand without bound (HttpApi §21).

A password-protected bundle (BundleSpecification §6) is decrypted when a
password is given, and is held to the same cap once decrypted and
decompressed.
"""

from __future__ import annotations
from typing import Final

from libranet.bundle.content import ContentSource, content_chunks
from libranet.bundle.errors import PasswordProtectedBundleError, UnsupportedBundleError
from libranet.bundle.parsing import decode_bundle
from libranet.bundle.protection import strip_targeting, unprotect
from libranet.bundle.shapes import Bundle
from libranet.cas.content_id import ContentId

# Provisional default. Bundle JSON is mostly paths and hex hashes, which
# compress about 2-4 times, so 1 MiB as stored is well within it.
DEFAULT_MAX_BUNDLE_BYTES: Final = 16 * 1024 * 1024


def load_bundle(
    content_id: ContentId,
    source: ContentSource,
    max_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    password: bytes | None = None,
    targeted: bool = False,
) -> Bundle:
    """The bundle ``source`` holds as ``content_id``.

    A bundle that is not password-protected is read as is, even when a
    ``password`` is given (§6.5). ``targeted`` says the bundle is a drop,
    ending in placement bytes to be dropped first (§6.4).

    Raises:
        MissingContentError: ``source`` does not hold ``content_id``.
        BundleVerificationError: what is stored does not match ``content_id``.
        UnsupportedBundleError: the bundle is larger than ``max_bytes`` once
            decompressed, or is one this node cannot read.
        PasswordProtectedBundleError: the bundle is password-protected, and
            no ``password`` was given.
        IncorrectPasswordError: ``password`` does not decrypt the bundle.
        MalformedBundleError: the content is not a well-formed bundle.
    """
    chunks: list[bytes] = []
    size = 0

    for chunk in content_chunks(source, content_id):
        size += len(chunk)

        if size > max_bytes:
            raise UnsupportedBundleError(f"Bundle {content_id} is larger than {max_bytes} bytes")

        chunks.append(chunk)

    data = b"".join(chunks)

    if targeted:
        data = strip_targeting(data)

    try:
        return decode_bundle(data)

    except PasswordProtectedBundleError:
        if password is None:
            raise

        return decode_bundle(unprotect(data, password, max_bytes))
