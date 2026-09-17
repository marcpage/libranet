"""Tests for the request body handlers read lazily."""

from __future__ import annotations
from io import BytesIO

from pytest import raises

from libranet.webserver.http_types import IncompleteBodyError, Request, RequestBody


class StalledStream:
    """A connection whose client never sends the body."""

    def read(self, size: int, /) -> bytes:
        raise TimeoutError("timed out")


def test_body_is_not_read_until_asked() -> None:
    stream = BytesIO(b"payload and the next request")
    body = RequestBody(7, stream)
    consumed_before_reading = body.consumed

    assert body.length == 7
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

    assert body.length is None
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
