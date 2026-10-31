"""The node's payments, queued and put up together (KarmaLedger §4.6, Phase 6 Step 132).

For most common payments, a node pays ``karma.payment_percent`` of its
balance, 0.01% by default, as the chain it follows states it at the head,
with the head's reward paid (§2), rounded down to whole Kismet: nothing
while that rounds to 0, as it does under 10,000 Kismet at 0.01%. The balance
is read from ``balances.json``, as the Karma module last wrote it
(:class:`~libranet.protocol.karma_balances.KarmaBalances`), since a payment
is queued for each piece of content a peer sends, and the blocks of the chain
may have been let go under eviction. It queues
each in ``payments.json`` in ``storage.karma_dir``, and puts them up
together, as one transaction paying each recipient all it is queued to be
paid, so that they cost the work of one transaction (§4.3), and do not
flood the network::

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
pays twice (§4.2). The queue, and the file, are
:class:`~libranet.protocol.karma_payments.PaymentQueue`'s, since the web
server reads them too (Phase 6 Step 138).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from logging import getLogger
from pathlib import Path
from typing import Callable

from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.json_format import MOMENT_FORMAT, moment_at
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.history import ChainHistory
from libranet.karma.putting import TransactionsPutUp
from libranet.protocol.karma_balances import KarmaBalances
from libranet.protocol.karma_payments import PAYMENTS_FILE, Payment, PaymentQueue
from libranet.protocol.karma_requests import NodePaymentRequest
from libranet.protocol.karma_transactions import SignedTransaction, Transaction

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class _Settings:
    """How much the node pays, and when, and how long a transaction of its payments is mined.

    As :class:`NodePayments` describes them.
    """

    percent: float
    batch: int
    interval_seconds: float
    transaction_seconds: float


class NodePayments:
    """Queues the node's payments in ``directory``, and has ``putting`` put them up together (§4.6).

    The node's balance is read from ``balances.json`` in ``directory``, at
    the head of the chain ``history`` keeps, with the head's reward paid, and
    the time from ``clock``, in seconds since the epoch, and each payment is
    ``percent`` of it. The queue is made a transaction
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
        self._identity = identity
        self._putting = putting
        self._clock = clock
        self._settings = _Settings(percent, batch, interval_seconds, transaction_seconds)
        self._searchers = searchers
        self._bounds = bounds

    def pay(self, recipient: ContentId) -> Payment | None:
        """Queue a payment to ``recipient`` of the node's percent of its balance at the head.

        The balance is the one the block after the head checks the node's
        transactions against, with the head's reward paid, as the Karma
        module last wrote it. None is queued while that rounds to 0, or while
        no chain is followed. One whose balance cannot be read, or that cannot
        be queued, is logged, and not made.

        Returns:
            The payment queued, or ``None`` if none was.

        Raises:
            FileNotFoundError: the node has no key yet.
            KeyFileError: the node's private key cannot be loaded.
        """
        try:
            balances = KarmaBalances.read(self._directory)

        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "Not paying %s: the node's Karma balance cannot be read: %s", recipient, error
            )
            return None

        if not balances.head.follows:
            _LOGGER.debug("Not paying %s: no Karma chain is followed", recipient)
            return None

        balance = balances.balance(self._identity().node_id).kismet
        payment = Payment.of(recipient, balance, self._settings.percent)

        if payment is None:
            _LOGGER.debug(
                "Not paying %s: %s%% of the %s Kismet the node holds rounds to 0",
                recipient,
                self._settings.percent,
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
                now = moment_at(self._clock())

                if not queue.due(now, self._settings.batch, self._settings.interval_seconds):
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

    def pay_out(self, request: NodePaymentRequest) -> SignedTransaction | None:
        """Pay as the operator asks, out of the node's Karma, as a transaction of its own, now.

        It is created later than the node's last transaction, in the head's
        generation, mined as the node's payments are, signed by the node,
        and given to be put up. One that cannot be made, mined, or carried,
        or a node whose key cannot be loaded, is logged, and not made; the
        operator may ask again.

        Returns:
            The transaction given to be put up, or ``None`` if none was.
        """
        try:
            queue = self._read()
            head = self._history.head
            created = queue.next_created(moment_at(self._clock()))
            transaction = request.transaction(
                self._identity().node_id, 0 if head is None else head.generation, created
            )
            replace(queue, last_created=created).write(self._directory)
            signed = self._signed(transaction)

            if not self._putting.take(signed):
                # Not logged: taking it logs why it was not taken.
                return None

        except (OSError, ValueError) as error:
            _LOGGER.error("Cannot make the payment the operator asked for: %s", error)
            return None

        _LOGGER.info(
            "Made the payment the operator asked for, %s Kismet in all, as transaction %s",
            transaction.sends().value(),
            signed.hash,
        )
        return signed

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
            self._settings.transaction_seconds,
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
