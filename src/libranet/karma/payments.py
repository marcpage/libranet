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
pays twice (§4.2). The queue, and the file, are
:class:`~libranet.protocol.karma_payments.PaymentQueue`'s, since the web
server reads them too (Phase 6 Step 138).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from logging import getLogger
from pathlib import Path
from typing import Callable

from libranet.bundle.errors import BundleError, MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.json_format import MOMENT_FORMAT, moment_at
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds
from libranet.karma.history import ChainHistory
from libranet.karma.putting import TransactionsPutUp
from libranet.karma.reading import HeldChain, TargetReader
from libranet.protocol.karma_payments import PAYMENTS_FILE, Payment, PaymentQueue
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
        self._settings = _Settings(percent, batch, interval_seconds, transaction_seconds)
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
