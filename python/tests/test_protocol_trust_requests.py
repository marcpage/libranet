"""Tests for what changing an entry of a trust list accepts (HttpApi §11.7)."""

from __future__ import annotations

from pytest import mark, raises

from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.trust_requests import TrustRequest


@mark.parametrize("trusted", [True, False])
def test_trust_or_distrust_is_read(trusted: bool) -> None:
    assert TrustRequest.from_value({"trust": trusted}) == TrustRequest(trusted)


def test_members_it_does_not_know_are_ignored() -> None:
    assert TrustRequest.from_value({"trust": True, "note": "a friend"}) == TrustRequest(True)


@mark.parametrize("value", [[True], True, {}, {"trust": None}, {"trust": 1}, {"trust": "true"}])
def test_anything_but_an_object_trusting_or_distrusting_is_refused(value: object) -> None:
    with raises(InvalidRequestError, match='"trust"'):
        TrustRequest.from_value(value)
