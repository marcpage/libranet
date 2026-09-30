"""Tests for the request body handlers read lazily, and what a request says of itself."""

from __future__ import annotations
from io import BytesIO

from pytest import mark, raises

from libranet.webserver.http_types import (
    IncompleteBodyError,
    Request,
    RequestBody,
    UnsupportedMediaTypeError,
)


class StalledStream:
    """A connection whose client never sends the body."""

    def read(self, size: int, /) -> bytes:
        raise TimeoutError("timed out")


def test_body_is_not_read_until_asked() -> None:
    stream = BytesIO(b"payload and the next request")
    body = RequestBody(7, stream)
    consumed_before_reading = body.consumed

    assert body.length_bytes == 7
    assert stream.tell() == 0
    assert body.read() == b"payload"
    assert not consumed_before_reading
    assert body.consumed
    assert stream.read() == b" and the next request"


def test_reading_again_returns_the_same_bytes() -> None:
    body = RequestBody.of(b"payload")

    assert body.read() == b"payload"
    assert body.read() == b"payload"


def test_empty_body_is_already_consumed() -> None:
    body = RequestBody(0)

    assert body.consumed
    assert body.read() == b""
    assert Request("GET", "/").body.consumed


def test_body_of_unknown_length_cannot_be_read() -> None:
    body = RequestBody(None, BytesIO(b"5\r\nhello\r\n0\r\n\r\n"))

    assert body.length_bytes is None
    assert not body.consumed

    with raises(ValueError):
        body.read()


def test_short_body_is_incomplete() -> None:
    body = RequestBody(10, BytesIO(b"short"))

    with raises(IncompleteBodyError, match="5 of 10"):
        body.read()

    assert not body.consumed


def test_stalled_body_is_incomplete() -> None:
    body = RequestBody(10, StalledStream())

    with raises(IncompleteBodyError, match="Timed out"):
        body.read()


def test_invalid_bodies_are_refused() -> None:
    with raises(ValueError):
        RequestBody(-1, BytesIO())

    with raises(ValueError):
        RequestBody(5)

    with raises(ValueError):
        RequestBody(None)


def json_request(body: bytes, content_type: str | None = "application/json") -> Request:
    headers = {} if content_type is None else {"Content-Type": content_type}
    return Request("POST", "/config/api/backups", headers=headers, body=RequestBody.of(body))


@mark.parametrize("name", ["Sec-Fetch-Site", "sec-fetch-site", "SEC-FETCH-SITE"])
def test_a_header_is_found_however_it_is_capitalized(name: str) -> None:
    request = Request("GET", "/", headers={"Host": "localhost", "sec-Fetch-site": "none"})

    assert request.header(name) == "none"


def test_a_header_not_sent_is_none() -> None:
    assert Request("GET", "/", headers={"Host": "localhost"}).header("Origin") is None


@mark.parametrize(
    "content_type",
    [
        "application/json",
        "Application/JSON",
        "application/json; charset=utf-8",
        " application/json ;x",
    ],
)
def test_a_body_that_says_it_is_json_is_read_as_json(content_type: str) -> None:
    assert json_request(b'{"directory": "/home/me"}', content_type).json() == {
        "directory": "/home/me"
    }


def test_the_content_type_is_found_however_the_header_is_capitalized() -> None:
    request = Request(
        "POST", "/", headers={"content-type": "application/json"}, body=RequestBody.of(b"[1]")
    )

    assert request.json() == [1]


@mark.parametrize(
    "content_type",
    [
        None,
        "",
        "text/plain",
        "text/plain; application/json",
        "application/x-www-form-urlencoded",
        "multipart/form-data; boundary=x",
        "application/problem+json",
        "application/jsonp",
        "text/json",
    ],
)
def test_a_body_that_does_not_say_it_is_json_is_not_read_as_json(
    content_type: str | None,
) -> None:
    with raises(UnsupportedMediaTypeError, match="application/json"):
        json_request(b'{"directory": "/home/me"}', content_type).json()


@mark.parametrize("body", [b"{not json", b"", b"\xff\xfe"])
def test_a_body_that_says_it_is_json_and_is_not_is_refused(body: bytes) -> None:
    with raises(ValueError, match="not JSON") as raised:
        json_request(body).json()

    assert not isinstance(raised.value, UnsupportedMediaTypeError)
