"""The request and response values route handlers work with.

Handlers never touch the socket or ``BaseHTTPRequestHandler``: they take a
:class:`Request` and return a :class:`Response`, which keeps them testable
without a running server. A request body is read from the connection only if
the handler asks for it, so a handler can refuse an oversized body unread.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from http import HTTPStatus
from io import BytesIO
from json import dumps
from typing import Any, Final, Mapping, Protocol

from libranet.identity.authentication import AuthenticationResult
from libranet.problems import PROBLEM_CONTENT_TYPE, Problem

OCTET_STREAM: Final = "application/octet-stream"
JSON_CONTENT_TYPE: Final = "application/json"


class IncompleteBodyError(ConnectionError):
    """The client stopped sending before the whole declared body arrived."""


class _Readable(Protocol):
    """Where body bytes come from: the connection's ``rfile``, or a buffer."""

    def read(self, size: int, /) -> bytes: ...


class RequestBody:
    """A request body, read from the connection only when a handler asks.

    ``length`` is the declared ``Content-Length``, or ``None`` when the body
    is sent with a ``Transfer-Encoding`` and its size is not known up front;
    such a body cannot be read. A body left unread means the connection
    cannot be reused, since its bytes would be parsed as the next request.
    """

    def __init__(self, length: int | None, stream: _Readable | None = None) -> None:
        if length is not None and length < 0:
            raise ValueError(f"length must not be negative, got {length}")

        if stream is None and length != 0:
            raise ValueError("A body that may be non-empty needs a stream")

        self._length = length
        self._stream = stream
        self._data: bytes | None = b"" if length == 0 else None

    @classmethod
    def of(cls, data: bytes) -> RequestBody:
        """A body holding ``data``, for calling handlers without a server."""
        return cls(len(data), BytesIO(data))

    @property
    def length(self) -> int | None:
        """The declared size in bytes, or ``None`` if not known in advance."""
        return self._length

    @property
    def consumed(self) -> bool:
        """Whether no unread body bytes remain on the connection."""
        return self._data is not None

    def read(self) -> bytes:
        """The whole body; later calls return the same bytes.

        Raises:
            ValueError: the body's length is not known.
            IncompleteBodyError: the connection closed or timed out first.
        """
        if self._data is not None:
            return self._data

        if self._length is None or self._stream is None:
            raise ValueError("A body of unknown length cannot be read")

        try:
            data = self._stream.read(self._length)

        except TimeoutError as error:
            raise IncompleteBodyError("Timed out reading the request body") from error

        if len(data) != self._length:
            raise IncompleteBodyError(
                f"Request body ended after {len(data)} of {self._length} bytes"
            )

        self._data = data
        return data


@dataclass(frozen=True)
class Request:
    """An incoming request, reduced to what handlers need.

    ``path`` excludes any query string; ``params`` holds the named groups the
    route pattern matched. ``authentication`` is the outcome of checking the
    request's signature, once something has checked it.
    """

    method: str
    path: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    client_address: str = ""
    body: RequestBody = field(default_factory=lambda: RequestBody(0))
    authentication: AuthenticationResult | None = None


@dataclass(frozen=True)
class Response:
    """A complete response; ``Content-Length`` is added when it is sent.

    ``close`` ends the connection once the response is sent, as when a
    peer's signature fails to verify (HandshakeProtocol §5.3).
    """

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    close: bool = False


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
