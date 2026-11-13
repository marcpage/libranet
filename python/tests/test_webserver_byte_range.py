"""Tests for reading the one byte range a ``Range`` header asks for."""

from __future__ import annotations
from logging import DEBUG

from pytest import LogCaptureFixture, mark, raises

from libranet.webserver.byte_range import ByteRange

FILE_BYTES = 1000


@mark.parametrize(
    "header, start_bytes, stop_bytes",
    [
        ("bytes=0-499", 0, 500),
        ("bytes=500-", 500, FILE_BYTES),
        ("bytes=-300", 700, FILE_BYTES),
        ("bytes=999-999", 999, FILE_BYTES),
        ("bytes=0-", 0, FILE_BYTES),
        ("Bytes = 10-19 ", 10, 20),
        ("bytes=,10-19,", 10, 20),
        ("bytes=007-008", 7, 9),
    ],
)
def test_each_form_of_a_range_is_read(header: str, start_bytes: int, stop_bytes: int) -> None:
    assert ByteRange.from_header(header, FILE_BYTES) == ByteRange(
        start_bytes, stop_bytes, FILE_BYTES
    )


@mark.parametrize(
    "header, start_bytes",
    [("bytes=900-1999", 900), ("bytes=-5000", 0)],
)
def test_a_range_running_past_the_end_is_cut_at_the_end(header: str, start_bytes: int) -> None:
    assert ByteRange.from_header(header, FILE_BYTES) == ByteRange(
        start_bytes, FILE_BYTES, FILE_BYTES
    )


@mark.parametrize(
    "header, file_bytes",
    [
        ("bytes=1000-", FILE_BYTES),
        ("bytes=1000-1500", FILE_BYTES),
        ("bytes=5000-6000", FILE_BYTES),
        ("bytes=-0", FILE_BYTES),
        ("bytes=0-", 0),
        ("bytes=-5", 0),
    ],
)
def test_a_range_holding_no_bytes_of_the_file_is_unsatisfiable(
    header: str, file_bytes: int
) -> None:
    byte_range = ByteRange.from_header(header, file_bytes)

    assert byte_range is not None
    assert not byte_range.satisfiable
    assert byte_range.content_range() == f"bytes */{file_bytes}"


@mark.parametrize("header", [None, "bytes=0-4,10-14", "bytes=0-4, -5", "items=0-4"])
def test_no_header_more_than_one_range_or_another_unit_is_ignored_unlogged(
    caplog: LogCaptureFixture, header: str | None
) -> None:
    caplog.set_level(DEBUG)

    assert ByteRange.from_header(header, FILE_BYTES) is None
    assert caplog.records == []


@mark.parametrize(
    "header",
    [
        "bytes",
        "0-499",
        "bytes=",
        "bytes=,",
        "bytes=-",
        "bytes=abc",
        "bytes=5-4",
        "bytes=1-2-3",
        "bytes=+1-2",
        "bytes=0x1-2",
        "bytes=1 -2",
        "bytes=٣-4",
        f"bytes={'9' * 5000}-",
    ],
)
def test_a_header_that_cannot_be_parsed_is_ignored_and_logged_at_debug(
    caplog: LogCaptureFixture, header: str
) -> None:
    caplog.set_level(DEBUG)

    assert ByteRange.from_header(header, FILE_BYTES) is None
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert record.getMessage().startswith(f"Ignoring the Range header {header!r}: ")


@mark.parametrize(
    "byte_range, content_range",
    [
        (ByteRange(0, 500, FILE_BYTES), "bytes 0-499/1000"),
        (ByteRange(999, 1000, FILE_BYTES), "bytes 999-999/1000"),
        (ByteRange(1000, 1000, FILE_BYTES), "bytes */1000"),
    ],
)
def test_the_content_range_names_the_first_and_last_bytes_sent(
    byte_range: ByteRange, content_range: str
) -> None:
    assert byte_range.content_range() == content_range


@mark.parametrize(
    "start_bytes, stop_bytes, file_bytes",
    [(-1, 5, 10), (6, 5, 10), (0, 11, 10), (0, 0, -1)],
)
def test_a_range_must_lie_within_its_file(
    start_bytes: int, stop_bytes: int, file_bytes: int
) -> None:
    with raises(ValueError, match="must lie within the file"):
        ByteRange(start_bytes, stop_bytes, file_bytes)
