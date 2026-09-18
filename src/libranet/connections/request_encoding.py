"""Signed HTTP/1.1 requests as the bytes the raw-socket client sends (Step 10).

Every request is signed with this node's key (HttpApi §11) over its method,
its path without any query string, and a digest of any body. The client
owns the message framing, so ``Host`` and ``Content-Length`` are always set
here and a caller may not supply them.
"""

from __future__ import annotations
from re import compile as compile_pattern
from typing import Final, Mapping

from libranet.identity.signatures import MessageSigner

# Methods that define a meaning for a body, so even an empty one is framed
# with `Content-Length: 0` (RFC 9110 §8.6).
_BODY_METHODS: Final = frozenset({"POST", "PUT", "PATCH"})

# Lower-cased names of the header fields that frame the message.
_FRAMING_HEADERS: Final = frozenset({"host", "content-length", "transfer-encoding"})

# RFC 9110 §5.6.2.
_TOKEN: Final = compile_pattern(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
# Origin-form (RFC 9112 §3.2.1): visible ASCII, starting with "/".
_TARGET: Final = compile_pattern(r"/[!-~]*")
# ASCII text, with no line breaks that would end the field early.
_FIELD_VALUE: Final = compile_pattern(r"[\t -~]*")


def encode_request(
    signer: MessageSigner,
    method: str,
    target: str,
    host: str,
    headers: Mapping[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    """The whole signed request, ready to write to the connection.

    ``target`` is the path, optionally followed by a query string, which the
    signature does not cover. ``host`` is the ``Host`` header's value.

    Raises:
        ValueError: the request cannot be sent as given.
    """
    if not _TOKEN.fullmatch(method):
        raise ValueError(f"Invalid method {method!r}")

    if not _TARGET.fullmatch(target):
        raise ValueError(f"Invalid request target {target!r}")

    if not _FIELD_VALUE.fullmatch(host):
        raise ValueError(f"Invalid host {host!r}")

    fields = dict(headers or {})

    for name, value in fields.items():
        if not _TOKEN.fullmatch(name) or not _FIELD_VALUE.fullmatch(value):
            raise ValueError(f"Invalid header {name!r}: {value!r}")

        if name.lower() in _FRAMING_HEADERS:
            raise ValueError(f"The {name} header is set by the client")

    signed = signer.sign_request(method, target.partition("?")[0], fields, body)
    lines = [f"{method} {target} HTTP/1.1", f"Host: {host}"]
    lines.extend(f"{name}: {value}" for name, value in signed.items())

    if body or method in _BODY_METHODS:
        lines.append(f"Content-Length: {len(body)}")

    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + body
