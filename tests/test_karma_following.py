"""Tests for following the chain, a generation at a time (KarmaLedger §3.2, §3.3, §7.5)."""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count
from logging import INFO, WARNING
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import BlockBounds, BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.following import ChainFollower
from libranet.karma.generations import FIRST_NEW_KISMET, GenerationTargets
from libranet.karma.history import ChainHistory
from libranet.karma.ledger import NO_BALANCE, BalanceSheet, Reward, reach_of
from libranet.karma.reading import TargetReader
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.transactions import SignedTransaction, Transaction
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 23, 9, 0, 0, tzinfo=UTC)
ROOT = GenerationTargets.root()

# The fewest bits a search returns, fewer than a node's, so that a drop is
# placed in moments.
MINIMUM_BITS = 8

# Bounds every transaction meets, whatever its work, in which a block of a
# few hundred bytes compressed is full.
BOUNDS = BlockBounds(most_bytes=4_000, room_bytes=3_900, transaction_bits=0)


class Recorder:
    """Stands in for the Karma module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def said(self, event: EventType) -> list[dict[str, Any]]:
        """What was published as ``event``, in the order it was."""
        return [payload for published, payload in self.messages if published is event]

    def asked_for(self) -> list[ContentId]:
        """The content asked for, in the order it was."""
        return [ContentId.from_fields(payload) for payload in self.said(EventType.DATA_NOT_FOUND)]


def placed(target: DropTarget, content: bytes, bits: int | None = None) -> bytes:
    """``content`` as a drop at ``target``, sharing exactly ``bits`` with it.

    Without ``bits``, it shares at least :data:`MINIMUM_BITS`.
    """
    for nonce in count():
        data = content + b"\x00" + str(nonce).encode("ascii")
        shared = matching_bits(target.hex, sha256(data).hexdigest())

        if shared == bits or (bits is None and shared >= MINIMUM_BITS):
            return data

    raise AssertionError("Every nonce was tried")


def signed_at(
    target: DropTarget,
    builder: NodeIdentity,
    block: TransactionBlock | ValidationBlock,
    bits: int | None = None,
) -> bytes:
    """``block``, as ``builder`` signs it, as a drop at ``target`` sharing ``bits`` with it."""
    return placed(target, builder.signed(block.text()).encoded(), bits)


def id_of(data: bytes) -> ContentId:
    """The id of ``data``."""
    return ContentId.for_data(data, "sha256")


def hold(truth: CasStore, *drops: bytes) -> None:
    """Store each of ``drops`` as it is."""
    for data in drops:
        truth.write(id_of(data), data)


def hold_keys(truth: CasStore, *identities: NodeIdentity) -> None:
    """Store the public key of each of ``identities``."""
    for identity in identities:
        truth.write(identity.node_id, identity.public_key)


def staked(staker: NodeIdentity, on: ContentId, kismet: int) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), CREATED)
    return SignedStake.of(staker.signed(stake.text()))


@dataclass(frozen=True)
class Root:
    """A root's two drops, as its creator placed them."""

    transaction: bytes
    validation: bytes

    @classmethod
    def of(
        cls,
        creator: NodeIdentity,
        *,
        transaction_bits: int | None = None,
        validation_bits: int | None = None,
    ) -> Root:
        """The root ``creator`` places, each drop sharing the bits given with its target."""
        own = creator.node_id
        sent = Transaction(own, {own: Kismet(0)}, Kismet(0), 0, CREATED)
        signed = SignedTransaction.of(creator.signed(sent.text()))
        block = TransactionBlock(0, None, ChainWeight(0), 0, (signed,), {own: NO_BALANCE})
        transaction = signed_at(ROOT.transaction, creator, block, transaction_bits)
        validation = ValidationBlock(0, None, (staked(creator, id_of(transaction), 0),), ())
        return cls(transaction, signed_at(ROOT.validation, creator, validation, validation_bits))

    @property
    def next(self) -> GenerationTargets:
        """Generation 1's targets, on this root."""
        return GenerationTargets.after(id_of(self.validation))

    def weight(self) -> ChainWeight:
        """What the chain through it weighs: 2^b over its two drops (§3.3)."""
        return ChainWeight.of_drop(id_of(self.transaction), ROOT.transaction) + ChainWeight.of_drop(
            id_of(self.validation), ROOT.validation
        )

    def block_after(self) -> TransactionBlock:
        """Transaction block 1, as it truly is, holding the root's reward as its payout alone."""
        root = BlockDrop.parsed(self.transaction, TransactionBlock).block
        validation = BlockDrop.parsed(self.validation, ValidationBlock)
        reward = Reward(validation.block, validation.builder, root, root.balances)
        block = TransactionBlock(
            1, id_of(self.validation), self.weight(), 0, (reward.payout(),), {}
        )
        block = replace(block, balances=BalanceSheet.of(block, root.balances).after)
        return replace(block, reach=reach_of([block.balances, root.balances]))


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(alice: NodeIdentity) -> Root:
    """Alice's root."""
    return Root.of(alice)


@fixture(scope="module")
def first(root: Root, bob: NodeIdentity) -> bytes:
    """Transaction block 1 on Alice's root, which Bob built."""
    return signed_at(root.next.transaction, bob, root.block_after())


def validating(root: Root, first: bytes, alice: NodeIdentity, kismet: int, bits: int) -> bytes:
    """Validation block 1, which Alice built, staking ``kismet`` on ``first``, sharing ``bits``."""
    block = ValidationBlock(1, None, (staked(alice, id_of(first), kismet),), ())
    return signed_at(root.next.validation, alice, block, bits)


@fixture(scope="module")
def first_validation(root: Root, first: bytes, alice: NodeIdentity) -> bytes:
    """Validation block 1 of Alice's stake of 5 on ``first``, sharing 9 bits with its target."""
    return validating(root, first, alice, 5, 9)


@fixture(scope="module")
def better_validation(root: Root, first: bytes, alice: NodeIdentity) -> bytes:
    """Validation block 1 of Alice's stake of 6 on ``first``, sharing 12 bits with its target."""
    return validating(root, first, alice, 6, 12)


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def reader(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> TargetReader:
    return TargetReader(
        truth,
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        ShunnedContent.of(storage),
        recorder,
        wait_seconds=30.0,
        answer_seconds=300.0,
        max_bytes=MIB,
    )


@fixture
def history(storage: StorageConfig) -> ChainHistory:
    return ChainHistory.open(storage.karma_dir)


@fixture
def follower(history: ChainHistory, reader: TargetReader, recorder: Recorder) -> ChainFollower:
    return ChainFollower(history, reader, recorder, bounds=BOUNDS)


def test_a_node_following_no_chain_follows_the_root_it_finds(
    follower: ChainFollower,
    history: ChainHistory,
    truth: CasStore,
    alice: NodeIdentity,
    root: Root,
    caplog: LogCaptureFixture,
) -> None:
    hold(truth, root.transaction, root.validation)
    hold_keys(truth, alice)

    with caplog.at_level(INFO):
        followed = follower.look()

    assert followed == [history.generation(0)]
    assert len(history) == 1
    assert (followed[0].transaction, followed[0].validation) == (
        id_of(root.transaction),
        id_of(root.validation),
    )
    assert followed[0].weight == root.weight()
    assert (
        f"Following Karma generation 0: validation block {id_of(root.validation)}, "
        f"validating {id_of(root.transaction)}"
    ) in caplog.text


def test_the_heaviest_root_is_followed_whatever_its_validation_block_shares(
    follower: ChainFollower,
    history: ChainHistory,
    truth: CasStore,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    heavier = Root.of(alice, transaction_bits=14, validation_bits=8)
    sharing_more = Root.of(bob, transaction_bits=8, validation_bits=10)
    hold(truth, heavier.transaction, heavier.validation)
    hold(truth, sharing_more.transaction, sharing_more.validation)
    hold_keys(truth, alice, bob)

    follower.look()

    assert history.generation(0).validation == id_of(heavier.validation)


def test_a_generation_is_followed_once_its_blocks_are_stored(
    follower: ChainFollower,
    history: ChainHistory,
    truth: CasStore,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation)
    hold_keys(truth, alice, bob)
    follower.look()

    assert follower.look() == []

    hold(truth, first, first_validation)
    [followed] = follower.look()

    assert history.head == followed
    assert (followed.generation, followed.transaction, followed.validation) == (
        1,
        id_of(first),
        id_of(first_validation),
    )
    assert followed.weight == root.weight() + ChainWeight(2**9) + ChainWeight.of_drop(
        id_of(first), root.next.transaction
    )


def test_every_generation_held_is_followed_in_one_look(
    follower: ChainFollower,
    truth: CasStore,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation, first, first_validation)
    hold_keys(truth, alice, bob)

    assert [followed.generation for followed in follower.look()] == [0, 1]


def test_the_best_of_several_is_followed_and_nothing_lighter_shunned(
    follower: ChainFollower,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
    better_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation, first, first_validation, better_validation)
    hold_keys(truth, alice, bob)

    follower.look()

    assert history.generation(1).validation == id_of(better_validation)
    assert recorder.said(EventType.DATA_SHUNNED) == []
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []


def test_a_better_block_found_after_its_generation_is_followed_is_passed_over(
    follower: ChainFollower,
    history: ChainHistory,
    truth: CasStore,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
    better_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation, first, first_validation)
    hold_keys(truth, alice, bob)
    follower.look()
    hold(truth, better_validation)

    assert follower.look() == []
    assert history.generation(1).validation == id_of(first_validation)


def test_an_unsigned_block_is_shunned_once(
    follower: ChainFollower,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    root: Root,
    caplog: LogCaptureFixture,
) -> None:
    unsigned = placed(root.next.validation, b"Not signed")
    hold(truth, root.transaction, root.validation, unsigned)
    hold_keys(truth, alice)

    with caplog.at_level(WARNING):
        follower.look()
        follower.look()

    assert recorder.said(EventType.DATA_SHUNNED) == [id_of(unsigned).fields()]
    assert f"Shunning Karma block {id_of(unsigned)}, which is unsigned" in caplog.text


def test_a_false_block_is_said_once_naming_its_builder_and_not_shunned(
    follower: ChainFollower,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    caplog: LogCaptureFixture,
) -> None:
    false = signed_at(
        root.next.transaction, bob, replace(root.block_after(), weight=ChainWeight(1))
    )
    hold(truth, root.transaction, root.validation, false)
    hold_keys(truth, alice, bob)

    with caplog.at_level(WARNING):
        follower.look()
        follower.look()

    statement = f'A transaction block\'s "weight" must be {root.weight().value()}, got 1'
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == [
        {
            "block": str(id_of(false)),
            "builder": str(bob.node_id),
            "staker": None,
            "statement": statement,
        }
    ]
    assert recorder.said(EventType.DATA_SHUNNED) == []
    assert (
        f"Karma block {id_of(false)}, built by {bob.node_id}, states something false: "
        f"{statement}"
    ) in caplog.text


def test_a_stake_more_than_what_covers_it_names_its_staker_too(
    follower: ChainFollower,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
) -> None:
    block = ValidationBlock(1, None, (staked(alice, id_of(first), FIRST_NEW_KISMET + 1),), ())
    overstaked = signed_at(root.next.validation, bob, block)
    hold(truth, root.transaction, root.validation, first, overstaked)
    hold_keys(truth, alice, bob)

    assert len(follower.look()) == 1

    [said] = recorder.said(EventType.KARMA_FALSE_BLOCK)
    assert (said["block"], said["builder"], said["staker"]) == (
        str(id_of(overstaked)),
        str(bob.node_id),
        str(alice.node_id),
    )


def test_a_validation_block_waits_with_the_transaction_block_it_validates(
    follower: ChainFollower,
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation, first, first_validation)
    hold_keys(truth, alice)

    assert len(follower.look()) == 1
    assert bob.node_id in recorder.asked_for()
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []

    hold_keys(truth, bob)

    assert reader.stored(bob.node_id)
    assert [followed.generation for followed in follower.look()] == [1]


def test_a_block_a_peer_names_is_asked_for_and_followed_once_stored(
    follower: ChainFollower,
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
) -> None:
    hold(truth, root.transaction, root.validation, first)
    hold_keys(truth, alice, bob)
    follower.look()
    named = id_of(first_validation)

    assert reader.answered(root.next.validation.hex, [named])
    assert recorder.asked_for() == [named]

    hold(truth, first_validation)

    assert reader.stored(named)
    assert [followed.validation for followed in follower.look()] == [named]


def test_a_block_naming_what_is_no_block_is_passed_over_and_logged(
    follower: ChainFollower,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    caplog: LogCaptureFixture,
) -> None:
    junk = b"Not a block"
    block = ValidationBlock(1, id_of(junk), (staked(alice, id_of(first), 5),), ())
    naming = signed_at(root.next.validation, alice, block)
    hold(truth, root.transaction, root.validation, first, naming, junk)
    hold_keys(truth, alice, bob)

    with caplog.at_level(WARNING):
        assert len(follower.look()) == 1
        follower.look()

    assert caplog.text.count(f"Passing over Karma block {id_of(naming)}, which cannot be") == 1
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []
    assert recorder.said(EventType.DATA_SHUNNED) == []


def test_a_block_validating_one_passed_over_is_passed_over_too(
    follower: ChainFollower,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: Root,
    first: bytes,
    first_validation: bytes,
    caplog: LogCaptureFixture,
) -> None:
    hold(truth, root.transaction, root.validation)
    hold_keys(truth, alice, bob)
    follower.look()
    # The copy held of the root's transaction block, which block 1 is checked
    # against, is not it.
    truth.write(id_of(root.transaction), b"Not the root")
    hold(truth, first, first_validation)

    with caplog.at_level(WARNING):
        assert follower.look() == []

    assert f"Passing over Karma block {id_of(first)}, which cannot be checked" in caplog.text
    assert (
        f"Passing over Karma block {id_of(first_validation)}, which cannot be checked: It names "
        f"{id_of(first)}, which is passed over"
    ) in caplog.text
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []
