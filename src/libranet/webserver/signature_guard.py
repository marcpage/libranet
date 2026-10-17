"""The signature policy every request passes before it is routed.

A signed request, whatever it asks for, has its signature checked; one that
fails is refused and its connection closed (HandshakeProtocol §2.1). The
outcome is attached to the request, so a handler never checks it again,
which would count one request twice against the provisional-trust limit. A
signer whose key verified it is noted on the connection the request arrived
on, since that key must be kept while the connection is open (Phase 2 Step
53).

Unsigned requests pass through, except that reads (``GET`` or ``HEAD``) of
the ``/data`` API are refused unless ``allow_unsigned_api_reads`` is set, the
stricter local policy HttpApi §7.3 allows. Unsigned reads outside ``/data``
are always passed on, since they must be honored, and handlers that need an
identity (uploads) refuse unsigned requests themselves.

A signature may cover the body, so the body is read before the check; a
signed request whose body cannot be read within ``max_body_bytes`` is refused
unread.

A request signed by a node this node does not connect to, whose trust says
it is untrusted, or not trusted while the node connects only to the nodes it
trusts, is refused ``403``, and its connection closed, whether its signature
is verified or its key not yet held (Phase 5 Step 113). The node serves no
peer it would not connect to.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, replace
from typing import Callable, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.identity.authentication import AuthenticationStatus, RequestAuthenticator
from libranet.identity.signatures import SIGNATURE_HEADER, SIGNATURE_INPUT_HEADER
from libranet.protocol.http_syntax import API_PREFIX
from libranet.webserver.http_types import READ_METHODS, Request, Response
from libranet.webserver.request_refusals import (
    invalid_signature_response,
    peer_not_allowed_response,
    signature_required_response,
    unreadable_body_response,
)

_SIGNATURE_FIELDS: Final = frozenset({SIGNATURE_HEADER.lower(), SIGNATURE_INPUT_HEADER.lower()})


@dataclass(frozen=True)
class SignatureGuard:
    """A :data:`~libranet.webserver.router.Guard` applying the signature policy.

    A request signed by a node ``allows`` does not allow is refused; with no
    ``allows``, every node is allowed.
    """

    authenticator: RequestAuthenticator
    max_body_bytes: int
    _: KW_ONLY
    allow_unsigned_api_reads: bool
    allows: Callable[[ContentId], bool] | None = None

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

        signer = result.node_id

        if signer is not None and self.allows is not None and not self.allows(signer):
            return peer_not_allowed_response(request, signer)

        verified = result.status is AuthenticationStatus.VERIFIED

        if verified and result.node_id is not None and request.connection is not None:
            request.connection.verified(result.node_id)

        return replace(request, authentication=result)


def _is_signed(headers: Mapping[str, str]) -> bool:
    """Whether a request carries any part of a signature."""
    return any(name.lower() in _SIGNATURE_FIELDS for name in headers)


def _is_api_read(request: Request) -> bool:
    """Whether a request reads from the programmatic API (HttpApi §2.1)."""
    in_api = request.path == API_PREFIX or request.path.startswith(f"{API_PREFIX}/")
    return in_api and request.method in READ_METHODS
