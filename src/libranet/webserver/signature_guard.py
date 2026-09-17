"""The signature policy every request passes before it is routed.

A signed request, whatever it asks for, has its signature checked; one that
fails is refused and its connection closed (HandshakeProtocol §2.1). The
outcome is attached to the request, so a handler never checks it again,
which would count one request twice against the provisional-trust limit.

Unsigned requests pass through, except that reads (``GET`` or ``HEAD``) of
the ``/data`` API are refused unless ``allow_unsigned_api_reads`` is set, the
stricter local policy HttpApi §7.3 allows. Unsigned reads outside ``/data``
are always passed on, since they must be honored, and handlers that need an
identity (uploads) refuse unsigned requests themselves.

A signature may cover the body, so the body is read before the check; a
signed request whose body cannot be read within ``max_body_bytes`` is refused
unread.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, replace
from typing import Final, Mapping

from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.signatures import SIGNATURE_HEADER, SIGNATURE_INPUT_HEADER
from libranet.webserver.http_types import Request, Response
from libranet.webserver.request_refusals import (
    invalid_signature_response,
    signature_required_response,
    unreadable_body_response,
)

API_PREFIX: Final = "/data"
_READ_METHODS: Final = frozenset({"GET", "HEAD"})
_SIGNATURE_FIELDS: Final = frozenset({SIGNATURE_HEADER.lower(), SIGNATURE_INPUT_HEADER.lower()})


@dataclass(frozen=True)
class SignatureGuard:
    """A :data:`~libranet.webserver.router.Guard` applying the signature policy."""

    authenticator: RequestAuthenticator
    max_body_bytes: int
    _: KW_ONLY
    allow_unsigned_api_reads: bool

    def __call__(self, request: Request) -> Request | Response:
        if not _is_signed(request.headers):
            if not self.allow_unsigned_api_reads and _is_api_read(request):
                return signature_required_response(request)

            return request

        refusal = unreadable_body_response(request, self.max_body_bytes)

        if refusal is not None:
            return refusal

        result = self.authenticator.authenticate(
            request.method, request.path, request.headers, request.body.read()
        )

        if result.rejected:
            return invalid_signature_response(request, result.reason)

        return replace(request, authentication=result)


def _is_signed(headers: Mapping[str, str]) -> bool:
    """Whether a request carries any part of a signature."""
    return any(name.lower() in _SIGNATURE_FIELDS for name in headers)


def _is_api_read(request: Request) -> bool:
    """Whether a request reads from the programmatic API (HttpApi §2.1)."""
    in_api = request.path == API_PREFIX or request.path.startswith(f"{API_PREFIX}/")
    return in_api and request.method in _READ_METHODS
