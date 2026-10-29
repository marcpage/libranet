"""Tests for every holder's Karma, as the chain the node follows states it."""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from typing import Any

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead

HEAD = KarmaHead(ContentId("sha256", "c1" * 32), 41, 1208)
ALICE = ContentId("sha256", "a0" * 32)
BOB = ContentId("sha256", "b0" * 32)
CAROL = ContentId("sha256", "c0" * 32)


def test_balances_are_written_and_read_back_with_the_head_they_were_read_at(
    tmp_path: Path,
) -> None:
    balances = KarmaBalances(
        HEAD, {BOB: Balance(Kismet(0), 40), ALICE: Balance(Kismet(10**28), None)}
    )

    balances.write(tmp_path)

    assert loads((tmp_path / "balances.json").read_bytes()) == {
        "head": str(HEAD.head),
        "generation": 41,
        "weight": "1208",
        "balances": {
            str(ALICE): {"kismet": str(10**28), "last_won": None},
            str(BOB): {"kismet": "0", "last_won": 40},
        },
    }
    assert KarmaBalances.read(tmp_path) == balances


def test_an_identity_not_stated_holds_nothing_and_has_won_nothing() -> None:
    balances = KarmaBalances(HEAD, {ALICE: Balance(Kismet(250), 40)})

    assert balances.balance(ALICE) == Balance(Kismet(250), 40)
    assert balances.balance(CAROL) == Balance(Kismet(0), None)
    assert balances.balance(CAROL).value() == {"kismet": "0", "last_won": None}


def test_no_balances_are_read_before_they_are_written(tmp_path: Path) -> None:
    balances = KarmaBalances.read(tmp_path)

    assert balances == KarmaBalances()
    assert not balances.head.follows


def test_a_node_following_no_chain_states_no_balances(tmp_path: Path) -> None:
    KarmaBalances().write(tmp_path)

    assert loads((tmp_path / "balances.json").read_bytes()) == {
        "head": None,
        "generation": None,
        "weight": None,
        "balances": {},
    }
    assert KarmaBalances.read(tmp_path) == KarmaBalances()

    with raises(ValueError, match="Only the chain followed states balances"):
        KarmaBalances(KarmaHead(), {ALICE: Balance(Kismet(1))})


@mark.parametrize(
    ("value", "message"),
    [
        ([], "must be a JSON object"),
        ({**HEAD.value()}, '"balances" must be an object'),
        ({**HEAD.value(), "balances": []}, '"balances" must be an object'),
        ({**HEAD.value(), "balances": {"sha256": {"kismet": "1"}}}, "algorithm/hash"),
        (
            {**HEAD.value(), "balances": {str(ALICE): {"kismet": 1, "last_won": None}}},
            f'The balance of {ALICE}\'s "kismet" must be a string',
        ),
        (
            {**HEAD.value(), "balances": {str(ALICE): {"kismet": "1", "last_won": "2"}}},
            '"last_won" must be a generation or null',
        ),
        (
            {**HEAD.value(), "balances": {str(ALICE): {"kismet": "1", "last_won": -2}}},
            '"last_won" must not be negative',
        ),
        ({**HEAD.value(), "balances": {str(ALICE): "1"}}, "must be a JSON object"),
        (
            {**KarmaHead().value(), "balances": {str(ALICE): {"kismet": "1", "last_won": None}}},
            "Only the chain",
        ),
    ],
)
def test_balances_that_are_not_balances_are_refused(value: object, message: str) -> None:
    with raises(ValueError, match=message):
        KarmaBalances.from_value(value)


def test_a_balances_file_that_is_not_json_is_refused(tmp_path: Path) -> None:
    (tmp_path / "balances.json").write_text("not JSON", encoding="utf-8")

    with raises(ValueError):
        KarmaBalances.read(tmp_path)


def test_a_balance_must_say_when_it_last_won(tmp_path: Path) -> None:
    (tmp_path / "balances.json").write_text(
        dumps({**HEAD.value(), "balances": {str(ALICE): {"kismet": "7"}}}), encoding="utf-8"
    )

    with raises(ValueError, match='"last_won" must be a generation or null'):
        KarmaBalances.read(tmp_path)


def test_a_balance_reads_back_as_written() -> None:
    balance = Balance(Kismet(1_250_000), 40)

    assert Balance.from_value(balance.value()) == balance
    assert balance.value() == {"kismet": "1250000", "last_won": 40}


def test_a_balance_that_has_won_nothing_is_stated_with_null() -> None:
    assert Balance.from_value({"kismet": "0", "last_won": None}) == Balance(Kismet(0))


@mark.parametrize(
    "value, member",
    [
        ({"kismet": "0"}, "last_won"),
        ({"kismet": "0", "last_won": "40"}, "last_won"),
        ({"kismet": "0", "last_won": True}, "last_won"),
        ({"kismet": "0", "last_won": 4.5}, "last_won"),
        ({"kismet": "-1", "last_won": None}, "kismet"),
        ({"kismet": "1.5", "last_won": None}, "kismet"),
        ({"last_won": None}, "kismet"),
    ],
)
def test_a_balance_not_as_it_must_be_is_refused_naming_its_member(
    value: dict[str, Any], member: str
) -> None:
    with raises(ValueError, match=f'"{member}"'):
        Balance.from_value(value)


def test_a_balance_won_before_the_root_is_refused() -> None:
    with raises(ValueError, match='"last_won" must not be negative'):
        Balance(Kismet(0), -1)


def test_what_is_no_object_is_no_balance() -> None:
    with raises(ValueError, match="must be a JSON object"):
        Balance.from_value("0")
