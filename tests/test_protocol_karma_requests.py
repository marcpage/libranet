"""Tests for what paying and staking as a person accept (HttpApi §10.10)."""

from __future__ import annotations
from datetime import UTC, datetime

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.karma_amounts import MAX_KISMET, Kismet
from libranet.protocol.karma_requests import PaymentRequest, StakeRequest
from libranet.protocol.karma_stakes import Stake
from libranet.protocol.karma_transactions import Transaction

ALICE = ContentId("sha256", "a0" * 32)
BOB = ContentId("sha256", "b0" * 32)
CAROL = ContentId("sha256", "c0" * 32)
BLOCK = ContentId("sha256", "e0" * 32)
CREATED = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)


def test_a_payment_names_what_it_pays_each_its_fee_and_the_search_asked_for() -> None:
    asked = PaymentRequest.from_value(
        {"to": {str(BOB): "250", str(CAROL): "75"}, "fee_kismet": "3", "seconds": 10}
    )

    assert asked == PaymentRequest({BOB: Kismet(250), CAROL: Kismet(75)}, Kismet(3), 10.0)
    assert isinstance(asked.seconds, float)


def test_a_payment_with_no_fee_pays_none_and_members_it_does_not_know_are_ignored() -> None:
    asked = PaymentRequest.from_value({"to": {str(BOB): "1"}, "seconds": 0.5, "note": "lunch"})

    assert asked == PaymentRequest({BOB: Kismet(1)}, Kismet(0), 0.5)


def test_a_payment_of_a_fee_alone_moves_something() -> None:
    assert PaymentRequest.from_value({"to": {}, "fee_kismet": "1", "seconds": 0}).fee == Kismet(1)


@mark.parametrize(
    "value, error",
    [
        ([], "must be a JSON object"),
        ({"seconds": 1}, '"to" must be an object'),
        ({"to": {"bob": "1"}, "seconds": 1}, 'A name in "to" must be an id'),
        ({"to": {str(BOB): 1}, "seconds": 1}, "decimal digits"),
        ({"to": {str(BOB): "01"}, "seconds": 1}, "decimal digits"),
        ({"to": {str(BOB): "1"}, "fee_kismet": "-1", "seconds": 1}, '"fee_kismet"'),
        ({"to": {str(BOB): "1"}}, '"seconds" must be a number'),
        ({"to": {str(BOB): "1"}, "seconds": "1"}, '"seconds" must be a number'),
        ({"to": {str(BOB): "1"}, "seconds": -1}, '"seconds" must be a number, not negative'),
        ({"to": {}, "seconds": 1}, "must move more than 0 Kismet"),
        ({"to": {str(BOB): "0"}, "fee_kismet": "0", "seconds": 1}, "must move more than 0"),
        (
            {"to": {str(BOB): str(MAX_KISMET)}, "fee_kismet": "1", "seconds": 1},
            f"from 0 to {MAX_KISMET} Kismet",
        ),
        ({"to": {"md5/" + "0" * 32: "1"}, "seconds": 1}, "cannot hash"),
    ],
)
def test_what_is_no_payment_is_refused(value: object, error: str) -> None:
    with raises(InvalidRequestError, match=error):
        PaymentRequest.from_value(value)


def test_a_payment_is_its_senders_transaction_its_nonce_not_yet_searched_for() -> None:
    asked = PaymentRequest({BOB: Kismet(250)}, Kismet(3), 10.0)

    assert asked.transaction(ALICE, 41, CREATED) == Transaction(
        ALICE, {BOB: Kismet(250)}, Kismet(3), 41, CREATED
    )


def test_a_stake_names_the_block_it_is_on_and_what_it_stakes() -> None:
    asked = StakeRequest.from_value(
        {"transaction_block": str(BLOCK), "kismet": "1000", "note": "ignored"}
    )

    assert asked == StakeRequest(BLOCK, Kismet(1000))
    assert asked.stake(ALICE, CREATED) == Stake(ALICE, BLOCK, Kismet(1000), CREATED)


@mark.parametrize(
    "value, error",
    [
        ("stake", "must be a JSON object"),
        ({"kismet": "1"}, '"transaction_block" must be an id'),
        ({"transaction_block": "block", "kismet": "1"}, '"transaction_block" must be an id'),
        ({"transaction_block": str(BLOCK)}, '"kismet"'),
        ({"transaction_block": str(BLOCK), "kismet": 5}, '"kismet"'),
        ({"transaction_block": str(BLOCK), "kismet": "0"}, "more than 0 Kismet"),
    ],
)
def test_what_is_no_stake_is_refused(value: object, error: str) -> None:
    with raises(InvalidRequestError, match=error):
        StakeRequest.from_value(value)
