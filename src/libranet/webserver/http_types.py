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
from json import loads
from typing import Any, Mapping, Protocol

from libranet.identity.authentication import AuthenticationResult
from libranet.json_format import compact_json
from libranet.problems import PROBLEM_CONTENT_TYPE, Problem
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE, OCTET_STREAM
from libranet.webserver.inbound_peers import InboundConnection


class IncompleteBodyError(ConnectionError):
    """The client stopped sending before the whole declared body arrived."""


class UnsupportedMediaTypeError(ValueError):
    """A request's body does not say it is of the type it would be read as."""


class _Readable(Protocol):
    """Where body bytes come from: the connection's ``rfile``, or a buffer."""

    def read(self, size_bytes: int, /) -> bytes:
        """Up to ``size_bytes`` more of the body, or nothing once it has all been read."""
        ...


class RequestBody:
    """A request body, read from the connection only when a handler asks.

    ``length_bytes`` is the declared ``Content-Length``, or ``None`` when the body
    is sent with a ``Transfer-Encoding`` and its size is not known up front;
    such a body cannot be read. A body left unread means the connection
    cannot be reused, since its bytes would be parsed as the next request.
    """

    def __init__(self, length_bytes: int | None, stream: _Readable | None = None) -> None:
        if length_bytes is not None and length_bytes < 0:
            raise ValueError(f"length_bytes must not be negative, got {length_bytes}")

        if stream is None and length_bytes != 0:
            raise ValueError("A body that may be non-empty needs a stream")

        self._length_bytes = length_bytes
        self._stream = stream
        self._data: bytes | None = b"" if length_bytes == 0 else None

    @classmethod
    def of(cls, data: bytes) -> RequestBody:
        """A body holding ``data``, for calling handlers without a server."""
        return cls(len(data), BytesIO(data))

    @property
    def length_bytes(self) -> int | None:
        """The declared size in bytes, or ``None`` if not known in advance."""
        return self._length_bytes

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

        if self._length_bytes is None or self._stream is None:
            raise ValueError("A body of unknown length cannot be read")

        try:
            data = self._stream.read(self._length_bytes)

        except TimeoutError as error:
            raise IncompleteBodyError("Timed out reading the request body") from error

        if len(data) != self._length_bytes:
            raise IncompleteBodyError(
                f"Request body ended after {len(data)} of {self._length_bytes} bytes"
            )

        self._data = data
        return data


@dataclass(frozen=True)
class Request:  # pylint: disable=too-many-instance-attributes
    """An incoming request, reduced to what handlers need.

    ``path`` excludes any query string; ``params`` holds the named groups the
    route pattern matched. ``authentication`` is the outcome of checking the
    request's signature, once something has checked it. ``connection`` is
    the connection it arrived on, if the server keeps track of them.
    """

    method: str
    path: str
    params: Mapping[str, str] = field(default_factory=dict)
    headers: Mapping[str, str] = field(default_factory=dict)
    client_address: str = ""
    body: RequestBody = field(default_factory=lambda: RequestBody(0))
    authentication: AuthenticationResult | None = None
    connection: InboundConnection | None = None

    def header(self, name: str) -> str | None:
        """The value of the header ``name``, however it is capitalized, or ``None`` if not sent."""
        wanted = name.lower()

        for sent, value in self.headers.items():
            if sent.lower() == wanted:
                return value

        return None

    def json(self) -> object:
        """The JSON value the body carries, read only if the request says its body is JSON.

        A page on another site can send this node a body of any type a
        form sends without asking it first, but one of ``application/json``
        only once the node agrees (HttpApi §2.3.3). Parameters after the
        type, such as a ``charset``, are ignored.

        Raises:
            UnsupportedMediaTypeError: the ``Content-Type`` is not
                ``application/json``.
            ValueError: the body is not JSON, or its length is not known.
            IncompleteBodyError: the connection closed or timed out first.
        """
        content_type = self.header("Content-Type")
        media_type = (content_type or "").partition(";")[0].strip().lower()

        if media_type != JSON_CONTENT_TYPE:
            raise UnsupportedMediaTypeError(
                f"A request body's Content-Type must be {JSON_CONTENT_TYPE}, got {content_type!r}"
            )

        body = self.body.read()

        try:
            return loads(body)

        except ValueError as error:
            raise ValueError(f"The request body is not JSON: {error}") from None


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
    return Response(status, compact_json(value), {"Content-Type": JSON_CONTENT_TYPE})


def problem_response(problem: Problem, headers: Mapping[str, str] | None = None) -> Response:
    """An error response whose status and body both come from ``problem``."""
    return Response(
        problem.status,
        problem.to_json(),
        {"Content-Type": PROBLEM_CONTENT_TYPE, **(headers or {})},
    )
