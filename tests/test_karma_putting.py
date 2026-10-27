"""Tests for putting a transaction up as a block of its own (KarmaLedger §5.6)."""

from __future__ import annotations
from dataclasses import replace
from logging import ERROR, INFO, WARNING

from pytest import LogCaptureFixture, fixture, mark

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockBounds, BlockDrop, TransactionBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import TransactionBlockCheck, TransactionVerdict
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.putting import OwnBlock, TransactionsPutUp
from libranet.karma.reading import HeldChain, TargetReader
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_carried import CARRIED_FILE, CarriedTransaction
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import BOUNDS, CREATED, MINIMUM_BITS, Chain, Recorder, hold, id_of, paying

# Bounds in which a block of its own, holding one transaction, is not full.
NOT_FULL = BlockBounds(most_bytes=MIB, room_bytes=1_000, transaction_bits=0)


class Placed:
    """Stands in for the Karma module's storing, keeping each drop in the node's store."""

    def __init__(self, truth: CasStore) -> None:
        self.truth = truth
        self.drops: list[bytes] = []
        self.builders: list[ContentId] = []

    def __call__(self, data: bytes, node_id: ContentId) -> None:
        self.truth.write(id_of(data), data)
        self.drops.append(data)
        self.builders.append(node_id)

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
    """The node that puts transactions up."""
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
    storage: StorageConfig, alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> CasStore:
    """The node's store, holding the keys of Alice, Bob, and Carol."""
    store = CasStore.source_of_truth(storage)

    for identity in (alice, bob, carol):
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
    return ChainFollower(history, reader, recorder, bounds=BOUNDS)


@fixture
def carried(storage: StorageConfig) -> CarriedTransactions:
    return CarriedTransactions(storage.karma_dir, 3)


@fixture
def placed(truth: CasStore) -> Placed:
    return Placed(truth)


def putting_up(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    carol: NodeIdentity,
    placed: Placed,
    bounds: BlockBounds = BOUNDS,
) -> TransactionsPutUp:
    """What puts transactions up as Carol, placing each drop in moments."""
    return TransactionsPutUp(
        history,
        reader,
        carried,
        lambda: carol,
        placed,
        block_seconds=0.0,
        drop_bits=MINIMUM_BITS,
        bounds=bounds,
    )


@fixture
def putting(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    carol: NodeIdentity,
    placed: Placed,
) -> TransactionsPutUp:
    return putting_up(history, reader, carried, carol, placed)


def following(follower: ChainFollower, truth: CasStore, chain: Chain) -> None:
    """Have ``follower`` follow ``chain`` through its head, its blocks then held."""
    hold(truth, *chain.drops(*range(len(chain.generations))))
    follower.look()

    assert follower.look() == []


def checked(
    reader: TargetReader, history: ChainHistory, head: ContentId, data: bytes, bounds: BlockBounds
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


def signed_by(sender: NodeIdentity, transaction: Transaction) -> SignedTransaction:
    """``transaction``, as ``sender`` signs it."""
    return SignedTransaction.of(sender.signed(transaction.text()))


def test_a_block_of_its_own_is_the_next_block_as_it_truly_is(
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    reader: TargetReader,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = paying(alice, bob, 1)

    own = OwnBlock.of(signed, first.head, HeldChain(history, reader))

    assert own.verdict is TransactionVerdict.VALID
    assert own.block == first.block_after(signed)


def test_a_transaction_of_a_generation_not_reached_is_pending_with_no_block(
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    reader: TargetReader,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    ahead = signed_by(
        alice, Transaction(alice.node_id, {bob.node_id: Kismet(1)}, Kismet(0), 5, CREATED)
    )

    own = OwnBlock.of(ahead, first.head, HeldChain(history, reader))

    assert (own.verdict, own.block) == (TransactionVerdict.PENDING, None)


def test_a_block_of_its_own_is_put_up_at_the_head_and_checked_correct_but_for_not_being_full(
    putting: TransactionsPutUp,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    reader: TargetReader,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    signed = paying(alice, bob, 1)

    with caplog.at_level(INFO):
        assert putting.take(signed)

    (data,) = placed.drops
    block_id = id_of(data)
    assert placed.builders == [carol.node_id]
    assert BlockDrop.parsed(data, TransactionBlock).builder == carol.node_id
    assert matching_bits(first.targets.transaction.hex, block_id.hash) >= MINIMUM_BITS
    assert checked(reader, history, first.head, data, BOUNDS).is_correct
    not_full = checked(reader, history, first.head, data, NOT_FULL)
    assert (not_full.full, not_full.incorrect.startswith("A transaction block must be full")) == (
        False,
        True,
    )
    assert f"Taking Karma transaction {signed.hash} from {alice.node_id} to put up" in caplog.text
    assert f"Put up Karma transaction {signed.hash} as block {block_id}" in caplog.text


def test_a_paid_transaction_put_up_alone_breaks_the_half_free_rule_and_states_nothing_false(
    putting: TransactionsPutUp,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    reader: TargetReader,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)

    putting.take(
        signed_by(
            alice, Transaction(alice.node_id, {bob.node_id: Kismet(1)}, Kismet(3), 1, CREATED)
        )
    )

    (data,) = placed.drops
    assert checked(reader, history, first.head, data, BOUNDS).incorrect == (
        "At least half a transaction block's transactions but the payout must be free"
    )


def test_a_pending_transaction_is_carried_and_put_up_once_covered(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    # Bob holds nothing yet.
    pending = paying(bob, carol, 1)

    with caplog.at_level(INFO):
        putting.take(pending)

    assert placed.drops == []
    assert carried.read() == [CarriedTransaction(pending, put_up=True)]
    assert (
        f"Not putting up Karma transaction {pending.hash} at head {first.head}, where it is pending"
    ) in caplog.text

    second = first.then(bob, alice, first.block_after(paying(alice, bob, 2)), bits=9)
    following(follower, truth, second)
    putting.put_up()

    (block,) = placed.blocks()
    assert (block.previous, block.signed) == (second.head, (pending,))


def test_a_transaction_taken_while_no_chain_is_followed_is_put_up_once_one_is(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    signed = paying(alice, bob, 1)

    putting.take(signed)

    assert placed.drops == []
    assert carried.read() == [CarriedTransaction(signed, put_up=True)]

    following(follower, truth, first)
    putting.put_up()

    assert [block.signed for block in placed.blocks()] == [(signed,)]


@mark.parametrize(("carry_generations", "put_up"), [(1, False), (2, True)])
def test_a_pending_transaction_is_carried_no_longer_than_the_count_after_it_was_witnessed(
    carry_generations: int,
    put_up: bool,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    reader: TargetReader,
    storage: StorageConfig,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    putting = putting_up(
        history, reader, CarriedTransactions(storage.karma_dir, carry_generations), carol, placed
    )
    pending = paying(bob, carol, 1)
    # Witnessed in generation 1, while Bob holds nothing, and covered from generation 2 on.
    witnessing = root.then(bob, alice, bits=9, witnessed=(pending.hash,))
    following(follower, truth, witnessing)
    putting.take(pending)
    following(
        follower,
        truth,
        witnessing.then(bob, alice, witnessing.block_after(paying(alice, bob, 2)), bits=9),
    )

    putting.put_up()

    assert [block.signed for block in placed.blocks()] == ([(pending,)] if put_up else [])


def test_a_key_is_put_up_once_at_a_head_and_again_at_each_after_until_the_chain_holds_it(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    follower: ChainFollower,
    truth: CasStore,
    history: ChainHistory,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    signed = paying(alice, bob, 1)
    same_key = signed_by(alice, replace(signed.transaction, to={carol.node_id: Kismet(2)}))

    putting.take(signed)
    putting.take(signed)

    with caplog.at_level(INFO):
        putting.take(same_key)

    putting.put_up()

    assert len(placed.drops) == 1
    assert (
        f"Not putting up Karma transaction {same_key.hash}: one of its key is carried already"
    ) in caplog.text

    # The chain goes on without it, so it is put up again at the next head.
    second = first.then(bob, alice, bits=9)
    following(follower, truth, second)
    putting.put_up()
    putting.put_up()

    assert [block.previous for block in placed.blocks()] == [first.head, second.head]

    third = second.then(bob, alice, second.block_after(signed), bits=9)
    following(follower, truth, third)
    putting.put_up()

    assert len(placed.drops) == 2
    assert carried.to_put_up(history) == []


def test_a_transaction_whose_check_lacks_a_block_of_the_chain_is_put_up_once_it_comes(
    putting: TransactionsPutUp,
    placed: Placed,
    recorder: Recorder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, first)
    validated = id_of(first.generations[1].transaction)
    data = truth.read(validated)
    truth.delete(validated)

    putting.take(paying(alice, bob, 1))

    assert placed.drops == []
    assert validated in recorder.asked_for()

    truth.write(validated, data)
    putting.put_up()

    assert len(placed.drops) == 1


def test_a_transaction_under_the_work_is_passed_over(
    placed: Placed,
    carried: CarriedTransactions,
    history: ChainHistory,
    reader: TargetReader,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    putting = putting_up(
        history, reader, carried, carol, placed, replace(BOUNDS, transaction_bits=40)
    )
    signed = paying(alice, bob, 1)

    with caplog.at_level(WARNING):
        putting.take(signed)

    assert (placed.drops, carried.read()) == ([], [])
    assert (
        f"Passing over Karma transaction {signed.hash}, which is invalid: it shares "
        f"{signed.work_bits} bits with the target of its work, under 40"
    ) in caplog.text


def test_a_transaction_moving_nothing_is_passed_over(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    nothing = signed_by(
        alice, Transaction(alice.node_id, {bob.node_id: Kismet(0)}, Kismet(0), 1, CREATED)
    )

    with caplog.at_level(WARNING):
        putting.take(nothing)

    assert (placed.drops, carried.read()) == ([], [])
    assert (
        f"Passing over Karma transaction {nothing.hash}, which is invalid: it moves 0 Kismet"
    ) in caplog.text


def test_a_transaction_whose_signature_fails_is_passed_over(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    signed = paying(alice, bob, 1)
    forged = SignedTransaction.from_value(
        {**signed.value(), "signature": paying(alice, bob, 2).value()["signature"]}
    )

    with caplog.at_level(WARNING):
        putting.take(forged)

    assert (placed.drops, carried.read()) == ([], [])
    assert (
        f"Passing over Karma transaction {forged.hash}, which is invalid: its signature must check"
    ) in caplog.text


def test_a_transaction_whose_key_the_chain_holds_is_passed_over(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    held = paying(alice, bob, 2)
    following(follower, truth, first.then(bob, alice, first.block_after(held), bits=9))

    with caplog.at_level(WARNING):
        done_with = putting.take(held)

    assert done_with
    assert (placed.drops, carried.read()) == ([], [])
    assert (
        f"Passing over Karma transaction {held.hash}, which is invalid: one of its key is in "
        "the chain"
    ) in caplog.text


def test_a_transaction_whose_senders_key_is_not_held_is_passed_over_and_the_key_asked_for(
    putting: TransactionsPutUp,
    placed: Placed,
    carried: CarriedTransactions,
    recorder: Recorder,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    dave = new_identity()
    signed = paying(dave, bob, 1)

    with caplog.at_level(WARNING):
        done_with = putting.take(signed)

    assert not done_with
    assert (placed.drops, carried.read()) == ([], [])
    assert recorder.asked_for() == [dave.node_id]
    assert (
        f"Passing over Karma transaction {signed.hash}: the key of its sender, {dave.node_id}, "
        "is not held, and is asked for"
    ) in caplog.text


def test_a_transaction_that_cannot_be_carried_is_logged_to_be_given_again(
    putting: TransactionsPutUp,
    placed: Placed,
    storage: StorageConfig,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    (storage.karma_dir / CARRIED_FILE).mkdir(parents=True)
    signed = paying(alice, bob, 1)

    with caplog.at_level(ERROR):
        done_with = putting.take(signed)

    assert not done_with
    assert placed.drops == []
    assert f"Cannot carry Karma transaction {signed.hash}" in caplog.text
