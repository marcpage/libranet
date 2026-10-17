"""Tests for what ``PUT /config/api/connecting`` accepts."""

from __future__ import annotations

from pytest import mark, raises

from libranet.protocol.connecting_requests import ConnectingRequest
from libranet.protocol.errors import InvalidRequestError


@mark.parametrize("only_trusted", [True, False])
def test_the_rule_asked_for_is_read_and_written_back(only_trusted: bool) -> None:
    asked = ConnectingRequest.from_value({"only_trusted": only_trusted, "later": 1})

    assert asked == ConnectingRequest(only_trusted)
    assert asked.value() == {"only_trusted": only_trusted}


@mark.parametrize("value", [None, [], {}, {"only_trusted": 1}, {"only_trusted": "true"}])
def test_anything_but_true_or_false_is_refused(value: object) -> None:
    with raises(InvalidRequestError, match="only_trusted"):
        ConnectingRequest.from_value(value)
