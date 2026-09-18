"""Incremental parsing of pipelined HTTP/1.1 responses (Step 10).

Bytes are fed in as they arrive and complete responses are taken out in
order. Whether a response has a body depends on the request it answers (a
response to ``HEAD`` never does, RFC 9112 §6.3), so the caller names that
request's method each time it asks for the next response.

A body is framed by ``Content-Length``, by chunked transfer coding, or by
the peer closing the connection. The client never offers any other transfer
coding, so no other is accepted. Interim (``1xx``) responses are skipped.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from http import HTTPStatus
from re import compile as compile_pattern
from typing import Final, Mapping

from http_message_signatures.structures import CaseInsensitiveDict

from libranet.connections.errors import MalformedResponseError

# The most bytes a status line and header section, or any single line of
# chunked framing, may take. Libranet responses need a small fraction of it.
MAX_HEAD_BYTES: Final = 64 * 1024

_LINE_END: Final = b"\r\n"
_HEAD_END: Final = b"\r\n\r\n"
_STATUS_LINE: Final = compile_pattern(r"HTTP/1\.([0-9]) ([0-9]{3})(?: (.*))?")
# RFC 9110 §5.6.2.
_TOKEN: Final = compile_pattern(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
# Field content may hold anything but control characters other than tab.
_FIELD_VALUE: Final = compile_pattern(r"[^\x00-\x08\x0a-\x1f\x7f]*")
_CHUNK_SIZE: Final = compile_pattern(rb"[0-9A-Fa-f]{1,16}")
_BODILESS_STATUSES: Final = frozenset({HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED})


@dataclass(frozen=True)
class PeerResponse:
    """One complete response from a peer.

    ``headers`` are looked up case-insensitively; a field sent more than once
    has its values joined with commas (RFC 9110 §5.3). ``closes_connection``
    is set when the peer sends nothing more on the connection after it.
    """

    status: int
    reason: str
    headers: Mapping[str, str]
    body: bytes = b""
    closes_connection: bool = False


class _Framing(Enum):
    """How the end of a response's body is found."""

    LENGTH = "length"
    CHUNKED = "chunked"
    UNTIL_CLOSE = "until close"


@dataclass(frozen=True)
class _Head:
    """A parsed status line and header section, and how its body is framed.

    ``size`` counts every byte up to and including the blank line;
    ``length`` is the body's size when framed by length.
    """

    status: int
    reason: str
    headers: Mapping[str, str]
    closes_connection: bool
    size: int
    framing: _Framing
    length: int = 0


class ResponseParser:
    """Splits the bytes received on one connection into responses.

    Bodies over ``max_body_bytes`` are refused, however they are framed.
    """

    def __init__(self, max_body_bytes: int) -> None:
        self._max_body_bytes = max_body_bytes
        self._buffer = bytearray()

    @property
    def buffered(self) -> bool:
        """Whether bytes are held that no returned response accounted for."""
        return bool(self._buffer)

    def feed(self, data: bytes) -> None:
        """Add bytes received from the connection."""
        self._buffer += data

    def next_response(self, method: str) -> PeerResponse | None:
        """The response to a ``method`` request, or ``None`` until all of it has arrived.

        Raises:
            MalformedResponseError: the bytes received are not a usable response.
        """
        head = self._final_head(method)

        if head is None:
            return None

        if head.framing is _Framing.LENGTH:
            end = head.size + head.length

            if len(self._buffer) < end:
                return None

            return self._take(head, bytes(self._buffer[head.size : end]), end)

        if head.framing is _Framing.CHUNKED:
            chunked = self._chunked_body(head.size)
            return None if chunked is None else self._take(head, *chunked)

        if len(self._buffer) - head.size > self._max_body_bytes:
            raise MalformedResponseError(f"Response body exceeds {self._max_body_bytes} bytes")

        return None

    def finish(self, method: str) -> PeerResponse | None:
        """The last response once the peer has closed the connection, if one remains.

        For use once :meth:`next_response` has returned everything it can:
        what remains is either nothing or a response whose body ran until the
        connection closed.

        Raises:
            MalformedResponseError: the connection closed partway through a response.
        """
        if not self._buffer:
            return None

        head = self._final_head(method)

        if head is None or head.framing is not _Framing.UNTIL_CLOSE:
            raise MalformedResponseError("The connection closed partway through a response")

        return self._take(head, bytes(self._buffer[head.size :]), len(self._buffer))

    def _take(self, head: _Head, body: bytes, end: int) -> PeerResponse:
        """``head``'s response with ``body``, after dropping its ``end`` bytes."""
        del self._buffer[:end]
        return PeerResponse(head.status, head.reason, head.headers, body, head.closes_connection)

    def _final_head(self, method: str) -> _Head | None:
        """The next non-interim response head, dropping interim ones, if it has arrived."""
        while True:
            head = self._head(method)

            if head is None or head.status >= HTTPStatus.OK:
                return head

            if head.status == HTTPStatus.SWITCHING_PROTOCOLS:
                raise MalformedResponseError("The peer switched protocols unasked")

            del self._buffer[: head.size]

    def _head(self, method: str) -> _Head | None:
        """The response head at the start of the buffer, if it has fully arrived."""
        end = self._buffer.find(_HEAD_END, 0, MAX_HEAD_BYTES)

        if end < 0:
            if len(self._buffer) >= MAX_HEAD_BYTES:
                raise MalformedResponseError(f"Response head exceeds {MAX_HEAD_BYTES} bytes")

            return None

        status_line, *lines = self._buffer[:end].decode("latin-1").split("\r\n")
        match = _STATUS_LINE.fullmatch(status_line)

        if match is None:
            raise MalformedResponseError(f"Invalid status line {status_line[:80]!r}")

        minor_version, status_text, reason = match.groups()
        status = int(status_text)
        headers = CaseInsensitiveDict()

        for line in lines:
            name, colon, value = line.partition(":")
            value = value.strip(" \t")

            if not colon or not _TOKEN.fullmatch(name) or not _FIELD_VALUE.fullmatch(value):
                raise MalformedResponseError(f"Invalid header line {line[:80]!r}")

            headers[name] = f"{headers[name]}, {value}" if name in headers else value

        options = {token.strip().lower() for token in headers.get("Connection", "").split(",")}
        closes = "close" in options or (minor_version == "0" and "keep-alive" not in options)
        framing, length = self._framing(method, status, headers)
        return _Head(
            status,
            reason or "",
            headers,
            closes or framing is _Framing.UNTIL_CLOSE,
            end + len(_HEAD_END),
            framing,
            length,
        )

    def _framing(
        self, method: str, status: int, headers: Mapping[str, str]
    ) -> tuple[_Framing, int]:
        """How the body is framed, and its size when framed by length (RFC 9112 §6.3)."""
        if method == "HEAD" or status < HTTPStatus.OK or status in _BODILESS_STATUSES:
            return _Framing.LENGTH, 0

        if "Transfer-Encoding" in headers:
            if "Content-Length" in headers:
                raise MalformedResponseError("Response has both Transfer-Encoding and length")

            if headers["Transfer-Encoding"].strip().lower() != "chunked":
                raise MalformedResponseError(
                    f"Unsupported Transfer-Encoding {headers['Transfer-Encoding']!r}"
                )

            return _Framing.CHUNKED, 0

        if "Content-Length" in headers:
            return _Framing.LENGTH, self._content_length(headers["Content-Length"])

        return _Framing.UNTIL_CLOSE, 0

    def _content_length(self, field: str) -> int:
        """The body size a ``Content-Length`` field declares."""
        values = {value.strip() for value in field.split(",")}

        if len(values) != 1:
            raise MalformedResponseError(f"Conflicting Content-Length {field!r}")

        (value,) = values

        if not (value.isascii() and value.isdecimal()):
            raise MalformedResponseError(f"Invalid Content-Length {field!r}")

        if int(value) > self._max_body_bytes:
            raise MalformedResponseError(f"Response body exceeds {self._max_body_bytes} bytes")

        return int(value)

    def _chunked_body(self, start: int) -> tuple[bytes, int] | None:
        """The body chunked from ``start`` and where it ends, if all of it has arrived.

        Chunk extensions and trailer fields are ignored (RFC 9112 §7.1). The
        framing around the data (sizes, line breaks, trailers) is held to
        :data:`MAX_HEAD_BYTES` in all, so tiny chunks cannot stretch a body
        far past its size limit.
        """
        chunks: list[bytes] = []
        size = 0
        position = start

        while True:
            line = self._framing_line(start, position, size)

            if line is None:
                return None

            size_line, position = line
            size_text = size_line.split(b";", 1)[0].strip(b" \t")

            if not _CHUNK_SIZE.fullmatch(size_text):
                raise MalformedResponseError(f"Invalid chunk size line {size_line[:80]!r}")

            chunk_size = int(size_text, 16)

            if chunk_size == 0:
                break

            size += chunk_size
            end = position + chunk_size

            if size > self._max_body_bytes:
                raise MalformedResponseError(f"Response body exceeds {self._max_body_bytes} bytes")

            if len(self._buffer) < end + len(_LINE_END):
                return None

            if self._buffer[end : end + len(_LINE_END)] != _LINE_END:
                raise MalformedResponseError("Chunk data does not end where its size says")

            chunks.append(bytes(self._buffer[position:end]))
            position = end + len(_LINE_END)

        while True:
            line = self._framing_line(start, position, size)

            if line is None:
                return None

            trailer, position = line

            if not trailer:
                return b"".join(chunks), position

    def _framing_line(self, start: int, position: int, size: int) -> tuple[bytes, int] | None:
        """The chunked-framing line at ``position``, and where the next line starts.

        ``start`` is where the chunked body starts and ``size`` how many data
        bytes it has held so far, which together bound the framing allowed.
        """
        allowed = start + size + MAX_HEAD_BYTES
        end = self._buffer.find(_LINE_END, position, allowed)

        if end < 0:
            if len(self._buffer) >= allowed:
                raise MalformedResponseError(f"Chunked framing exceeds {MAX_HEAD_BYTES} bytes")

            return None

        return bytes(self._buffer[position:end]), end + len(_LINE_END)
