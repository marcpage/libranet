"""The node's payments, as the Karma module queues them (KarmaLedger §4.6, Phase 6 Step 132).

The Karma module queues each payment the node makes in ``payments.json`` in
``storage.karma_dir``, and puts them up together, as one transaction paying
each recipient all it is queued to be paid
(:mod:`libranet.karma.payments`)::

    {"payments": [{"to": "sha256/9f8e…", "kismet": "200000000000"}],
     "last_created": "2026-10-23T09:00:00Z", "transaction": null}

The transaction made of them is kept in the file, in place of the queue,
until it is put up. The web server loads none of the Karma module's code
(Module System §2), so the file is named, and read, here, for ``/config`` to
show (Phase 6 Step 138).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from fractions import Fraction
from json import loads
from pathlib import Path
from typing import Any, Final

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.json_format import MOMENT_FORMAT, compact_json
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_members import id_member, moment_member
from libranet.protocol.karma_transactions import Transaction

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
