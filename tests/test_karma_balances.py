"""Tests for writing every holder's balance each time the head changes (KarmaLedger §5.3)."""

from __future__ import annotations
from json import loads
from typing import Any, Mapping

from pytest import fixture, raises

from libranet.bundle.errors import MissingContentError
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.balances import HeadBalances
from libranet.karma.blocks import BlockDrop, ValidationBlock
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.modules import ModuleName
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from tests.helpers import new_identity
from tests.karma_chains import CREATED, Chain, hold, id_of


def ignored(event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
    """What publishing ``event`` would send, sent nowhere."""
    return make_message(event, ModuleName.KARMA, payload)


@fixture
def alice() -> NodeIdentity:
    return new_identity()


@fixture
def bob() -> NodeIdentity:
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


def generation(truth: CasStore, chain: Chain, number: int) -> ChainGeneration:
    """Generation ``number`` of ``chain``, its drops held."""
    drops = chain.generations[number]
    hold(truth, drops.transaction, drops.validation)
    return ChainGeneration.of(
        id_of(drops.validation),
        BlockDrop.parsed(drops.validation, ValidationBlock).block,
        id_of(drops.transaction),
        chain.transaction(number),
    )


def written(storage: StorageConfig) -> Any:
    """What ``balances.json`` holds."""
    return loads((storage.karma_dir / "balances.json").read_bytes())


def test_while_no_chain_is_followed_no_head_and_no_balance_are_written_once(
    balances: HeadBalances, storage: StorageConfig
) -> None:
    assert balances.write()
    assert written(storage) == {"head": None, "generation": None, "weight": None, "balances": {}}
    assert not balances.write()


def test_the_roots_creator_is_written_holding_the_roots_reward_once_for_the_head(
    balances: HeadBalances,
    history: ChainHistory,
    truth: CasStore,
    storage: StorageConfig,
    alice: NodeIdentity,
) -> None:
    chain = Chain.root(alice)
    balances.write()
    history.add(generation(truth, chain, 0))

    assert balances.write()
    # 20 Karma, paid by the payout of the block after the root (§7.4).
    assert written(storage) == {
        "head": str(chain.head),
        "generation": 0,
        "weight": chain.weight().value(),
        "balances": {str(alice.node_id): {"kismet": "2000000000000000", "last_won": 0}},
    }
    assert not balances.write()


def test_every_balance_stated_at_the_head_is_written_with_the_heads_reward_paid(
    balances: HeadBalances,
    history: ChainHistory,
    truth: CasStore,
    storage: StorageConfig,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    first = Chain.root(alice).then(bob, alice)
    sent = Transaction(alice.node_id, {bob.node_id: Kismet(7)}, Kismet(0), 1, CREATED)
    chain = first.then(
        bob, alice, first.block_after(SignedTransaction.of(alice.signed(sent.text())))
    )

    for number in range(3):
        history.add(generation(truth, chain, number))

    balances.write()

    # Alice staked on generation 2, whose reward pays her, and Bob holds what
    # she paid him.
    assert written(storage)["balances"] == {
        str(alice.node_id): chain.block_after().balances[alice.node_id].value(),
        str(bob.node_id): {"kismet": "7", "last_won": None},
    }


def test_balances_read_from_a_block_not_held_are_written_once_it_is(
    balances: HeadBalances,
    history: ChainHistory,
    truth: CasStore,
    storage: StorageConfig,
    alice: NodeIdentity,
) -> None:
    root = generation(truth, Chain.root(alice), 0)
    history.add(root)

    for block_id in (root.transaction, root.validation):
        data = truth.read(block_id)
        truth.delete(block_id)

        with raises(MissingContentError):
            balances.write()

        assert not (storage.karma_dir / "balances.json").exists()

        truth.write(block_id, data)

    assert balances.write()
    assert written(storage)["generation"] == 0
