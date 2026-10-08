"""Tests for reading what ``POST /data/drop`` asks for."""

from __future__ import annotations
from base64 import b64encode

from pytest import mark, raises

from libranet.cas.drops import TARGET_BITS, DropTarget
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.errors import InvalidConfigRequestError


def test_a_drop_of_text_names_its_target_string_and_the_search_asked_for() -> None:
    asked = DropRequest.from_value(
        {"target": "user:alice", "text": "héllo", "seconds": 5, "minimum_bits": 16}
    )

    assert asked == DropRequest(DropTarget.of("user:alice"), "héllo".encode("utf-8"), 5.0, 16)
    assert isinstance(asked.seconds, float)


def test_a_drop_of_base64_and_no_minimum_matches_none() -> None:
    data = bytes(range(256))

    asked = DropRequest.from_value(
        {"target": "t", "base64": b64encode(data).decode("ascii"), "seconds": 0.5}
    )

    assert (asked.data, asked.seconds, asked.minimum_bits) == (data, 0.5, 0)


@mark.parametrize(
    "value, complaint",
    [
        ([], "must be a JSON object"),
        ({"text": "x", "seconds": 1}, '"target" must be a string'),
        ({"target": "", "text": "x", "seconds": 1}, '"target" must be a string, not empty'),
        ({"target": 7, "text": "x", "seconds": 1}, '"target" must be a string'),
        ({"target": "user:\ud800", "text": "x", "seconds": 1}, '"target" is not UTF-8'),
        ({"target": "t", "text": "x"}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": "5"}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": True}, '"seconds" must be a number'),
        ({"target": "t", "text": "x", "seconds": -1}, '"seconds" must be a number, not negative'),
        ({"target": "t", "text": "x", "seconds": float("nan")}, "not negative, got nan"),
        ({"target": "t", "text": "x", "seconds": float("inf")}, "not negative, got inf"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": 1.5}, "must be an integer"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": True}, "must be an integer"),
        ({"target": "t", "text": "x", "seconds": 1, "minimum_bits": -1}, "from 0 to 256"),
        (
            {"target": "t", "text": "x", "seconds": 1, "minimum_bits": TARGET_BITS + 1},
            "from 0 to 256",
        ),
        ({"target": "t", "seconds": 1}, 'given as "text" or as "base64"'),
        ({"target": "t", "text": "x", "base64": "eA==", "seconds": 1}, '"text" or as "base64"'),
        ({"target": "t", "text": 5, "seconds": 1}, '"text" or as "base64", a string'),
        ({"target": "t", "text": "\udfff", "seconds": 1}, 'The "text" of a drop is not UTF-8'),
        (
            {"target": "t", "base64": "not base64!", "seconds": 1},
            '"base64" of a drop is not base64',
        ),
    ],
)
def test_a_body_that_is_no_usable_drop_is_refused(value: object, complaint: str) -> None:
    with raises(InvalidConfigRequestError, match=complaint.replace("(", r"\(")):
        DropRequest.from_value(value)
