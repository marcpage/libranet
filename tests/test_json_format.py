"""Tests for compact JSON."""

from __future__ import annotations
from json import loads

from pytest import raises

from libranet.json_format import compact_json


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
