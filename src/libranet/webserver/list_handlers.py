"""``/data/nodes`` and ``/data/seek``: the node and seek lists (HttpApi §10.5, §10.7).

A ``GET`` serves the file the stats module derives (Step 8) exactly as it is
on disk, so reading a list costs the stats module nothing. Until the first
derivation has written the file, the answer is ``503`` with a
``Retry-After``.

A ``POST`` is a peer publishing its own list. Like an upload, it needs a
signature (HandshakeProtocol §2.1): a posted seek list is recorded against
the node that signed it. These handlers rely on the router's
:class:`~libranet.webserver.signature_guard.SignatureGuard` to have checked
that signature, so without the guard every ``POST`` is refused as unsigned.
The body is capped at ``max_bytes`` as sent, and a compressed one at
``max_decompressed_bytes`` once expanded, which may be the larger of the two.
The list is checked and then published for the stats module to persist, and
the answer is ``202`` without waiting for that::

    nodes.received  {"nodes": {"http://203.0.113.42:4300": "sha256/<hex>"}}
    seek.received   {"node_id": "sha256/<hex>", "data": ["sha256/<hex>"], "search": ["<hex>"]}

Every ``localhost`` endpoint in a received node list is resolved to the
address the request came from before the list is published (HttpApi §10.2).
"""

from __future__ import annotations
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.messaging.events import EventType
from libranet.problems import INVALID_LIST, Problem
from libranet.webserver.http_types import (
    JSON_CONTENT_TYPE,
    Request,
    Response,
    bytes_response,
    problem_response,
)
from libranet.webserver.list_bodies import (
    InvalidListError,
    decode_list,
    parse_node_list,
    parse_seek_list,
)
from libranet.webserver.localhost_resolution import resolve_endpoint
from libranet.webserver.publishing import Publish
from libranet.webserver.request_refusals import (
    signature_required_response,
    unreadable_body_response,
)

NODES_PATH: Final = "/data/nodes"
SEEK_PATH: Final = "/data/seek"


@dataclass(frozen=True)
class ListFileHandler:
    """Serves one derived list file as JSON."""

    path: Path
    retry_after_seconds: int

    def __call__(self, request: Request) -> Response:
        try:
            body = self.path.read_bytes()

        except FileNotFoundError:
            return problem_response(
                Problem.for_status(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    detail="This list has not been derived yet.",
                    instance=request.path,
                ),
                {"Retry-After": str(self.retry_after_seconds)},
            )

        return bytes_response(body, JSON_CONTENT_TYPE)


@dataclass(frozen=True)
class NodeListHandler:
    """Publishes a peer's node list with its ``localhost`` endpoints resolved.

    An entry whose endpoint cannot be stored is dropped (see
    :func:`~libranet.webserver.localhost_resolution.resolve_endpoint`). If
    two entries end up with the same endpoint, the later one is kept.
    """

    max_bytes: int
    max_decompressed_bytes: int
    publish: Publish

    def __call__(self, request: Request) -> Response:
        signer = _signer_or_refusal(request, self.max_bytes)

        if isinstance(signer, Response):
            return signer

        try:
            nodes = parse_node_list(decode_list(request.body.read(), self.max_decompressed_bytes))

        except InvalidListError as error:
            return _invalid_list_response(request, error)

        resolved: dict[str, str] = {}

        for endpoint, node_id in nodes.items():
            stored = resolve_endpoint(endpoint, request.client_address)

            if stored is not None:
                resolved[stored] = node_id

        self.publish(EventType.NODES_RECEIVED, {"nodes": resolved})
        return Response(HTTPStatus.ACCEPTED)


@dataclass(frozen=True)
class SeekListHandler:
    """Publishes a peer's seek list, attributed to the node that signed it."""

    max_bytes: int
    max_decompressed_bytes: int
    publish: Publish

    def __call__(self, request: Request) -> Response:
        signer = _signer_or_refusal(request, self.max_bytes)

        if isinstance(signer, Response):
            return signer

        try:
            data, search = parse_seek_list(
                decode_list(request.body.read(), self.max_decompressed_bytes)
            )

        except InvalidListError as error:
            return _invalid_list_response(request, error)

        self.publish(
            EventType.SEEK_RECEIVED, {"node_id": str(signer), "data": data, "search": search}
        )
        return Response(HTTPStatus.ACCEPTED)


def _signer_or_refusal(request: Request, max_bytes: int) -> ContentId | Response:
    """The node posting ``request``'s list, or the response refusing it.

    The body is read before the signer is looked at, so an unsigned request
    is refused without the connection having to close, as with uploads.
    """
    refusal = unreadable_body_response(request, max_bytes)

    if refusal is not None:
        return refusal

    request.body.read()
    signer = None if request.authentication is None else request.authentication.node_id

    if signer is None:
        return signature_required_response(request)

    return signer


def _invalid_list_response(request: Request, error: InvalidListError) -> Response:
    """The ``400`` for a posted body that is not a list of the expected shape."""
    return problem_response(
        Problem(
            status=HTTPStatus.BAD_REQUEST,
            title="Invalid list",
            type=INVALID_LIST,
            detail=str(error),
            instance=request.path,
        )
    )
