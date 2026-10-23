"""The node's payments, queued and put up together (KarmaLedger §4.6, Phase 6 Step 132).

For most common payments, a node pays ``karma.payment_percent`` of its
balance, 0.01% by default, as the chain it follows states it at the head,
rounded down to whole Kismet: nothing while that rounds to 0, as it does
under 10,000 Kismet at 0.01%. It queues each in ``payments.json`` in
``storage.karma_dir``, and puts them up together, as one transaction paying
each recipient all it is queued to be paid, so that they cost the work of
one transaction (§4.3), and do not flood the network::

    {"payments": [{"to": "sha256/9f8e…", "kismet": "200000000000"}],
     "last_created": "2026-10-23T09:00:00Z", "transaction": null}

The queue becomes a transaction once ``karma.payment_batch`` payments are
queued, or ``karma.payment_interval_seconds`` have passed since the node's
last transaction was created, as the node next looks, at once if it has
made none. The transaction pays no fee, is created in the head's
generation, and is stamped ``created`` then, later than the node's last.
It is kept in the file, in place of the queue, until it is put up
(:mod:`libranet.karma.putting`), so that a node that stops before then
mines it again with the same ``created``, and so the same key, and never
pays twice (§4.2).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from json import loads
from logging import getLogger
from pathlib import Path
from typing import Any, Callable, Final

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError, MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.json_format import MOMENT_FORMAT, compact_json
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.history import ChainHistory
from libranet.karma.members import id_member, moment_member
from libranet.karma.putting import TransactionsPutUp
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.transactions import SignedTransaction, Transaction

_LOGGER = getLogger(__name__)

#: The file of the Karma module's history holding the node's payments.
PAYMENTS_FILE: Final = "payments.json"

# The members of the file, and of each payment queued.
_PAYMENTS: Final = "payments"
_LAST_CREATED: Final = "last_created"
_TRANSACTION: Final = "transaction"
_TO: Final = "to"
_KISMET: Final = "kismet"


@dataclass(frozen=True)
class Payment:
    """``amount`` the node is to pay ``recipient``, queued (§4.6).

    Raises:
        ValueError: ``amount`` is 0, since a payment of nothing is not
            queued.
    """

    recipient: ContentId
    amount: Kismet

    def __post_init__(self) -> None:
        if self.amount == Kismet(0):
            raise ValueError(f"A payment queued must be of more than 0 Kismet, to {self.recipient}")

    @classmethod
    def of(cls, recipient: ContentId, balance: Kismet, percent: float) -> Payment | None:
        """The payment to ``recipient`` of ``percent`` of ``balance``, or ``None`` if it is 0.

        It is rounded down to whole Kismet, ``percent`` read as the decimal
        it is written as, so that it is exact.
        """
        amount = Kismet(balance.count * Fraction(str(percent)) // 100)
        return None if amount == Kismet(0) else cls(recipient, amount)

    @classmethod
    def from_value(cls, value: object) -> Payment:
        """The payment ``value``, an entry of ``payments.json``'s ``payments``, is.

        Raises:
            UnknownAlgorithmError: it names a recipient under a hash
                algorithm this node lacks.
            ValueError: it is not an object, or its ``to`` or ``kismet`` is
                not what it must be, as the error names.
        """
        if not isinstance(value, dict):
            raise ValueError(f"A payment queued must be an object, got {type(value).__name__}")

        return cls(
            id_member(value.get(_TO), named=f'A payment\'s "{_TO}"'),
            Kismet.from_value(value.get(_KISMET), named=f'A payment\'s "{_KISMET}"'),
        )

    def value(self) -> dict[str, str]:
        """This payment as ``payments.json`` holds it."""
        return {_TO: str(self.recipient), _KISMET: self.amount.value()}


@dataclass(frozen=True)
class PaymentQueue:
    """The node's ``payments`` queued, and the ``transaction`` made of those before, if any.

    ``transaction`` is kept, unmined, until it is put up. ``last_created``
    is the ``created`` of the node's last transaction, ``None`` before it
    has made one.
    """

    payments: tuple[Payment, ...] = ()
    last_created: datetime | None = None
    transaction: Transaction | None = None

    @classmethod
    def read(cls, directory: Path) -> PaymentQueue:
        """The queue kept in ``directory``, empty before its file is first written.

        Raises:
            OSError: the file could not be read, other than for there being
                none.
            UnknownAlgorithmError: it names an id under a hash algorithm
                this node lacks.
            ValueError: it is not JSON, or not what it must be, as the error
                names.
        """
        try:
            value = loads((directory / PAYMENTS_FILE).read_bytes())

        except FileNotFoundError:
            # Not logged: nothing has been queued yet.
            return cls()

        return cls.from_value(value)

    @classmethod
    def from_value(cls, value: object) -> PaymentQueue:
        """The queue ``value``, what ``payments.json`` holds, is.

        Raises:
            UnknownAlgorithmError: it names an id under a hash algorithm
                this node lacks.
            ValueError: it is not an object, or a member of it is not what it
                must be, as the error names.
        """
        if not isinstance(value, dict):
            raise ValueError(f"The payments queued must be an object, got {type(value).__name__}")

        payments = value.get(_PAYMENTS)
        last_created = value.get(_LAST_CREATED)
        transaction = value.get(_TRANSACTION)

        if not isinstance(payments, list):
            raise ValueError(f'"{_PAYMENTS}" must be a list, got {payments!r}')

        return cls(
            tuple(Payment.from_value(each) for each in payments),
            (
                None
                if last_created is None
                else moment_member(last_created, named=f'"{_LAST_CREATED}"')
            ),
            None if transaction is None else Transaction.from_value(transaction),
        )

    def value(self) -> dict[str, Any]:
        """This queue as ``payments.json`` holds it."""
        return {
            _PAYMENTS: [payment.value() for payment in self.payments],
            _LAST_CREATED: (
                None if self.last_created is None else self.last_created.strftime(MOMENT_FORMAT)
            ),
            _TRANSACTION: None if self.transaction is None else self.transaction.value(),
        }

    def write(self, directory: Path) -> None:
        """Keep this queue in ``directory``, in place of what was kept there.

        Raises:
            OSError: it could not be written.
        """
        write_atomically(directory / PAYMENTS_FILE, compact_json(self.value()))

    def queued(self, payment: Payment) -> PaymentQueue:
        """This queue with ``payment`` queued, after those queued before."""
        return replace(self, payments=(*self.payments, payment))

    def due(self, now: datetime, batch: int, interval_seconds: float) -> bool:
        """Whether the payments queued are to be made a transaction at ``now``.

        They are once ``batch`` are queued, or once ``interval_seconds`` have
        passed since the node's last transaction was created, or it has made
        none, unless a transaction made before is yet to be put up.
        """
        if self.transaction is not None or not self.payments:
            return False

        if len(self.payments) >= batch:
            return True

        return self.last_created is None or now - self.last_created >= timedelta(
            seconds=interval_seconds
        )

    def made(self, sender: ContentId, generation: int, now: datetime) -> Transaction:
        """The payments queued, made into one transaction by ``sender`` in ``generation``.

        It pays each recipient the payments to it summed, and no fee. It is
        created at ``now``, or a second after the node's last, if that is
        not earlier, so that no two transactions of the node's share a key
        (§4.2).

        Raises:
            ValueError: it sends more than there can be.
        """
        to: dict[ContentId, Kismet] = {}

        for payment in self.payments:
            to[payment.recipient] = to.get(payment.recipient, Kismet(0)) + payment.amount

        created = now

        if self.last_created is not None and created <= self.last_created:
            created = self.last_created + timedelta(seconds=1)

        return Transaction(sender, to, Kismet(0), generation, created)

    @classmethod
    def keeping(cls, transaction: Transaction) -> PaymentQueue:
        """A queue of nothing, keeping ``transaction``, the node's last, until it is put up."""
        return cls((), transaction.created, transaction)


class NodePayments:  # pylint: disable=too-many-instance-attributes
    """Queues the node's payments in ``directory``, and has ``putting`` put them up together (§4.6).

    The node's balance is read at the head of the chain ``history`` keeps,
    its blocks read from what ``reader`` holds, and the time from
    ``clock``, in seconds since the epoch, and each payment is ``percent``
    of it. The queue is made a transaction
    once ``batch`` payments are queued, or ``interval_seconds`` have passed
    since the node's last, created in the head's generation, its nonce
    searched for by ``searchers`` for ``transaction_seconds``, and after that
    until its work meets ``bounds``, and signed as ``identity`` gives the
    node.
    """

    def __init__(
        self,
        directory: Path,
        history: ChainHistory,
        reader: TargetReader,
        identity: Callable[[], NodeIdentity],
        putting: TransactionsPutUp,
        *,
        clock: Callable[[], float],
        percent: float,
        batch: int,
        interval_seconds: float,
        transaction_seconds: float,
        searchers: NonceSearchers = NONCE_SEARCHERS,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._directory = directory
        self._history = history
        self._chain = HeldChain(history, reader)
        self._identity = identity
        self._putting = putting
        self._clock = clock
        self._percent = percent
        self._batch = batch
        self._interval_seconds = interval_seconds
        self._transaction_seconds = transaction_seconds
        self._searchers = searchers
        self._bounds = bounds

    def pay(self, recipient: ContentId) -> Payment | None:
        """Queue a payment to ``recipient`` of the node's percent of its balance at the head.

        None is queued while that rounds to 0, or while no chain is
        followed. One whose balance is read from blocks
        not held, which are asked for, or that cannot be read or queued, is
        logged, and not made.

        Returns:
            The payment queued, or ``None`` if none was.

        Raises:
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
        """
        head = self._history.head

        if head is None:
            _LOGGER.debug("Not paying %s: no Karma chain is followed", recipient)
            return None

        node_id = self._identity().node_id

        try:
            balance = self._chain.balances((node_id,), head.generation)[node_id].kismet

        except MissingContentError:
            _LOGGER.info(
                "Not paying %s: the node's Karma balance is read from blocks not held, which are "
                "asked for",
                recipient,
            )
            return None

        except (BundleError, ValueError) as error:
            _LOGGER.warning(
                "Not paying %s: the node's Karma balance cannot be read: %s", recipient, error
            )
            return None

        payment = Payment.of(recipient, balance, self._percent)

        if payment is None:
            _LOGGER.debug(
                "Not paying %s: %s%% of the %s Kismet the node holds rounds to 0",
                recipient,
                self._percent,
                balance.value(),
            )
            return None

        try:
            self._read().queued(payment).write(self._directory)

        except OSError as error:
            _LOGGER.error("Cannot queue the node's Karma payment to %s: %s", recipient, error)
            return None

        _LOGGER.debug("Queued paying %s %s Kismet", recipient, payment.amount.value())
        return payment

    def put_up(self) -> None:
        """Put up the payments queued as one transaction, if they are due, or the one made before.

        The transaction made is kept in place of the queue, mined, signed,
        and given to be put up, and kept no more once that takes it, or
        passes it over for good. Until then, as its sender's key is asked
        for, or it cannot be carried, it is mined and given again, with the
        same ``created``, as this is next called. A queue that cannot be
        read, written, or mined, or a node whose key cannot be loaded, is
        logged.
        """
        try:
            queue = self._read()
            transaction = queue.transaction

            if transaction is None:
                now = datetime.fromtimestamp(int(self._clock()), UTC)

                if not queue.due(now, self._batch, self._interval_seconds):
                    return

                head = self._history.head
                generation = 0 if head is None else head.generation
                transaction = queue.made(self._identity().node_id, generation, now)
                _LOGGER.info(
                    "Making %d Karma payments of the node's into one transaction, created %s",
                    len(queue.payments),
                    transaction.created.strftime(MOMENT_FORMAT),
                )
                queue = PaymentQueue.keeping(transaction)
                queue.write(self._directory)

            if self._putting.take(self._signed(transaction)):
                replace(queue, transaction=None).write(self._directory)

        except (OSError, ValueError) as error:
            _LOGGER.error("Cannot put up the node's Karma payments: %s", error)

    def _signed(self, transaction: Transaction) -> SignedTransaction:
        """``transaction`` mined, and signed by the node.

        Raises:
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
            ValueError: the node is not ``transaction``'s sender.
            OSError: a searching process could not be started, or exited
                during the search.
        """
        mined = transaction.mined(
            self._transaction_seconds,
            minimum_bits=self._bounds.transaction_bits,
            searchers=self._searchers,
        )
        return SignedTransaction.of(self._identity().signed(mined.text()))

    def _read(self) -> PaymentQueue:
        """The queue kept, or an empty one, kept in its place, if what is kept cannot be read.

        Raises:
            OSError: it could not be read, other than for there being none,
                or written.
        """
        try:
            return PaymentQueue.read(self._directory)

        except ValueError as error:
            _LOGGER.warning(
                "%s cannot be read, so the payments queued in it are not made: %s",
                self._directory / PAYMENTS_FILE,
                error,
            )

        queue = PaymentQueue()
        queue.write(self._directory)
        return queue
