"""Tests for combining transaction blocks into one, as full as it can be (KarmaLedger §5.6)."""

from __future__ import annotations
from dataclasses import replace
from datetime import timedelta

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import KIB, MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import Balance, BlockBounds, TransactionBlock
from libranet.karma.building import CombinedBlock
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.transactions import SignedTransaction, Transaction
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import (
    CREATED,
    MINIMUM_BITS,
    Chain,
    Recorder,
    hold,
    id_of,
    signed_at,
)

# Bounds in which every transaction meets the work, and a block of more than
# 100 bytes compressed, up to 64 KiB, is full.
ROOMY = BlockBounds(most_bytes=64 * KIB, room_bytes=64 * KIB - 100, transaction_bits=0)

# Identities paid, whose keys no test holds.
PAID = tuple(ContentId("sha256", f"{byte:02x}" * 32) for byte in range(0xD1, 0xD5))


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    """The node that builds blocks."""
    return new_identity()


@fixture(scope="module")
def dave() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(alice: NodeIdentity) -> Chain:
    """Alice's root, each drop sharing 9 bits with its target."""
    return Chain.root(alice, 9)


@fixture(scope="module")
def first(root: Chain, alice: NodeIdentity, bob: NodeIdentity) -> Chain:
    """A generation on Alice's root that Bob builds, paying Alice the root's reward."""
    return root.then(bob, alice, bits=9)


@fixture
def truth(
    storage: StorageConfig,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
) -> CasStore:
    """The node's store, holding the keys of Alice, Bob, Carol, and Dave."""
    store = CasStore.source_of_truth(storage)

    for identity in (alice, bob, carol, dave):
        store.write(identity.node_id, identity.public_key)

    return store


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
    return ChainFollower(history, reader, recorder, bounds=ROOMY)


@fixture
def chain(history: ChainHistory, reader: TargetReader) -> HeldChain:
    return HeldChain(history, reader)


def following(follower: ChainFollower, truth: CasStore, chain: Chain) -> None:
    """Have ``follower`` follow ``chain`` through its head, its blocks then held."""
    hold(truth, *chain.drops(*range(len(chain.generations))))
    follower.look()

    assert follower.look() == []


def checked(
    reader: TargetReader,
    history: ChainHistory,
    head: ContentId,
    data: bytes,
    bounds: BlockBounds = ROOMY,
) -> TransactionBlockCheck:
    """The transaction block placed as ``data``, checked at the target named for ``head``."""
    held = reader.block(id_of(data), TransactionBlock)
    check = TransactionBlockCheck.of(
        held.block_id,
        held.data,
        head,
        HeldChain(history, reader),
        held.keys,
        compressed_bytes=held.compressed_bytes,
        bounds=bounds,
    )
    assert isinstance(check, TransactionBlockCheck)
    return check


def sent(
    sender: NodeIdentity,
    to: dict[ContentId, int],
    seconds: int,
    *,
    fee: int = 0,
    generation: int = 1,
    bits: int = 0,
) -> SignedTransaction:
    """``sender``'s transaction paying ``to``, ``seconds`` after CREATED, mined to ``bits``."""
    transaction = Transaction(
        sender.node_id,
        {identity: Kismet(amount) for identity, amount in to.items()},
        Kismet(fee),
        generation,
        CREATED + timedelta(seconds=seconds),
    )
    mined = transaction.mined(0, minimum_bits=bits) if bits else transaction
    return SignedTransaction.of(sender.signed(mined.text()))


def test_free_transactions_are_taken_by_most_bits_and_paid_by_largest_fee_in_turn(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    free = [sent(alice, {bob.node_id: 1}, seconds, bits=seconds) for seconds in (1, 2, 3)]
    paid = [sent(alice, {bob.node_id: 1}, 10 + fee, fee=fee) for fee in (2, 9, 5)]

    combined = CombinedBlock.of([*paid, *free], first.head, chain, bounds=ROOMY)

    by_bits = sorted(free, key=lambda signed: (-signed.work_bits, signed.hash))
    assert combined.taken == (
        by_bits[0],
        paid[1],
        by_bits[1],
        paid[2],
        by_bits[2],
        paid[0],
    )


def test_paid_transactions_are_taken_only_while_as_many_free_ones_are(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    free = sent(alice, {bob.node_id: 1}, 1)
    paid = [sent(alice, {bob.node_id: 1}, 10 + fee, fee=fee) for fee in (2, 9, 5)]

    combined = CombinedBlock.of([*paid, free], first.head, chain, bounds=ROOMY)

    assert combined.taken == (free, paid[1])
    assert combined.block(len(combined.taken)).is_half_free


def test_a_transaction_its_senders_balance_does_not_cover_is_not_taken(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    # What Alice holds once the block's payout pays her.
    held = first.block_after().balances[alice.node_id].kismet.count
    more = sent(alice, {dave.node_id: 1}, 2)
    everything = sent(alice, {dave.node_id: held}, 1, bits=more.work_bits + 1)
    # Bob holds nothing.
    uncovered = sent(bob, {dave.node_id: 1}, 3)

    combined = CombinedBlock.of([everything, more, uncovered], first.head, chain, bounds=ROOMY)

    assert combined.taken == (everything,)


def test_no_transaction_is_taken_before_its_generation(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    ahead = sent(alice, {bob.node_id: 1}, 1, generation=3)
    now = sent(alice, {bob.node_id: 1}, 2, generation=2)

    assert CombinedBlock.of([ahead, now], first.head, chain, bounds=ROOMY).taken == (now,)


def test_a_transaction_whose_key_the_chain_holds_is_not_taken(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    included = sent(alice, {bob.node_id: 1}, 1)
    second = first.then(bob, alice, first.block_after(included), bits=9)
    following(follower, truth, second)
    again = sent(alice, {bob.node_id: 2}, 1)

    assert CombinedBlock.of([included, again], second.head, chain, bounds=ROOMY).taken == ()


def test_of_transactions_sharing_a_key_the_one_of_most_bits_is_taken(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    unmined = sent(alice, {bob.node_id: 1}, 1)
    mined = sent(alice, {bob.node_id: 1}, 1, bits=unmined.work_bits + 1)

    assert CombinedBlock.of([unmined, mined], first.head, chain, bounds=ROOMY).taken == (mined,)


def test_one_moving_nothing_or_under_the_work_is_not_taken(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    unworked = sent(alice, {bob.node_id: 1}, 3)
    bits = unworked.work_bits + 1
    nothing = sent(alice, {bob.node_id: 0}, 1, bits=bits)
    worked = sent(alice, {bob.node_id: 1}, 2, bits=bits)
    bounds = replace(ROOMY, transaction_bits=bits)

    taken = CombinedBlock.of([nothing, worked, unworked], first.head, chain, bounds=bounds).taken

    assert taken == (worked,)


def test_the_block_of_those_taken_is_the_next_block_as_it_truly_is(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    offered = [sent(alice, {bob.node_id: 1}, 1), sent(alice, {dave.node_id: 2}, 2, fee=3)]

    combined = CombinedBlock.of(offered, first.head, chain, bounds=ROOMY)

    assert combined.block(2) == first.block_after(*offered)
    assert combined.block(1) == first.block_after(combined.taken[0])


def test_the_same_transactions_offered_in_any_order_make_the_same_block(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    offered = [
        sent(alice, {bob.node_id: 1}, 1),
        sent(alice, {dave.node_id: 2}, 2, fee=3),
        sent(alice, {dave.node_id: 1}, 3),
        sent(alice, {bob.node_id: 1}, 4, fee=1),
    ]

    forward = CombinedBlock.of(offered, first.head, chain, bounds=ROOMY)
    backward = CombinedBlock.of(offered[::-1], first.head, chain, bounds=ROOMY)

    assert forward.block(4) == backward.block(4)


def test_balances_are_stated_to_shorten_the_reach_within_the_cap(
    follower: ChainFollower,
    truth: CasStore,
    reader: TargetReader,
    history: ChainHistory,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    # PAID[0] is paid in generation 2, and stated in no block after.
    second = first.then(bob, alice, first.block_after(sent(alice, {PAID[0]: 5}, 1)), bits=9)
    third = second.then(bob, alice, bits=9)
    following(follower, truth, third)
    assert third.transaction(3).reach == 1

    taking = sent(alice, {bob.node_id: 1}, 2)
    block = CombinedBlock.of([taking], third.head, chain, bounds=ROOMY).block(1)

    assert block.balances[PAID[0]] == Balance(Kismet(5))
    assert block.reach == 0
    data = signed_at(third.targets.transaction, carol, block)
    hold(truth, data)
    assert checked(reader, history, third.head, data).is_correct


def test_no_balance_is_stated_to_shorten_the_reach_when_the_cap_leaves_too_little_room(
    follower: ChainFollower,
    truth: CasStore,
    reader: TargetReader,
    history: ChainHistory,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    # Four are paid in generation 2, and the block taking one transaction
    # names two, so has room for two more balances.
    paid = sent(alice, dict.fromkeys(PAID, 5), 1)
    second = first.then(bob, alice, first.block_after(paid), bits=9)
    third = second.then(bob, alice, bits=9)
    following(follower, truth, third)

    taking = sent(alice, {bob.node_id: 1}, 2)
    block = CombinedBlock.of([taking], third.head, chain, bounds=ROOMY).block(1)

    assert set(block.balances) == {alice.node_id, bob.node_id}
    assert block.reach == 2
    data = signed_at(third.targets.transaction, carol, block)
    hold(truth, data)
    assert checked(reader, history, third.head, data).is_correct
