"""Tests for writing every holder's balance each time the head changes (KarmaLedger §5.3)."""

from __future__ import annotations
from datetime import UTC, datetime
from json import loads
from typing import Any, Mapping

from pytest import fixture, raises

from libranet.bundle.errors import MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.balances import HeadBalances
from libranet.karma.blocks import TransactionBlock
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.modules import ModuleName
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
HEAD = ContentId("sha256", "c1" * 32)


def ignored(event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
    """What publishing ``event`` would send, sent nowhere."""
    return make_message(event, ModuleName.KARMA, payload)


@fixture
def alice() -> NodeIdentity:
    return new_identity()


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def history(storage: StorageConfig) -> ChainHistory:
    return ChainHistory.open(storage.karma_dir)


@fixture
def balances(storage: StorageConfig, history: ChainHistory) -> HeadBalances:
    reader = TargetReader.of(LibranetConfig(storage=storage), LayeredSource.open(storage), ignored)
    return HeadBalances(history, HeldChain(history, reader), storage.karma_dir)


def root(truth: CasStore, alice: NodeIdentity) -> ChainGeneration:
    """Generation 0, Alice's root, its transaction block held, stating her balance of 250."""
    transaction = Transaction(alice.node_id, {alice.node_id: Kismet(0)}, Kismet(0), 0, CREATED)
    block = TransactionBlock(
        0,
        None,
        ChainWeight(0),
        0,
        (SignedTransaction.of(alice.signed(transaction.text())),),
        {alice.node_id: Balance(Kismet(250), 3)},
    )
    data = alice.signed(block.text()).encoded() + b"\x007"
    block_id = ContentId.for_data(data, "sha256")
    truth.write(block_id, data)
    return ChainGeneration(0, HEAD, block_id, ChainWeight(80), (), ())


def written(storage: StorageConfig) -> Any:
    """What ``balances.json`` holds."""
    return loads((storage.karma_dir / "balances.json").read_bytes())


def test_while_no_chain_is_followed_no_head_and_no_balance_are_written_once(
    balances: HeadBalances, storage: StorageConfig
) -> None:
    assert balances.write()
    assert written(storage) == {"head": None, "generation": None, "weight": None, "balances": {}}
    assert not balances.write()


def test_every_balance_stated_at_the_head_is_written_with_it_once_for_each_head(
    balances: HeadBalances,
    history: ChainHistory,
    truth: CasStore,
    storage: StorageConfig,
    alice: NodeIdentity,
) -> None:
    balances.write()
    history.add(root(truth, alice))

    assert balances.write()
    assert written(storage) == {
        "head": str(HEAD),
        "generation": 0,
        "weight": "80",
        "balances": {str(alice.node_id): {"kismet": "250", "last_won": 3}},
    }
    assert not balances.write()


def test_balances_read_from_a_block_not_held_are_written_once_it_is(
    balances: HeadBalances,
    history: ChainHistory,
    truth: CasStore,
    storage: StorageConfig,
    alice: NodeIdentity,
) -> None:
    generation = root(truth, alice)
    history.add(generation)
    data = truth.read(generation.transaction)
    truth.delete(generation.transaction)

    with raises(MissingContentError):
        balances.write()

    assert not (storage.karma_dir / "balances.json").exists()

    truth.write(generation.transaction, data)

    assert balances.write()
    assert written(storage)["generation"] == 0
