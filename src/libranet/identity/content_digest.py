"""The RFC 9530 ``Content-Digest`` header that binds a signature to a body.

RFC 9421 signatures cover headers, not bodies; covering ``content-digest``
(HttpApi §11) extends a signature to the body, provided the digest itself is
checked against the bytes received.
"""

from __future__ import annotations
from base64 import b64encode
from hashlib import sha256, sha512
from hmac import compare_digest
from typing import Callable, Final

from http_message_signatures.http_sfv import Dictionary

from libranet.identity.errors import InvalidSignatureError

CONTENT_DIGEST_HEADER: Final = "Content-Digest"

_DIGESTS: Final[dict[str, Callable[[bytes], bytes]]] = {
    "sha-256": lambda body: sha256(body).digest(),
    "sha-512": lambda body: sha512(body).digest(),
}


def content_digest(body: bytes) -> str:
    """The ``Content-Digest`` value this node sends for ``body``."""
    return f"sha-256=:{b64encode(sha256(body).digest()).decode('ascii')}:"


def verify_content_digest(value: str, body: bytes) -> None:
    """Check a received ``Content-Digest`` value against ``body``.

    Unknown algorithms are ignored, but at least one supported digest must be
    present, and every supported digest must match.

    Raises:
        InvalidSignatureError: the value is malformed, has no supported
            digest, or does not match ``body``.
    """
    digests = Dictionary()

    try:
        digests.parse(value.encode("ascii"))

    except (ValueError, UnicodeEncodeError) as error:
        raise InvalidSignatureError(f"Malformed Content-Digest: {value!r}") from error

    checked = False

    for algorithm, member in digests.items():
        digest = _DIGESTS.get(algorithm)

        if digest is None:
            continue

        if not isinstance(member.value, bytes) or not compare_digest(member.value, digest(body)):
            raise InvalidSignatureError(f"Content-Digest {algorithm} does not match the body")

        checked = True

    if not checked:
        raise InvalidSignatureError(f"Content-Digest has no supported algorithm: {value!r}")
