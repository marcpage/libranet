"""Tests for compact JSON."""

from __future__ import annotations
from json import loads

from libranet.json_format import compact_json


def test_compact_json_has_no_space_after_a_comma_or_a_colon() -> None:
    assert compact_json({"nodes": {"a": 1, "b": [2, 3]}}) == b'{"nodes":{"a":1,"b":[2,3]}}'


def test_compact_json_is_ascii_whatever_the_text() -> None:
    body = compact_json({"name": "caf\u00e9"})

    assert body == b'{"name":"caf\\u00e9"}'
    assert loads(body) == {"name": "caf\u00e9"}
