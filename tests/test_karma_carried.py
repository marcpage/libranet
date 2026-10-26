"""Tests for the transactions the Karma module carries (KarmaLedger §3.5.3, §4.4, §5.6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from json import dumps, loads
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.carried import CARRIED_FILE, CarriedTransaction, CarriedTransactions
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.transactions import SignedTransaction, Transaction
from libranet.karma.weight import ChainWeight
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 23, 9, 0, 0, tzinfo=UTC)
CARRY_GENERATIONS = 3


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


def paying(sender: NodeIdentity, seconds: int, kismet: int = 1) -> SignedTransaction:
    """``sender``'s transaction paying itself ``kismet``, ``seconds`` after CREATED."""
    sent = Transaction(
        sender.node_id,
        {sender.node_id: Kismet(kismet)},
        Kismet(0),
        1,
        CREATED + timedelta(seconds=seconds),
    )
    return SignedTransaction.of(sender.signed(sent.text()))


def chain_of(
    directory: Path,
    generations: int,
    *,
    keys: dict[int, SignedTransaction] | None = None,
    witnessed: dict[int, SignedTransaction] | None = None,
) -> ChainHistory:
    """A history of ``generations``, each holding the keys and witnesses given for it."""
    held = keys or {}
    listed = witnessed or {}
    return ChainHistory(
        directory,
        [
            ChainGeneration(
                generation,
                ContentId("sha256", f"{generation:064x}"),
                ContentId("sha256", f"{generation + 100:064x}"),
                ChainWeight(generation),
                () if generation not in held else (held[generation].transaction.key,),
                () if generation not in listed else (listed[generation].hash,),
            )
            for generation in range(generations)
        ],
    )


def carried_file(directory: Path) -> bytes:
    """What ``carried.json`` in ``directory`` holds."""
    return (directory / CARRIED_FILE).read_bytes()


@fixture
def carried(tmp_path: Path) -> CarriedTransactions:
    return CarriedTransactions(tmp_path, CARRY_GENERATIONS)


def test_nothing_is_carried_before_the_file_is_written(carried: CarriedTransactions) -> None:
    assert carried.read() == []


def test_what_is_collected_is_carried_and_read_back(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    collected = [CarriedTransaction(paying(alice, 1), 2), CarriedTransaction(paying(alice, 2))]

    assert carried.carry(collected, chain_of(tmp_path, 4)) == collected
    assert carried.read() == collected


def test_one_whose_key_the_chain_holds_is_carried_no_more(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    met = paying(alice, 1)
    carried.carry([CarriedTransaction(met, 2)], chain_of(tmp_path, 4))

    assert carried.carry([], chain_of(tmp_path, 4, keys={3: met})) == []
    assert carried.read() == []


def test_one_is_carried_until_carry_generations_after_it_was_witnessed(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    collected = [CarriedTransaction(paying(alice, 1), 2)]

    # The head of 5 generations is generation 4, and the next block is 5.
    assert carried.carry(collected, chain_of(tmp_path, 5)) == collected
    assert carried.carry([], chain_of(tmp_path, 6)) == []


def test_one_not_witnessed_is_carried_until_the_chain_witnesses_it(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    pending = paying(alice, 1)
    carried.carry([CarriedTransaction(pending)], chain_of(tmp_path, 40))

    assert carried.carry([], chain_of(tmp_path, 41, witnessed={38: pending})) == [
        CarriedTransaction(pending, 38)
    ]
    assert carried.carry([], chain_of(tmp_path, 42, witnessed={38: pending})) == []


def test_one_sharing_its_key_with_one_carried_before_is_left_out(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    first = CarriedTransaction(paying(alice, 1), 2)
    carried.carry([first], chain_of(tmp_path, 3))

    assert carried.carry([CarriedTransaction(paying(alice, 1, 2), 2)], chain_of(tmp_path, 3)) == [
        first
    ]


def test_a_file_that_cannot_be_read_is_logged_and_written_again(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity, caplog: LogCaptureFixture
) -> None:
    (tmp_path / CARRIED_FILE).write_bytes(b"Not JSON")
    collected = [CarriedTransaction(paying(alice, 1), 2)]

    with caplog.at_level(WARNING):
        assert carried.carry(collected, chain_of(tmp_path, 3)) == collected

    assert "cannot be read, so what it carried is carried no more" in caplog.text
    assert carried.read() == collected


def test_those_carried_that_cannot_be_read_are_none_to_build_with_and_logged(
    carried: CarriedTransactions, tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    (tmp_path / CARRIED_FILE).write_bytes(b"Not JSON")

    with caplog.at_level(WARNING):
        assert carried.readable_at(chain_of(tmp_path, 3)) == []

    assert "Cannot read the Karma transactions carried" in caplog.text


def test_nothing_is_written_while_nothing_is_carried(
    carried: CarriedTransactions, tmp_path: Path
) -> None:
    carried.carry([], chain_of(tmp_path, 3))

    assert not (tmp_path / CARRIED_FILE).exists()


def test_a_file_emptied_is_written_empty(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    carried.carry([CarriedTransaction(paying(alice, 1), 0)], chain_of(tmp_path, 3))
    carried.carry([], chain_of(tmp_path, 10))

    assert (tmp_path / CARRIED_FILE).read_bytes() == b'{"transactions":[]}'


def test_one_the_node_puts_up_is_carried_as_such_and_read_back(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    collected = [
        CarriedTransaction(paying(alice, 1), 2),
        CarriedTransaction(paying(alice, 2), put_up=True),
    ]
    carried.carry(collected, chain_of(tmp_path, 3))

    assert carried.read() == collected
    assert [entry["put_up"] for entry in loads(carried_file(tmp_path))["transactions"]] == [
        False,
        True,
    ]


def test_only_those_the_node_puts_up_are_read_to_put_up_each_as_carried_at_the_head(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    met = paying(alice, 3)
    pending = paying(alice, 2)
    carried.carry(
        [
            CarriedTransaction(paying(alice, 1), 2),
            CarriedTransaction(pending, put_up=True),
            CarriedTransaction(met, put_up=True),
        ],
        chain_of(tmp_path, 3),
    )
    history = chain_of(tmp_path, 4, keys={3: met}, witnessed={3: pending})

    assert carried.to_put_up(history) == [CarriedTransaction(pending, 3, put_up=True)]
    # Nothing is written: the file is as it was carried.
    assert len(carried.read()) == 3


def test_an_entry_saying_nothing_of_being_put_up_is_not_put_up(
    carried: CarriedTransactions, tmp_path: Path, alice: NodeIdentity
) -> None:
    entry = {"transaction": paying(alice, 1).value(), "witnessed": 2}
    (tmp_path / CARRIED_FILE).write_text(dumps({"transactions": [entry]}))

    assert carried.read() == [CarriedTransaction(paying(alice, 1), 2)]


def test_an_entry_whose_put_up_is_not_true_or_false_is_refused(alice: NodeIdentity) -> None:
    with raises(ValueError, match='"put_up" must be true or false, got 1'):
        CarriedTransaction.from_value(
            {"transaction": paying(alice, 1).value(), "witnessed": None, "put_up": 1}
        )
