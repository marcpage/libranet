"""Refusals shared by everything that reads request bodies or checks signatures."""

from __future__ import annotations
from dataclasses import replace
from http import HTTPStatus

from libranet.problems import CONTENT_TOO_LARGE, INVALID_SIGNATURE, SIGNATURE_REQUIRED, Problem
from libranet.webserver.http_types import Request, Response, problem_response


def unreadable_body_response(request: Request, max_bytes: int) -> Response | None:
    """The response refusing ``request``'s body unread, or ``None`` if it may be read.

    A body of unknown length is ``411``; one declared larger than
    ``max_bytes`` is ``413``.
    """
    length = request.body.length

    if length is None:
        return problem_response(
            Problem.for_status(
                HTTPStatus.LENGTH_REQUIRED,
                detail="The request body must declare its Content-Length.",
                instance=request.path,
            )
        )

    if length <= max_bytes:
        return None

    return problem_response(
        Problem(
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            title="Content too large",
            type=CONTENT_TOO_LARGE,
            detail=f"Request bodies are limited to {max_bytes} bytes; this one declares {length}.",
            instance=request.path,
            extensions={"max_bytes": max_bytes},
        )
    )


def signature_required_response(request: Request) -> Response:
    """The ``401`` for an unsigned request that needs a node identity (HandshakeProtocol §2.1)."""
    return problem_response(
        Problem(
            status=HTTPStatus.UNAUTHORIZED,
            title="Signature required",
            type=SIGNATURE_REQUIRED,
            detail="This request must carry the sending node's signature.",
            instance=request.path,
        )
    )


def invalid_signature_response(request: Request, reason: str | None) -> Response:
    """The ``401`` for a failed signature, which also ends the connection (HandshakeProtocol §5.3)."""
    response = problem_response(
        Problem(
            status=HTTPStatus.UNAUTHORIZED,
            title="Invalid signature",
            type=INVALID_SIGNATURE,
            detail=reason,
            instance=request.path,
        )
    )
    return replace(response, close=True)
