"""Tests for combining transaction blocks into one, as full as it can be (KarmaLedger §5.6)."""

from __future__ import annotations
from dataclasses import replace
from datetime import timedelta
from logging import DEBUG, INFO
from zlib import compress

from pytest import LogCaptureFixture, fixture

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import KIB, MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockBounds, BlockDrop, TransactionBlock
from libranet.karma.building import BlockBuilder, CombinedBlock, FilledBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.placing import BlockPlacing
from libranet.karma.reading import HeldChain, TargetReader
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance
from libranet.protocol.karma_carried import CarriedTransaction
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import (
    CREATED,
    MINIMUM_BITS,
    Chain,
    Generation,
    Recorder,
    hold,
    id_of,
    paying,
    signed_at,
    validating,
)

# Bounds in which every transaction meets the work, and a block of more than
# 100 bytes compressed, up to 64 KiB, is full.
ROOMY = BlockBounds(most_bytes=64 * KIB, room_bytes=64 * KIB - 100, transaction_bits=0)

# Bounds in which every transaction meets the work, and a block is full only
# from 63 KiB compressed, which no test's few transactions fill.
LARGE = BlockBounds(most_bytes=64 * KIB, room_bytes=KIB, transaction_bits=0)

# Identities paid, whose keys no test holds.
PAID = tuple(ContentId("sha256", f"{byte:02x}" * 32) for byte in range(0xD1, 0xD5))


class Placed:
    """Stands in for the Karma module's storing, keeping each drop in the node's store."""

    def __init__(self, truth: CasStore) -> None:
        self.truth = truth
        self.drops: list[bytes] = []

    def __call__(self, data: bytes, node_id: ContentId) -> None:
        self.truth.write(id_of(data), data)
        self.drops.append(data)

    def blocks(self) -> list[TransactionBlock]:
        """The blocks placed, in the order they were."""
        return [BlockDrop.parsed(data, TransactionBlock).block for data in self.drops]


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


@fixture
def carried(storage: StorageConfig) -> CarriedTransactions:
    return CarriedTransactions(storage.karma_dir, 3)


@fixture
def placed(truth: CasStore) -> Placed:
    return Placed(truth)


def building(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    carol: NodeIdentity,
    placed: Placed,
    bounds: BlockBounds = ROOMY,
) -> BlockBuilder:
    """What builds blocks as Carol, placing each drop in moments."""
    return BlockBuilder(
        history,
        reader,
        carried,
        BlockPlacing(lambda: carol, placed, block_seconds=0.0, drop_bits=MINIMUM_BITS),
        bounds=bounds,
    )


@fixture
def builder(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    carol: NodeIdentity,
    placed: Placed,
) -> BlockBuilder:
    return building(history, reader, carried, carol, placed)


@fixture
def shunning(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    carol: NodeIdentity,
    placed: Placed,
) -> BlockBuilder:
    """What builds blocks as Carol, in which a block of a few transactions is not full."""
    return building(history, reader, carried, carol, placed, LARGE)


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
    check = TransactionBlockCheck.of_held(held, head, HeldChain(history, reader), bounds=bounds)
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


def put_up(
    truth: CasStore, chain: Chain, builder: NodeIdentity, *signed: SignedTransaction
) -> bytes:
    """``signed``, put up by ``builder`` as a block of their own on ``chain``, held."""
    data = signed_at(chain.targets.transaction, builder, chain.block_after(*signed))
    hold(truth, data)
    return data


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


def test_a_block_is_filled_with_as_many_transactions_as_leave_it_full(
    follower: ChainFollower,
    truth: CasStore,
    reader: TargetReader,
    history: ChainHistory,
    carried: CarriedTransactions,
    placed: Placed,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, first)
    offered = [sent(alice, {bob.node_id: 1}, seconds) for seconds in range(1, 5)]
    put_up(truth, first, alice, *offered)
    combined = CombinedBlock.of(offered, first.head, HeldChain(history, reader), bounds=ROOMY)
    # Two fit, with room for what signing again changes, and three do not.
    bounds = BlockBounds(
        most_bytes=len(compress(carol.signed(combined.block(2).text()).encoded() + b"\x00", 9))
        + 32
        + 100,
        room_bytes=300,
        transaction_bits=0,
    )

    filled = FilledBlock.of(combined, carol, bounds=bounds)
    building(history, reader, carried, carol, placed, bounds).build()

    assert filled is not None and filled.drop.block == combined.block(2)
    assert filled.measured_bytes <= bounds.most_bytes
    (data,) = placed.drops
    assert BlockDrop.parsed(data, TransactionBlock).block == combined.block(2)
    assert checked(reader, history, first.head, data, bounds).is_correct


def test_no_block_is_filled_when_not_one_transaction_fits(
    follower: ChainFollower,
    truth: CasStore,
    chain: HeldChain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, first)
    combined = CombinedBlock.of([sent(alice, {bob.node_id: 1}, 1)], first.head, chain, bounds=ROOMY)
    bounds = BlockBounds(most_bytes=200, room_bytes=100, transaction_bits=0)

    assert FilledBlock.of(combined, carol, bounds=bounds) is None


def test_several_small_blocks_and_a_false_start_are_combined_into_one_correct_block(
    builder: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    reader: TargetReader,
    history: ChainHistory,
    root: Chain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    # pylint: disable=too-many-locals
    following(follower, truth, first)
    own = [sent(alice, {bob.node_id: 1}, 1), sent(alice, {dave.node_id: 2}, 2, fee=1)]

    for signed in own:
        put_up(truth, first, dave, signed)

    # Bob's rival to the head, at the target it was chosen from, which a
    # transaction is put up after.
    rival = validating(root.targets.validation, bob, first.generations[1].transaction)
    started = Chain((root.generations[0], Generation(first.generations[1].transaction, rival)))
    false_start = sent(alice, {carol.node_id: 3}, 3)
    hold(truth, rival)
    put_up(truth, started, dave, false_start)

    with caplog.at_level(INFO):
        builder.build()

    (data,) = placed.drops
    block = BlockDrop.parsed(data, TransactionBlock)
    assert block.builder == carol.node_id
    assert block.block == first.block_after(*own, false_start)
    assert checked(reader, history, first.head, data).is_correct
    assert f"Built Karma transaction block {id_of(data)} of 3 transactions" in caplog.text


def test_the_transactions_carried_are_built_in(
    builder: BlockBuilder,
    placed: Placed,
    carried: CarriedTransactions,
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = sent(alice, {bob.node_id: 1}, 1)
    carried.carry((CarriedTransaction(signed, 1),), history)

    builder.build()

    assert [block.signed for block in placed.blocks()] == [(signed,)]


def test_nothing_is_built_while_one_block_held_holds_every_transaction(
    builder: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = sent(alice, {bob.node_id: 1}, 1)
    put_up(truth, first, dave, signed)

    builder.build()

    assert placed.drops == []

    put_up(truth, first, dave, sent(alice, {bob.node_id: 1}, 2))
    builder.build()
    builder.build()

    assert len(placed.drops) == 1


def test_a_false_block_holding_every_transaction_keeps_none_from_being_built(
    builder: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = sent(alice, {bob.node_id: 1}, 1)
    true = first.block_after(signed)
    false = replace(true, balances={**true.balances, bob.node_id: Balance(Kismet(7))})
    hold(truth, signed_at(first.targets.transaction, dave, false))

    builder.build()

    assert [block.signed for block in placed.blocks()] == [(signed,)]


def test_a_transaction_whose_signature_fails_is_passed_over_and_logged_once_at_a_head(
    builder: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    transaction = Transaction(alice.node_id, {bob.node_id: Kismet(1)}, Kismet(0), 1, CREATED)
    # Signed as Alice, with Bob's key.
    forged = SignedTransaction.of(
        SignedBundle.signed(transaction.text(), bob.private_key, alice.node_id)
    )
    put_up(truth, first, dave, forged)

    with caplog.at_level(DEBUG):
        builder.build()
        builder.build()

    assert placed.drops == []
    passing = f"Passing over Karma transaction {forged.hash}"
    assert [r.levelno for r in caplog.records if passing in r.getMessage()] == [DEBUG]


def test_a_transaction_whose_senders_key_is_not_held_is_taken_once_it_is(
    builder: BlockBuilder,
    placed: Placed,
    recorder: Recorder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    erin = new_identity()
    second = first.then(bob, alice, first.block_after(sent(alice, {erin.node_id: 1}, 1)), bits=9)
    following(follower, truth, second)
    unheld = sent(erin, {bob.node_id: 1}, 2)
    other = sent(alice, {bob.node_id: 1}, 3)
    put_up(truth, second, bob, unheld)
    put_up(truth, second, bob, other)

    builder.build()

    # The block of Alice's own holds all that can be taken.
    assert placed.drops == []
    assert erin.node_id in recorder.asked_for()

    truth.write(erin.node_id, erin.public_key)
    builder.build()

    assert [{signed.hash for signed in block.signed} for block in placed.blocks()] == [
        {unheld.hash, other.hash}
    ]


def test_nothing_is_built_while_no_chain_is_followed(
    builder: BlockBuilder, placed: Placed, carried: CarriedTransactions, history: ChainHistory
) -> None:
    carried.carry((CarriedTransaction(paying(new_identity(), new_identity(), 1)),), history)

    builder.build()

    assert placed.drops == []


def test_what_is_no_transaction_block_at_a_target_is_passed_over_and_logged_once_at_a_head(
    builder: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    # Bob's validation block, copied to the transaction target.
    misplaced = validating(first.targets.transaction, bob, first.generations[1].transaction)
    hold(truth, misplaced)

    with caplog.at_level(DEBUG):
        builder.build()
        builder.build()

    assert placed.drops == []
    taking_none = f"Taking no Karma transactions from {id_of(misplaced)}"
    assert [r.levelno for r in caplog.records if taking_none in r.getMessage()] == [DEBUG]


def test_blocks_whose_transactions_a_block_held_holds_more_of_are_superseded_by_it(
    shunning: BlockBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    small = [
        put_up(truth, first, dave, sent(alice, {bob.node_id: 1}, seconds)) for seconds in (1, 2)
    ]

    assert shunning.superseded() == {}

    shunning.build()
    (built,) = placed.drops

    assert shunning.superseded() == {id_of(data): id_of(built) for data in small}
    # Each is named once at a head.
    assert shunning.superseded() == {}


def test_of_blocks_holding_the_same_transactions_those_sharing_fewer_bits_are_superseded(
    shunning: BlockBuilder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    block = first.block_after(sent(alice, {bob.node_id: 1}, 1))
    fewer = [signed_at(first.targets.transaction, builder, block, 8) for builder in (bob, dave)]
    hold(truth, *fewer)

    assert shunning.superseded() == {}

    more = signed_at(first.targets.transaction, alice, block, 9)
    hold(truth, more)

    assert shunning.superseded() == {id_of(data): id_of(more) for data in fewer}


def test_a_block_a_validation_block_names_is_never_superseded(
    shunning: BlockBuilder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = [sent(alice, {bob.node_id: 1}, seconds) for seconds in (1, 2)]
    named, unnamed = (put_up(truth, first, dave, each) for each in signed)
    keeper = put_up(truth, first, dave, *signed)
    hold(truth, validating(first.targets.validation, bob, named))

    assert shunning.superseded() == {id_of(unnamed): id_of(keeper)}


def test_a_full_block_is_never_superseded(
    builder: BlockBuilder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = [sent(alice, {bob.node_id: 1}, seconds) for seconds in (1, 2)]

    for each in signed:
        put_up(truth, first, dave, each)

    put_up(truth, first, dave, *signed)

    assert builder.superseded() == {}


def test_a_block_of_another_generation_or_built_on_another_head_is_never_superseded(
    shunning: BlockBuilder,
    follower: ChainFollower,
    truth: CasStore,
    root: Chain,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = [sent(alice, {bob.node_id: 1}, seconds) for seconds in (1, 2, 3)]
    # A block of the generation before, built on the root, and one built on
    # Bob's rival to the head, each copied to the head's target.
    rival = validating(root.targets.validation, bob, first.generations[1].transaction)
    started = Chain((root.generations[0], Generation(first.generations[1].transaction, rival)))
    elsewhere = [
        signed_at(first.targets.transaction, dave, root.block_after(signed[0])),
        signed_at(first.targets.transaction, dave, started.block_after(signed[1])),
    ]
    hold(truth, *elsewhere)
    small = put_up(truth, first, dave, signed[2])
    keeper = put_up(truth, first, dave, *signed)

    assert shunning.superseded() == {id_of(small): id_of(keeper)}


def test_a_block_is_never_superseded_by_one_that_is_not_correct(
    shunning: BlockBuilder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = sent(alice, {bob.node_id: 1}, 1)
    put_up(truth, first, dave, signed)
    true = first.block_after(signed, sent(alice, {bob.node_id: 1}, 2))
    false = replace(true, balances={**true.balances, bob.node_id: Balance(Kismet(7))})
    hold(truth, signed_at(first.targets.transaction, dave, false))

    assert shunning.superseded() == {}


def test_nothing_is_superseded_while_no_chain_is_followed(shunning: BlockBuilder) -> None:
    assert shunning.superseded() == {}
