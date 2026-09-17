"""The request and response values route handlers work with.

Handlers never touch the socket or ``BaseHTTPRequestHandler``: they take a
:class:`Request` and return a :class:`Response`, which keeps them testable
without a running server.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from http import HTTPStatus
from json import dumps
from typing import Any, Final, Mapping

from libranet.problems import PROBLEM_CONTENT_TYPE, Problem

OCTET_STREAM: Final = "application/octet-stream"
JSON_CONTENT_TYPE: Final = "application/json"


@dataclass(frozen=True)
class Request:
    """An incoming request, reduced to what handlers need.

    ``path`` excludes any query string; ``params`` holds the named groups the
    route pattern matched.
    """

    method: str
    path: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    client_address: str = ""


@dataclass(frozen=True)
class Response:
    """A complete response; ``Content-Length`` is added when it is sent."""

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)


def bytes_response(
    body: bytes,
    content_type: str = OCTET_STREAM,
    headers: Mapping[str, str] | None = None,
) -> Response:
    """A ``200 OK`` carrying ``body``."""
    return Response(HTTPStatus.OK, body, {"Content-Type": content_type, **(headers or {})})


def json_response(value: Any, status: int = HTTPStatus.OK) -> Response:
    """``value`` serialized as a JSON body."""
    body = dumps(value, separators=(",", ":")).encode("utf-8")
    return Response(status, body, {"Content-Type": JSON_CONTENT_TYPE})


def problem_response(problem: Problem, headers: Mapping[str, str] | None = None) -> Response:
    """An error response whose status and body both come from ``problem``."""
    return Response(
        problem.status,
        problem.to_json(),
        {"Content-Type": PROBLEM_CONTENT_TYPE, **(headers or {})},
    )
