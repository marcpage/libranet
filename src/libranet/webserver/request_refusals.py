"""Responses more than one handler gives in place of what a request asked for.

Those refusing a body or a signature are shared by everything that reads
request bodies or checks signatures. The ``503`` is for content that is on
its way, whether from peers or out of a bundle. The ``500`` for a registry
that cannot be read does not say why, which would name its file, since any
client may ask.
"""

from __future__ import annotations
from dataclasses import replace
from http import HTTPStatus

from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    INVALID_SIGNATURE,
    SIGNATURE_REQUIRED,
    Problem,
)
from libranet.webserver.errors import UnsupportedMediaTypeError
from libranet.webserver.http_types import Request, Response, problem_response, status_response


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
    return problem_response(
        Problem.of_type(
            CONTENT_TOO_LARGE,
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            detail=detail,
            instance=request.path,
            extensions=None if max_bytes is None else {"max_bytes": max_bytes},
        )
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
    return problem_response(
        Problem.of_type(
            SIGNATURE_REQUIRED,
            HTTPStatus.UNAUTHORIZED,
            detail="This request must carry the sending node's signature.",
            instance=request.path,
        )
    )


def invalid_signature_response(request: Request, reason: str | None) -> Response:
    """The ``401`` for a failed signature, which ends the connection (HandshakeProtocol §5.3)."""
    response = problem_response(
        Problem.of_type(
            INVALID_SIGNATURE, HTTPStatus.UNAUTHORIZED, detail=reason, instance=request.path
        )
    )
    return replace(response, close=True)


def content_unavailable_response(
    request: Request, detail: str, retry_after_seconds: int
) -> Response:
    """The ``503`` for content asked for that is not here yet, but has been sent for (HttpApi §5.2).

    ``detail`` says what was sent for, and ``retry_after_seconds`` how long
    the client is told to wait before it asks again.
    """
    return problem_response(
        Problem.of_type(
            CONTENT_UNAVAILABLE,
            HTTPStatus.SERVICE_UNAVAILABLE,
            detail=detail,
            instance=request.path,
            extensions={"retry_after": retry_after_seconds},
        ),
        {"Retry-After": str(retry_after_seconds), "Cache-Control": "no-store"},
    )
