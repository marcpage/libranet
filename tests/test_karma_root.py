"""Tests for making a chain's root, which anyone may place (KarmaLedger §7)."""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Iterator

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.drops import NonceSearchers
from libranet.cas.prefix import matching_bits
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.generations import GenerationTargets
from libranet.karma.root import ChainRoot
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import NO_BALANCE
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 22, 9, 30, 0, tzinfo=UTC)
ROOT = GenerationTargets.root()

# Few enough bits that the root is made in moments.
DROP_BITS = 6
TRANSACTION_BITS = 5


@fixture(scope="module")
def searchers() -> Iterator[NonceSearchers]:
    searchers = NonceSearchers(1)
    yield searchers
    searchers.close()


@fixture(scope="module")
def creator() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(creator: NodeIdentity, searchers: NonceSearchers) -> ChainRoot:
    return ChainRoot.made(
        creator,
        CREATED,
        drop_bits=DROP_BITS,
        transaction_bits=TRANSACTION_BITS,
        searchers=searchers,
    )


def id_of(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def test_transaction_block_0_holds_only_the_root_transaction(
    creator: NodeIdentity, root: ChainRoot
) -> None:
    drop = BlockDrop.parsed(root.transaction.data, TransactionBlock)
    block = drop.block
    (signed,) = block.transactions

    assert drop.builder == creator.node_id
    assert (block.generation, block.previous, block.weight, block.reach) == (
        0,
        None,
        ChainWeight(0),
        0,
    )
    assert signed == block.signed[0]
    assert signed.transaction.is_root
    assert (signed.transaction.sender, signed.transaction.created) == (creator.node_id, CREATED)
    assert signed.work_bits >= TRANSACTION_BITS
    assert dict(block.balances) == {creator.node_id: NO_BALANCE}


def test_validation_block_0_holds_only_its_creators_stake_of_0_on_it(
    creator: NodeIdentity, root: ChainRoot
) -> None:
    drop = BlockDrop.parsed(root.validation.data, ValidationBlock)
    block = drop.block
    (stake,) = block.stakes

    assert drop.builder == creator.node_id
    assert (block.generation, block.runner_up, block.witnessed) == (0, None, ())
    assert stake.staker == creator.node_id
    assert stake.stake.transaction_block == id_of(root.transaction.data)
    assert (stake.stake.kismet, stake.stake.created) == (Kismet(0), CREATED)


def test_each_drop_shares_at_least_the_bits_asked_for_with_its_target(root: ChainRoot) -> None:
    for drop, target in ((root.transaction, ROOT.transaction), (root.validation, ROOT.validation)):
        assert drop.matching_bits >= DROP_BITS
        assert matching_bits(target.hex, id_of(drop.data).hash) == drop.matching_bits


def test_the_root_is_generation_0_of_the_chain_on_it(root: ChainRoot) -> None:
    generation = root.generation
    transaction = BlockDrop.parsed(root.transaction.data, TransactionBlock).block

    assert generation.generation == 0
    assert generation.transaction == id_of(root.transaction.data)
    assert generation.validation == id_of(root.validation.data)
    assert generation.weight == ChainWeight(
        2**root.transaction.matching_bits + 2**root.validation.matching_bits
    )
    assert generation.keys == (transaction.signed[0].transaction.key,)
    assert generation.witnessed == ()


def test_a_root_is_made_only_at_a_moment_to_the_second(
    creator: NodeIdentity, searchers: NonceSearchers
) -> None:
    with raises(ValueError, match="created"):
        ChainRoot.made(creator, CREATED.replace(microsecond=1), drop_bits=0, searchers=searchers)
