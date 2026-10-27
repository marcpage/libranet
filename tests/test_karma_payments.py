"""Tests for the node's payments, queued and put up together (KarmaLedger §4.6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from logging import ERROR, INFO, WARNING

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop, TransactionBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.payments import NodePayments
from libranet.karma.putting import TransactionsPutUp
from libranet.karma.reading import TargetReader
from libranet.protocol.karma_amounts import MAX_KISMET, Kismet
from libranet.protocol.karma_payments import PAYMENTS_FILE, Payment, PaymentQueue
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import BOUNDS, CREATED, MINIMUM_BITS, Chain, Recorder, hold, id_of

INTERVAL_SECONDS = 3_600.0
# When the tests' clock starts: a minute after CREATED, which the chains' own
# transactions are stamped near.
STARTED = CREATED + timedelta(minutes=1)


class Clock:
    """A clock a test moves on, in seconds since the epoch."""

    def __init__(self) -> None:
        self.now = STARTED.timestamp()

    def __call__(self) -> float:
        return self.now

    @property
    def moment(self) -> datetime:
        """The time, to the second, as a transaction states it."""
        return datetime.fromtimestamp(int(self.now), UTC)


class Placed:
    """Stands in for the Karma module's storing, keeping each drop in the node's store."""

    def __init__(self, truth: CasStore) -> None:
        self.truth = truth
        self.drops: list[bytes] = []

    def __call__(self, data: bytes, _node_id: ContentId) -> None:
        self.truth.write(id_of(data), data)
        self.drops.append(data)

    def transactions(self) -> list[Transaction]:
        """The transaction each block placed holds but the payout, in the order they were."""
        return [
            entry.transaction
            for data in self.drops
            for entry in BlockDrop.parsed(data, TransactionBlock).block.signed
            if isinstance(entry, SignedTransaction)
        ]


@fixture(scope="module")
def alice() -> NodeIdentity:
    """The node that pays, holding the root's reward from generation 1 on."""
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    """Paid 19,999 Kismet in generation 2."""
    return new_identity()


@fixture(scope="module")
def dave() -> NodeIdentity:
    """Paid 9,999 Kismet in generation 2, and whose key the node does not hold."""
    return new_identity()


@fixture(scope="module")
def first(alice: NodeIdentity, bob: NodeIdentity) -> Chain:
    """Alice's root, and a generation on it that Bob builds, paying Alice the root's reward."""
    return Chain.root(alice, 9).then(bob, alice, bits=9)


@fixture(scope="module")
def second(
    first: Chain, alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity, dave: NodeIdentity
) -> Chain:
    """``first``, and a generation in which Alice pays Carol 19,999 Kismet and Dave 9,999."""
    sent = Transaction(
        alice.node_id,
        {carol.node_id: Kismet(19_999), dave.node_id: Kismet(9_999)},
        Kismet(0),
        1,
        CREATED + timedelta(seconds=1),
    )
    signed = SignedTransaction.of(alice.signed(sent.text()))
    return first.then(bob, alice, first.block_after(signed), bits=9)


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
def placed(truth: CasStore) -> Placed:
    return Placed(truth)


@fixture
def clock() -> Clock:
    return Clock()


def following(history: ChainHistory, reader: TargetReader, truth: CasStore, chain: Chain) -> None:
    """Have the node follow ``chain`` through its head, its blocks then held."""
    hold(truth, *chain.drops(*range(len(chain.generations))))
    follower = ChainFollower(history, reader, Recorder(), bounds=BOUNDS)
    follower.look()

    assert follower.look() == []


def made_last(storage: StorageConfig, clock: Clock) -> datetime:
    """As if the node made its last transaction now, created then, with the clock moved on 10 s.

    Returns:
        When it was created.
    """
    last = clock.moment
    PaymentQueue((), last).write(storage.karma_dir)
    clock.now += 10
    return last


def paying_as(
    node: NodeIdentity,
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    placed: Placed,
    clock: Clock,
    *,
    percent: float = 0.01,
    batch: int = 100,
) -> NodePayments:
    """The payments of ``node``, which puts up each transaction, and its block, in moments."""
    putting = TransactionsPutUp(
        history,
        reader,
        CarriedTransactions(storage.karma_dir, 3),
        lambda: node,
        placed,
        block_seconds=0.0,
        drop_bits=MINIMUM_BITS,
        bounds=BOUNDS,
    )
    return NodePayments(
        storage.karma_dir,
        history,
        reader,
        lambda: node,
        putting,
        clock=clock,
        percent=percent,
        batch=batch,
        interval_seconds=INTERVAL_SECONDS,
        transaction_seconds=0.0,
        bounds=BOUNDS,
    )


@mark.parametrize(("percent", "kismet"), [(0.01, 1), (0.5, 99), (2.5, 499)])
def test_a_payment_is_queued_at_the_percent_of_the_nodes_balance_rounded_down(
    percent: float,
    kismet: int,
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    second: Chain,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(history, reader, truth, second)
    payments = paying_as(carol, storage, history, reader, placed, clock, percent=percent)

    payment = payments.pay(bob.node_id)

    # Carol holds 19,999 Kismet.
    assert payment == Payment(bob.node_id, Kismet(kismet))
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((payment,))


def test_a_percent_is_read_as_the_decimal_it_is_written_as(bob: NodeIdentity) -> None:
    # As binary floating point, each is a little more than the decimal, which
    # a share of the most there can be would show.
    assert Payment.of(bob.node_id, Kismet(MAX_KISMET), 0.01) == Payment(bob.node_id, Kismet(10**24))
    assert Payment.of(bob.node_id, Kismet(MAX_KISMET), 0.0123) == Payment(
        bob.node_id, Kismet(123 * 10**22)
    )
    assert Payment.of(bob.node_id, Kismet(9_999), 0.01) is None


def test_a_payment_is_queued_as_the_chain_states_the_balance_at_the_head(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    held = history.generation(1)
    reward = BlockDrop.parsed(truth.read(held.transaction), TransactionBlock).block.balances

    payment = paying_as(alice, storage, history, reader, placed, clock).pay(bob.node_id)

    assert payment == Payment(bob.node_id, Kismet(reward[alice.node_id].kismet.count // 10_000))


def test_no_payment_is_queued_by_a_node_holding_under_ten_thousand_kismet(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    second: Chain,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(history, reader, truth, second)

    assert paying_as(dave, storage, history, reader, placed, clock).pay(bob.node_id) is None
    assert not (storage.karma_dir / PAYMENTS_FILE).exists()


def test_no_payment_is_queued_while_no_chain_is_followed(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    placed: Placed,
    clock: Clock,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    assert paying_as(alice, storage, history, reader, placed, clock).pay(bob.node_id) is None


def test_the_payments_queued_are_one_transaction_once_the_batch_is_recipients_summed(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(history, reader, truth, first)
    made_last(storage, clock)
    payments = paying_as(alice, storage, history, reader, placed, clock, batch=3)
    paid = payments.pay(bob.node_id)
    payments.pay(carol.node_id)
    payments.put_up()

    assert paid is not None and placed.drops == []

    payments.pay(bob.node_id)

    with caplog.at_level(INFO):
        payments.put_up()

    (transaction,) = placed.transactions()
    amount = paid.amount
    assert transaction.sender == alice.node_id
    assert dict(transaction.to) == {bob.node_id: amount + amount, carol.node_id: amount}
    assert (transaction.fee, transaction.generation, transaction.created) == (
        Kismet(0),
        1,
        clock.moment,
    )
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((), clock.moment)
    assert (
        "Making 3 Karma payments of the node's into one transaction, created "
        f"{transaction.created:%Y-%m-%dT%H:%M:%SZ}"
    ) in caplog.text


def test_the_payments_queued_are_one_transaction_once_the_interval_has_passed(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    last = made_last(storage, clock)
    payments = paying_as(alice, storage, history, reader, placed, clock)
    payments.pay(bob.node_id)
    clock.now = last.timestamp() + INTERVAL_SECONDS - 1
    payments.put_up()

    assert placed.drops == []

    clock.now += 1
    payments.put_up()

    (transaction,) = placed.transactions()
    assert transaction.created == clock.moment


def test_the_payments_of_a_node_that_has_made_no_transaction_are_one_at_once(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    payments = paying_as(alice, storage, history, reader, placed, clock)
    paid = payments.pay(bob.node_id)
    payments.put_up()

    assert paid is not None
    assert [dict(each.to) for each in placed.transactions()] == [{bob.node_id: paid.amount}]


def test_no_two_transactions_of_the_nodes_share_a_key(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    payments = paying_as(alice, storage, history, reader, placed, clock, batch=1)

    for _ in range(2):
        payments.pay(bob.node_id)
        payments.put_up()

    assert [each.created for each in placed.transactions()] == [
        clock.moment,
        clock.moment + timedelta(seconds=1),
    ]


def test_the_queue_is_kept_across_a_restart(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    made_last(storage, clock)
    paid = paying_as(alice, storage, history, reader, placed, clock).pay(bob.node_id)
    clock.now += INTERVAL_SECONDS

    paying_as(alice, storage, history, reader, placed, clock).put_up()

    assert paid is not None
    assert [dict(each.to) for each in placed.transactions()] == [{bob.node_id: paid.amount}]


def test_a_transaction_made_is_kept_with_its_created_and_put_up_once_its_senders_key_comes(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    recorder: Recorder,
    second: Chain,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(history, reader, truth, second)
    PaymentQueue((Payment(bob.node_id, Kismet(5)),)).write(storage.karma_dir)
    made = clock.moment

    paying_as(dave, storage, history, reader, placed, clock).put_up()

    kept = PaymentQueue.read(storage.karma_dir)
    assert placed.drops == []
    assert dave.node_id in recorder.asked_for()
    assert kept == PaymentQueue(
        (), made, Transaction(dave.node_id, {bob.node_id: Kismet(5)}, Kismet(0), 2, made)
    )

    truth.write(dave.node_id, dave.public_key)
    clock.now += 60
    paying_as(dave, storage, history, reader, placed, clock).put_up()

    (transaction,) = placed.transactions()
    assert transaction.key == (Transaction(dave.node_id, {}, Kismet(0), 2, made).key)
    assert dict(transaction.to) == {bob.node_id: Kismet(5)}
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((), made)


def test_a_transaction_made_and_taken_before_a_restart_is_given_again_under_its_key(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(history, reader, truth, first)
    payments = paying_as(alice, storage, history, reader, placed, clock, batch=1)
    payments.pay(bob.node_id)
    payments.put_up()
    (transaction,) = placed.transactions()
    # As if the node stopped once it was taken, before it was kept no more.
    PaymentQueue.keeping(transaction).write(storage.karma_dir)
    clock.now += 60

    paying_as(alice, storage, history, reader, placed, clock, batch=1).put_up()

    (carried,) = CarriedTransactions(storage.karma_dir, 3).read()
    assert carried.signed.transaction.key == transaction.key
    assert {each.key for each in placed.transactions()} == {transaction.key}
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((), transaction.created)


def test_a_payment_whose_balance_is_read_from_a_block_not_held_is_logged_and_not_made(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    recorder: Recorder,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(history, reader, truth, first)
    validated = history.generation(1).transaction
    truth.delete(validated)

    with caplog.at_level(INFO):
        paid = paying_as(alice, storage, history, reader, placed, clock).pay(bob.node_id)

    assert paid is None
    assert validated in recorder.asked_for()
    assert (
        f"Not paying {bob.node_id}: the node's Karma balance is read from blocks not held"
    ) in caplog.text


def test_a_queue_that_cannot_be_read_is_logged_and_written_again_empty(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(history, reader, truth, first)
    path = storage.karma_dir / PAYMENTS_FILE
    path.write_bytes(b"[")

    with caplog.at_level(WARNING):
        paid = paying_as(alice, storage, history, reader, placed, clock).pay(bob.node_id)

    assert paid is not None
    assert PaymentQueue.read(storage.karma_dir) == PaymentQueue((paid,))
    assert f"{path} cannot be read, so the payments queued in it are not made" in caplog.text


def test_a_queue_that_cannot_be_written_is_logged(
    storage: StorageConfig,
    history: ChainHistory,
    reader: TargetReader,
    truth: CasStore,
    placed: Placed,
    clock: Clock,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(history, reader, truth, first)
    payments = paying_as(alice, storage, history, reader, placed, clock)
    in_the_way = storage.karma_dir / PAYMENTS_FILE
    in_the_way.mkdir()

    with caplog.at_level(ERROR):
        paid = payments.pay(bob.node_id)
        payments.put_up()

    assert paid is None
    assert f"Cannot queue the node's Karma payment to {bob.node_id}" in caplog.text
    assert "Cannot put up the node's Karma payments" in caplog.text


def test_a_queue_is_written_as_it_is_read(alice: NodeIdentity, bob: NodeIdentity) -> None:
    made = Transaction(alice.node_id, {bob.node_id: Kismet(7)}, Kismet(0), 4, CREATED)
    queue = PaymentQueue((Payment(bob.node_id, Kismet(3)),), CREATED, made)

    assert queue.value() == {
        "payments": [{"to": str(bob.node_id), "kismet": "3"}],
        "last_created": "2026-10-23T09:00:00Z",
        "transaction": made.value(),
    }
    assert PaymentQueue.from_value(queue.value()) == queue
    assert PaymentQueue.from_value(PaymentQueue().value()) == PaymentQueue()


@mark.parametrize(
    ("value", "error"),
    [
        ([], "The payments queued must be an object"),
        ({"payments": {}}, '"payments" must be a list'),
        ({"payments": [7]}, "A payment queued must be an object"),
        ({"payments": [{"to": "sha256/x", "kismet": "1"}]}, 'A payment\'s "to" must be an id'),
        ({"payments": [], "last_created": "today"}, '"last_created" must be a UTC time'),
    ],
)
def test_a_queue_that_is_not_one_is_refused(value: object, error: str) -> None:
    with raises(ValueError, match=error):
        PaymentQueue.from_value(value)


def test_a_payment_of_nothing_is_refused(bob: NodeIdentity) -> None:
    with raises(ValueError, match="more than 0 Kismet"):
        PaymentQueue.from_value({"payments": [{"to": str(bob.node_id), "kismet": "0"}]})
