"""Responses more than one handler gives in place of what a request asked for.

Those refusing a body or a signature are shared by everything that reads
request bodies or checks signatures. The ``503`` is for content that is on
its way, whether from peers or out of a bundle, or for what the backup module
has not reported yet. The ``404`` for blocked content is for everything that
serves content a path names, whole or read into. The ``500`` for a registry
that cannot be read does not say why, which would name its file, since any
client may ask. The ``400`` for content that is not a usable bundle is for
everything that opens or checks one a request names.

What more than one handler reads from a request is read here too, giving what
was asked for or the response refusing it: the JSON value a body carries,
which every endpoint taking one reads, ``/config/api``'s and ``/data``'s
alike, and the content id a ``/data/{algorithm}/{hash}`` path names, which
every endpoint beneath one reads.

A JSON body is read only if its ``Content-Type`` says it is JSON, and is
``415`` otherwise, since a page on another site can send a form's types
without asking this node first (HttpApi §2.3.3).
"""

from __future__ import annotations
from dataclasses import replace
from http import HTTPStatus
from logging import DEBUG, WARNING, Logger, getLogger
from typing import Callable, Final, TypeVar

from libranet.bundle.errors import BundleError, UnsupportedBundleError
from libranet.bundle.parts import PartPath
from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError, UnknownAlgorithmError
from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    INVALID_CONTENT_ADDRESS,
    INVALID_REQUEST,
    INVALID_SIGNATURE,
    SIGNATURE_REQUIRED,
    UNUSABLE_BUNDLE,
)
from libranet.webserver.errors import UnsupportedMediaTypeError
from libranet.webserver.http_types import (
    NOT_STORED,
    Request,
    Response,
    status_response,
    typed_response,
)

_LOGGER = getLogger(__name__)

_Parsed = TypeVar("_Parsed")

# A JSON body an endpoint reads, unless it sets a limit of its own, is a small
# object of a few fields: a backup job, a restore, an application, a sign-in.
# Anything larger is a mistake, and is refused before it is read.
MAX_JSON_BODY_BYTES: Final = 64 * 1024


def unreadable_body_response(request: Request, max_bytes: int) -> Response | None:
    """The response refusing ``request``'s body unread, or ``None`` if it may be read.

    A body of unknown length is ``411``; one declared larger than
    ``max_bytes`` is ``413``.
    """
    length_bytes = request.body.length_bytes

    if length_bytes is None:
        return status_response(
            request,
            HTTPStatus.LENGTH_REQUIRED,
            "The request body must declare its Content-Length.",
        )

    if length_bytes <= max_bytes:
        return None

    return content_too_large_response(
        request,
        f"Request bodies are limited to {max_bytes} bytes; this one declares {length_bytes}.",
        max_bytes=max_bytes,
    )


def content_too_large_response(
    request: Request, detail: str, *, max_bytes: int | None = None
) -> Response:
    """The ``413`` for what ``request`` would have stored, which is larger than allowed.

    ``detail`` says what, and ``max_bytes`` is the limit, if one limit is
    what was passed.
    """
    return typed_response(
        request,
        CONTENT_TOO_LARGE,
        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
        detail,
        extensions=None if max_bytes is None else {"max_bytes": max_bytes},
    )


def unsupported_media_type_response(request: Request, error: UnsupportedMediaTypeError) -> Response:
    """The ``415`` for a body that does not say it is of the type it would be read as."""
    return status_response(request, HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(error))


def unreadable_registry_response(request: Request) -> Response:
    """The ``500`` for an application registry that cannot be read, not saying why."""
    return status_response(
        request, HTTPStatus.INTERNAL_SERVER_ERROR, "The application registry cannot be read."
    )


def signature_required_response(request: Request) -> Response:
    """The ``401`` for an unsigned request that needs a node identity (HandshakeProtocol §2.1)."""
    return typed_response(
        request,
        SIGNATURE_REQUIRED,
        HTTPStatus.UNAUTHORIZED,
        "This request must carry the sending node's signature.",
    )


def invalid_signature_response(request: Request, reason: str | None) -> Response:
    """The ``401`` for a failed signature, which ends the connection (HandshakeProtocol §5.3)."""
    response = typed_response(request, INVALID_SIGNATURE, HTTPStatus.UNAUTHORIZED, reason)
    return replace(response, close=True)


def peer_not_allowed_response(request: Request, node_id: ContentId) -> Response:
    """The ``403`` for a node this node does not connect to, which ends the connection.

    The node serves no peer it would not connect to (Phase 5 Step 113).
    """
    response = status_response(
        request,
        HTTPStatus.FORBIDDEN,
        f"This node does not serve {node_id}, as it does not connect to it.",
    )
    return replace(response, close=True)


def content_unavailable_response(
    request: Request, detail: str, retry_after_seconds: int
) -> Response:
    """The ``503`` for content asked for that is not here yet, but has been sent for (HttpApi §5.2).

    ``detail`` says what was sent for, and ``retry_after_seconds`` how long
    the client is told to wait before it asks again.
    """
    return typed_response(
        request,
        CONTENT_UNAVAILABLE,
        HTTPStatus.SERVICE_UNAVAILABLE,
        detail,
        extensions={"retry_after": retry_after_seconds},
        headers={"Retry-After": str(retry_after_seconds), **NOT_STORED},
    )


def blocked_response(request: Request) -> Response:
    """The ``404`` for blocked content, which is neither served nor fetched (HttpApi §5.5)."""
    return status_response(
        request,
        HTTPStatus.NOT_FOUND,
        "The requested content is not stored here, and will not be retrieved.",
    )


def unusable_bundle_response(request: Request, error: BundleError, logger: Logger) -> Response:
    """The ``400`` for content ``request`` names that is not usable as it asks, as ``error`` says.

    ``error`` is logged with ``logger``: as a warning if it names a scheme
    or an algorithm this node lacks, since that may mean the node needs a
    newer release, and otherwise at debug, as what a page or a peer sent.
    """
    level = WARNING if isinstance(error, UnsupportedBundleError) else DEBUG
    logger.log(level, "Refusing %s %s: %s", request.method, request.path, error)
    return typed_response(request, UNUSABLE_BUNDLE, HTTPStatus.BAD_REQUEST, str(error))


def unreported_response(request: Request, retry_after_seconds: int) -> Response:
    """The ``503`` for what the backup module has not reported yet."""
    return status_response(
        request,
        HTTPStatus.SERVICE_UNAVAILABLE,
        "The backup module has not reported its state yet.",
        headers={"Retry-After": str(retry_after_seconds)},
    )


def invalid_request_response(request: Request, error: ValueError, logger: Logger) -> Response:
    """The ``400`` for a body an endpoint cannot act on, as ``error`` says.

    ``error`` is logged with ``logger``, at debug, as what a page sent.
    """
    logger.debug("Refusing %s %s: %s", request.method, request.path, error)
    return typed_response(request, INVALID_REQUEST, HTTPStatus.BAD_REQUEST, str(error))


def body_or_refusal(request: Request, *, max_bytes: int = MAX_JSON_BODY_BYTES) -> bytes | Response:
    """``request``'s body, or the response refusing it unread, as over ``max_bytes`` is."""
    refusal = unreadable_body_response(request, max_bytes)
    return refusal if refusal is not None else request.body.read()


def json_or_refusal(request: Request, *, max_bytes: int = MAX_JSON_BODY_BYTES) -> object | Response:
    """The JSON value ``request``'s body carries, or the response refusing the body.

    A body larger than ``max_bytes``, :data:`MAX_JSON_BODY_BYTES` unless it
    is given, is ``413``. One that does not say it is JSON is ``415``, and
    one that says so and is not is ``400``.
    """
    body = body_or_refusal(request, max_bytes=max_bytes)

    if isinstance(body, Response):
        return body

    try:
        return request.json()

    except UnsupportedMediaTypeError as error:
        _LOGGER.debug("Refusing %s %s: %s", request.method, request.path, error)
        return unsupported_media_type_response(request, error)

    except ValueError as error:
        # Not logged: the refusal logs it.
        return invalid_request_response(request, error, _LOGGER)


def parsed_or_refusal(
    request: Request,
    parse: Callable[[object], _Parsed],
    *,
    max_bytes: int = MAX_JSON_BODY_BYTES,
) -> _Parsed | Response:
    """What ``parse`` makes of the JSON value ``request``'s body carries, or the refusal.

    The body is refused as :func:`json_or_refusal` refuses it, and a value
    ``parse`` refuses with a ``ValueError`` is ``400``.
    """
    value = json_or_refusal(request, max_bytes=max_bytes)

    if isinstance(value, Response):
        return value

    try:
        return parse(value)

    except ValueError as error:
        # Not logged: the refusal logs it.
        return invalid_request_response(request, error, _LOGGER)


def invalid_address_response(
    error: InvalidContentIdError | BundleError, request: Request
) -> Response:
    """The ``400`` for a ``/data/{algorithm}/{hash}`` path naming no valid id (HttpApi §5.4).

    That includes an encrypted id whose key cannot be read (§12.1).
    """
    return typed_response(request, INVALID_CONTENT_ADDRESS, HTTPStatus.BAD_REQUEST, str(error))


def content_id_or_refusal(request: Request, logger: Logger) -> ContentId | Response:
    """The content a ``/data/{algorithm}/{hash}`` path names, or the response refusing it.

    ``logger`` is the handler's own, which the refusal is logged with, but for
    any key the path carries (§12.1).
    """
    try:
        return ContentId.from_fields(request.params)

    except UnknownAlgorithmError as error:
        logger.warning(
            "Refusing %s %s: %s", request.method, PartPath.without_keys(request.path), error
        )
        return invalid_address_response(error, request)

    except InvalidContentIdError as error:
        logger.debug(
            "Refusing %s %s: %s", request.method, PartPath.without_keys(request.path), error
        )
        return invalid_address_response(error, request)
