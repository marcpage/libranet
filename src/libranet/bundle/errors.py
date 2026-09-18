"""Exceptions raised by the bundle library."""

from __future__ import annotations

from libranet.cas.content_id import ContentId


class BundleError(Exception):
    """Base class for every bundle library error."""


class MalformedBundleError(BundleError, ValueError):
    """The data is not a well-formed bundle (BundleSpecification)."""


class UnsupportedBundleError(BundleError):
    """The bundle may be well-formed, but this node cannot read it.

    It uses a feature not implemented here, such as a signed bundle (§5),
    per-entry encryption (§7), or a hash algorithm this node lacks, or it is
    beyond one of this node's local limits.
    """


class PasswordProtectedBundleError(UnsupportedBundleError):
    """The bundle is password-protected (BundleSpecification §6).

    Decrypting it is the write-side library's job (Phase 1 Step 17).
    """


class MissingContentError(BundleError):
    """Content the bundle names is not held locally.

    This is normal rather than a fault: the content can be fetched from peers
    and the read tried again. Every missing identifier found is named at
    once, so all of them can be fetched together.
    """

    def __init__(self, content_ids: tuple[ContentId, ...]) -> None:
        super().__init__(f"Content not held locally: {', '.join(map(str, content_ids))}")
        self.content_ids = content_ids


class BundleVerificationError(BundleError):
    """Content does not match the identifier, hash, or size the bundle gives it."""
