"""Tests for incremental parsing of pipelined responses."""

from __future__ import annotations

from pytest import mark, raises

from libranet.connections.errors import MalformedResponseError
from libranet.connections.response_parser import (
    MAX_HEAD_BYTES,
    PeerResponse,
    RequestLine,
    ResponseParser,
)

MAX_BODY_BYTES = 1024
GET = RequestLine("GET", "/data/sha256/0a")


def _parser(*chunks: bytes) -> ResponseParser:
    parser = ResponseParser(MAX_BODY_BYTES)

    for chunk in chunks:
        parser.feed(chunk)

    return parser


def _response(parser: ResponseParser, request: RequestLine = GET) -> PeerResponse:
    response = parser.next_response(request)
    assert response is not None
    return response


def test_response_framed_by_length() -> None:
    parser = _parser(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nhello"
    )

    response = _response(parser)

    assert response == PeerResponse(
        GET, 200, "OK", {"Content-Type": "text/plain", "Content-Length": "5"}, b"hello"
    )
    assert not parser.buffered
    assert parser.next_response(GET) is None


def test_pipelined_responses_come_out_in_order() -> None:
    parser = _parser(
        b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\none"
        b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nthree"
    )

    assert [(r.status, r.body) for r in (_response(parser) for _ in range(3))] == [
        (200, b"one"),
        (503, b""),
        (200, b"three"),
    ]


def test_each_response_names_the_request_it_answers() -> None:
    put = RequestLine("PUT", "/data/sha256/0a")
    head = RequestLine("HEAD", "/data/sha256/0b")
    seek = RequestLine("GET", "/data/seek")
    search = RequestLine("GET", "/data/search/0c?limit=1")
    nodes = RequestLine("GET", "/data/nodes")
    parser = _parser(
        b"HTTP/1.1 202 Accepted\r\nContent-Length: 0\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n"
        b"HTTP/1.1 100 Continue\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nseek"
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n6\r\nsearch\r\n0\r\n\r\n"
        b"HTTP/1.1 200 OK\r\n\r\nnodes until close"
    )

    responses = [_response(parser, request) for request in (put, head, seek, search)]

    assert parser.next_response(nodes) is None

    last = parser.finish(nodes)

    assert last is not None
    assert [(r.request, r.status, r.body) for r in [*responses, last]] == [
        (put, 202, b""),
        (head, 200, b""),
        (seek, 200, b"seek"),
        (search, 200, b"search"),
        (nodes, 200, b"nodes until close"),
    ]


def test_request_line_reads_as_method_and_target() -> None:
    assert str(RequestLine("GET", "/data/search/0c?limit=1")) == "GET /data/search/0c?limit=1"


def test_response_arriving_a_byte_at_a_time() -> None:
    raw = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
    parser = _parser()

    for index in range(len(raw) - 1):
        parser.feed(raw[index : index + 1])
        assert parser.next_response(GET) is None

    parser.feed(raw[-1:])

    assert _response(parser).body == b"hello"


def test_headers_are_case_insensitive_and_repeats_combined() -> None:
    parser = _parser(b"HTTP/1.1 200 OK\r\nX-Thing:  a \r\nx-thing: b\r\nContent-Length: 0\r\n\r\n")

    headers = _response(parser).headers

    assert headers["X-THING"] == "a, b"
    assert headers.get("x-request-path") is None


def test_status_line_without_reason() -> None:
    response = _response(_parser(b"HTTP/1.1 204\r\n\r\n"))

    assert (response.status, response.reason) == (204, "")


def test_head_response_has_no_body() -> None:
    parser = _parser(
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n"
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi"
    )

    assert _response(parser, RequestLine("HEAD", "/a")).body == b""
    assert _response(parser).body == b"hi"


@mark.parametrize("status", [204, 304])
def test_bodiless_statuses_ignore_content_length(status: int) -> None:
    parser = _parser(f"HTTP/1.1 {status} X\r\nContent-Length: 5\r\n\r\n".encode())

    assert _response(parser).body == b""
    assert not parser.buffered


def test_interim_responses_are_skipped() -> None:
    parser = _parser(
        b"HTTP/1.1 100 Continue\r\n\r\nHTTP/1.1 103 Early Hints\r\nLink: </a>\r\n\r\n"
        b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n"
    )

    assert _response(parser).status == 201


def test_chunked_body_with_extensions_and_trailers() -> None:
    parser = _parser(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"5;name=value\r\nhello\r\n1\r\n \r\nA \r\n0123456789\r\n0\r\nTrailer: x\r\n\r\n"
        b"HTTP/1.1 204 No Content\r\n\r\n"
    )

    assert _response(parser).body == b"hello 0123456789"
    assert _response(parser).status == 204


def test_chunked_body_waits_for_every_piece() -> None:
    raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n"

    for end in range(len(raw)):
        assert _parser(raw[:end]).next_response(GET) is None

    assert _response(_parser(raw)).body == b"abc"


def test_body_running_until_close() -> None:
    parser = _parser(b"HTTP/1.1 200 OK\r\n\r\nall of ", b"this")

    assert parser.next_response(GET) is None

    response = parser.finish(GET)

    assert response is not None
    assert response.body == b"all of this"
    assert response.closes_connection
    assert parser.finish(GET) is None


def test_finish_with_nothing_left_is_none() -> None:
    assert _parser().finish(GET) is None


@mark.parametrize(
    "partial",
    [
        b"HTTP/1.1 200 OK\r\nContent-",
        b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhel",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\nhel",
    ],
)
def test_close_partway_through_a_response_is_malformed(partial: bytes) -> None:
    parser = _parser(partial)

    assert parser.next_response(GET) is None

    with raises(MalformedResponseError):
        parser.finish(GET)


@mark.parametrize(
    ("head", "closes"),
    [
        (b"HTTP/1.1 200 OK", False),
        (b"HTTP/1.1 200 OK\r\nConnection: close", True),
        (b"HTTP/1.1 200 OK\r\nConnection: Upgrade, Close", True),
        (b"HTTP/1.0 200 OK", True),
        (b"HTTP/1.0 200 OK\r\nConnection: keep-alive", False),
    ],
)
def test_connection_close_is_reported(head: bytes, closes: bool) -> None:
    parser = _parser(head + b"\r\nContent-Length: 0\r\n\r\n")

    assert _response(parser).closes_connection is closes


@mark.parametrize(
    "raw",
    [
        b"HTTP/2 200 OK\r\n\r\n",
        b"HTTP/1.1 20 OK\r\n\r\n",
        b"garbage\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nNo colon\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nBad name: x\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX: 1\r\n folded\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nX: a\x00b\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 1, 2\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nContent-Length: 0x10\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nContent-Length: 3\r\n\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nabc\r\n",
        b"HTTP/1.1 101 Switching Protocols\r\n\r\n",
    ],
)
def test_malformed_responses(raw: bytes) -> None:
    with raises(MalformedResponseError):
        _parser(raw).next_response(GET)


def test_repeated_identical_content_length_is_accepted() -> None:
    parser = _parser(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\nok")

    assert _response(parser).body == b"ok"


def test_body_over_the_limit_is_refused_whatever_its_framing() -> None:
    at_limit = b"x" * MAX_BODY_BYTES
    over = at_limit + b"x"

    assert (
        _response(
            _parser(
                f"HTTP/1.1 200 OK\r\nContent-Length: {MAX_BODY_BYTES}\r\n\r\n".encode(), at_limit
            )
        ).body
        == at_limit
    )

    for raw in (
        f"HTTP/1.1 200 OK\r\nContent-Length: {len(over)}\r\n\r\n".encode(),
        b"HTTP/1.1 200 OK\r\n\r\n" + over,
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        + f"{len(at_limit):x}\r\n".encode()
        + at_limit
        + b"\r\n1\r\nx\r\n0\r\n\r\n",
    ):
        with raises(MalformedResponseError, match="exceeds"):
            _parser(raw).next_response(GET)


def test_oversized_head_is_refused() -> None:
    parser = _parser(b"HTTP/1.1 200 OK\r\nX: " + b"a" * MAX_HEAD_BYTES)

    with raises(MalformedResponseError, match="head exceeds"):
        parser.next_response(GET)


def test_oversized_chunked_framing_is_refused() -> None:
    parser = _parser(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n"
        + b"Trailer: x\r\n" * (MAX_HEAD_BYTES // 10)
    )

    with raises(MalformedResponseError, match="framing exceeds"):
        parser.next_response(GET)
