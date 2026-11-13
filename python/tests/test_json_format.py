"""Tests for compact JSON, JSON indented for people, and moments."""

from __future__ import annotations
from datetime import UTC, datetime
from json import loads

from pytest import mark, raises

from libranet.json_format import (
    compact_json,
    moment_at,
    moment_text,
    parsed_moment,
    pretty_json,
)


def test_compact_json_has_no_space_after_a_comma_or_a_colon() -> None:
    assert compact_json({"nodes": {"a": 1, "b": [2, 3]}}) == b'{"nodes":{"a":1,"b":[2,3]}}'


def test_compact_json_is_ascii_whatever_the_text() -> None:
    body = compact_json({"name": "caf\u00e9"})

    assert body == b'{"name":"caf\\u00e9"}'
    assert loads(body) == {"name": "caf\u00e9"}


def test_compact_json_sorts_keys_when_asked() -> None:
    assert compact_json({"b": 1, "a": {"d": 2, "c": 3}}, sort_keys=True) == (
        b'{"a":{"c":3,"d":2},"b":1}'
    )


def test_compact_json_refuses_what_is_not_json_when_asked() -> None:
    assert compact_json([float("nan")]) == b"[NaN]"

    with raises(ValueError):
        compact_json([float("inf")], allow_nan=False)


def test_pretty_json_is_indented_and_ends_in_a_newline() -> None:
    assert pretty_json({"jobs": [1]}) == b'{\n  "jobs": [\n    1\n  ]\n}\n'


def test_a_moment_is_written_in_utc_to_the_second() -> None:
    # 2026-10-14T12:00:00Z, and a little more.
    assert moment_text(1_791_979_200.75) == "2026-10-14T12:00:00Z"


def test_a_moment_at_a_time_is_in_utc_to_the_second() -> None:
    assert moment_at(1_791_979_200.75) == datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)


def test_a_moment_written_reads_back_in_utc() -> None:
    assert parsed_moment(moment_text(1_791_979_200)) == moment_at(1_791_979_200)


@mark.parametrize("text", ["", "2026-10-14", "2026-10-14T12:00:00", "2026-10-14 12:00:00Z"])
def test_what_is_no_moment_is_refused(text: str) -> None:
    with raises(ValueError):
        parsed_moment(text)
