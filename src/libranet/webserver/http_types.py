"""The request and response values route handlers work with.

Handlers never touch the socket or ``BaseHTTPRequestHandler``: they take a
:class:`Request` and return a :class:`Response`, which keeps them testable
without a running server. A request body is read from the connection only if
the handler asks for it, so a handler can refuse an oversized body unread.
A response body may likewise be produced only as it is sent, as an
application file is, from its parts (Phase 3 Step 65).

The responses every handler may give are built here too, as is what more
than one reads from a request's path.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from http import HTTPStatus
from io import BytesIO
from json import loads
from logging import getLogger
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import unquote

from libranet.cas.content_id import ContentId
from libranet.identity.authentication import AuthenticationResult
from libranet.json_format import compact_json
from libranet.problems import PROBLEM_CONTENT_TYPE, Problem
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE, OCTET_STREAM
from libranet.webserver.errors import IncompleteBodyError, UnsupportedMediaTypeError
from libranet.webserver.inbound_peers import InboundConnection

_LOGGER = getLogger(__name__)


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

    def carries_json(self) -> bool:
        """Whether the request says its body is JSON: its ``Content-Type`` is ``application/json``.

        A page on another site can send this node a body of any type a
        form sends without asking it first, but one of ``application/json``
        only once the node agrees (HttpApi §2.3.3). Parameters after the
        type, such as a ``charset``, are ignored.
        """
        media_type = (self.header("Content-Type") or "").partition(";")[0].strip().lower()
        return media_type == JSON_CONTENT_TYPE

    def require_json(self) -> None:
        """Raise unless the request :meth:`carries_json`.

        Raises:
            UnsupportedMediaTypeError: the ``Content-Type`` is not
                ``application/json``.
        """
        if not self.carries_json():
            raise UnsupportedMediaTypeError(
                f"A request body's Content-Type must be {JSON_CONTENT_TYPE}, "
                f"got {self.header('Content-Type')!r}"
            )

    def json(self) -> object:
        """The JSON value the body carries, read only if the request :meth:`carries_json`.

        Raises:
            UnsupportedMediaTypeError: the ``Content-Type`` is not
                ``application/json``.
            ValueError: the body is not JSON, or its length is not known.
            IncompleteBodyError: the connection closed or timed out first.
        """
        self.require_json()
        body = self.body.read()

        try:
            return loads(body)

        except ValueError as error:
            raise ValueError(f"The request body is not JSON: {error}") from None


@dataclass(frozen=True)
class StreamedBody:
    """A response body sent as ``chunks`` produces it, rather than held whole.

    ``length_bytes`` is how long it is, or ``None`` if that is not known, in
    which case it is sent until the connection closes. ``chunks`` raises
    :class:`~libranet.webserver.errors.ResponseCutShortError` to end the body
    short, and the connection with it, having logged why.

    Raises:
        ValueError: ``length_bytes`` is negative.
    """

    length_bytes: int | None
    chunks: Iterable[bytes]

    def __post_init__(self) -> None:
        if self.length_bytes is not None and self.length_bytes < 0:
            raise ValueError(f"length_bytes must not be negative, got {self.length_bytes}")


@dataclass(frozen=True)
class Response:
    """A response; ``Content-Length`` is added when it is sent.

    ``close`` ends the connection once the response is sent, as when a
    peer's signature fails to verify (HandshakeProtocol §5.3). A response
    with a ``stream`` sends it in place of ``body``, which is then empty.

    Raises:
        ValueError: both ``body`` and ``stream`` are given.
    """

    status: int
    body: bytes = b""
    headers: Mapping[str, str] = field(default_factory=dict)
    close: bool = False
    stream: StreamedBody | None = None

    def __post_init__(self) -> None:
        if self.stream is not None and self.body:
            raise ValueError("A response sends its body or a stream, not both")


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


def status_response(
    request: Request, status: HTTPStatus, detail: str, *, headers: Mapping[str, str] | None = None
) -> Response:
    """The error response to ``request`` that ``status`` describes, ``detail`` saying why.

    Its problem is ``about:blank``, titled with the status phrase.
    """
    return problem_response(Problem.for_status(status, detail, request.path), headers)


def redirect_response(location: str) -> Response:
    """A ``302`` to ``location``."""
    return Response(HTTPStatus.FOUND, headers={"Location": location})


def entity_tag(content_id: ContentId) -> str:
    """The strong ``ETag`` of what hashes to ``content_id``, naming its algorithm and hash."""
    return f'"{content_id.algorithm}-{content_id.hash}"'


def percent_decoded(text: str) -> str | None:
    """``text`` percent-decoded, or ``None`` if what it encodes is not UTF-8."""
    try:
        return unquote(text, errors="strict")

    except UnicodeDecodeError as error:
        _LOGGER.debug("%r does not percent-encode UTF-8: %s", text, error)
        return None
