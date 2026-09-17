"""Exceptions raised by the node identity and signature library."""

from __future__ import annotations

from libranet.cas.content_id import ContentId


class IdentityError(Exception):
    """Base class for every identity library error."""


class KeyFileError(IdentityError, ValueError):
    """Stored or received key material cannot be decoded as a node key."""


class SignatureError(IdentityError):
    """Base class for every reason a message fails authentication."""


class MissingSignatureError(SignatureError):
    """The message carries no signature at all.

    This is an unauthenticated message rather than a forged one:
    HandshakeProtocol §2.1 still honors such ``GET`` requests.
    """


class InvalidSignatureError(SignatureError):
    """The message's signature is malformed, stale, or does not verify."""


class UnknownKeyError(SignatureError):
    """The signer's public key is not held locally, so it cannot be checked yet.

    Every check that does not need the key has already passed when this is
    raised.
    """

    def __init__(self, node_id: ContentId) -> None:
        super().__init__(f"Public key not held locally: {node_id}")
        self.node_id = node_id
