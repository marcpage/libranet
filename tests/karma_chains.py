"""Chains of Karma blocks that tests build, a generation at a time, as their builders place them.

Every block is as it truly is unless a test gives it otherwise, and checks
against :data:`BOUNDS`, in which a block of a few hundred bytes compressed is
full, and every transaction meets the work.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from itertools import count
from typing import Any, Collection, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import (
    Balance,
    BlockBounds,
    BlockDrop,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.generations import GenerationTargets
from libranet.karma.ledger import NO_BALANCE, BalanceSheet, Reward, reach_of
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.transactions import SignedTransaction, Transaction
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType

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

    def shunned(self, unplaced: Collection[ContentId]) -> list[ContentId]:
        """The content said to be shunned, in the order it was, but ``unplaced``.

        Those are the ids of what a test holds that is placed at no target,
        as a public key is. The follower reads one the node holds as an
        unsigned block, and shuns it, when its id shares the few bits these
        tests search for with a target's hash, as one now and then does by
        chance.
        """
        return [
            content_id
            for content_id in map(ContentId.from_fields, self.said(EventType.DATA_SHUNNED))
            if content_id not in unplaced
        ]


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


def staked(staker: NodeIdentity, on: ContentId, kismet: int) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), CREATED)
    return SignedStake.of(staker.signed(stake.text()))


def paying(sender: NodeIdentity, to: NodeIdentity, seconds: int) -> SignedTransaction:
    """``sender``'s transaction paying ``to`` 1 Kismet, ``seconds`` after CREATED."""
    sent = Transaction(
        sender.node_id, {to.node_id: Kismet(1)}, Kismet(0), 1, CREATED + timedelta(seconds=seconds)
    )
    return SignedTransaction.of(sender.signed(sent.text()))


@dataclass(frozen=True)
class Generation:
    """A generation's two drops, as their builders placed them."""

    transaction: bytes
    validation: bytes


@dataclass(frozen=True)
class Chain:
    """A chain a test builds, a generation at a time, from its root's."""

    generations: tuple[Generation, ...]

    @classmethod
    def root(cls, creator: NodeIdentity, bits: int | None = None) -> Chain:
        """The root ``creator`` places, each drop sharing ``bits`` with its target."""
        own = creator.node_id
        sent = Transaction(own, {own: Kismet(0)}, Kismet(0), 0, CREATED)
        signed = SignedTransaction.of(creator.signed(sent.text()))
        block = TransactionBlock(0, None, ChainWeight(0), 0, (signed,), {own: NO_BALANCE})
        transaction = signed_at(ROOT.transaction, creator, block, bits)
        validation = ValidationBlock(0, None, (staked(creator, id_of(transaction), 0),), ())
        drops = Generation(transaction, signed_at(ROOT.validation, creator, validation, bits))
        return cls((drops,))

    @property
    def head(self) -> ContentId:
        """Its latest validation block."""
        return id_of(self.generations[-1].validation)

    @property
    def targets(self) -> GenerationTargets:
        """The targets of the generation after its latest."""
        return GenerationTargets.after(self.head)

    def drops(self, *generations: int) -> list[bytes]:
        """The drops of each of ``generations``."""
        return [
            drop
            for generation in generations
            for drop in (
                self.generations[generation].transaction,
                self.generations[generation].validation,
            )
        ]

    def transaction(self, generation: int) -> TransactionBlock:
        """Its transaction block ``generation``."""
        return BlockDrop.parsed(self.generations[generation].transaction, TransactionBlock).block

    def weight(self) -> ChainWeight:
        """What it weighs through its latest generation (§3.3)."""
        last = self.generations[-1]
        return self.transaction(-1).weight_through(id_of(last.transaction), id_of(last.validation))

    def block_after(self, *signed: SignedTransaction) -> TransactionBlock:
        """The next transaction block, as it truly is, holding ``signed`` and the payout."""
        last = self.generations[-1]
        validated = self.transaction(-1)
        validation = BlockDrop.parsed(last.validation, ValidationBlock)
        latest: dict[ContentId, Balance] = {}

        for generation in range(len(self.generations)):
            latest.update(self.transaction(generation).balances)

        stakers = {stake.staker for stake in validation.block.stakes}
        reward = Reward(
            validation.block,
            validation.builder,
            validated,
            {staker: latest.get(staker, NO_BALANCE) for staker in stakers},
        )
        block = TransactionBlock(
            validated.generation + 1,
            self.head,
            self.weight(),
            0,
            tuple(sorted((reward.payout(), *signed), key=lambda entry: entry.hash)),
            {},
        )
        before = {identity: latest.get(identity, NO_BALANCE) for identity in block.named()}
        block = replace(block, balances=BalanceSheet.of(block, before).after)
        back = min(validated.reach, validated.generation)
        older = [
            self.transaction(generation).balances
            for generation in range(validated.generation - 1, validated.generation - back - 1, -1)
        ]
        return replace(block, reach=reach_of([block.balances, validated.balances, *older]))

    def then(
        self,
        builder: NodeIdentity,
        staker: NodeIdentity,
        block: TransactionBlock | None = None,
        *,
        bits: int | None = None,
        generation: int | None = None,
    ) -> Chain:
        """This chain and the next generation: ``block``, or the next as it truly is.

        ``builder`` builds the transaction block, and ``staker`` the
        validation block of ``generation``, the block's unless given, staking
        5 on it, each drop sharing ``bits`` with its target.
        """
        built = self.block_after() if block is None else block
        transaction = signed_at(self.targets.transaction, builder, built, bits)
        validation = ValidationBlock(
            built.generation if generation is None else generation,
            None,
            (staked(staker, id_of(transaction), 5),),
            (),
        )
        drops = Generation(
            transaction, signed_at(self.targets.validation, staker, validation, bits)
        )
        return Chain((*self.generations, drops))


def hold(truth: CasStore, *drops: bytes) -> None:
    """Store each of ``drops`` as it is."""
    for data in drops:
        truth.write(id_of(data), data)


def validating(
    target: DropTarget,
    staker: NodeIdentity,
    on: bytes,
    *,
    runner_up: bytes | None = None,
    bits: int | None = None,
) -> bytes:
    """The validation block ``staker`` places at ``target``, staking 5 on transaction block ``on``.

    It names the validation block ``runner_up`` as its runner-up, if given,
    and shares ``bits`` with its target.
    """
    block = ValidationBlock(
        BlockDrop.parsed(on, TransactionBlock).block.generation,
        None if runner_up is None else id_of(runner_up),
        (staked(staker, id_of(on), 5),),
        (),
    )
    return signed_at(target, staker, block, bits)
