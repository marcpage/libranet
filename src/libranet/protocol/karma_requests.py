"""What ``POST /data/karma/payments`` and ``/data/karma/stakes`` accept (HttpApi §10.10).

A page has the node pay, and stake, as whoever is signed in, which signs for
them, as it does a signed drop (Phase 6 Step 137). A payment names what it
pays each recipient, and its fee, which may be left out for none, and how
long to search for its nonce (:class:`PaymentRequest`)::

    {"to": {"sha256/9f8e…": "250"}, "fee_kismet": "0", "seconds": 10}

A stake names the transaction block it is on, and what it stakes
(:class:`StakeRequest`)::

    {"transaction_block": "sha256/…", "kismet": "1000000000000000"}

Each amount is decimal digits, as the ledger writes one (KarmaLedger §4.1).
The node stamps what the person does not say: when each was made, and the
generation a payment is made in. Members neither knows are ignored.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.json_fields import number_field
from libranet.protocol.errors import InvalidRequestError
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_members import amounts_member, id_member
from libranet.protocol.karma_stakes import Stake
from libranet.protocol.karma_transactions import Transaction

# The members of a payment, and of a stake.
_TO: Final = "to"
_FEE: Final = "fee_kismet"
_SECONDS: Final = "seconds"
_TRANSACTION_BLOCK: Final = "transaction_block"
_KISMET: Final = "kismet"


@dataclass(frozen=True)
class PaymentRequest:
    """Paying each of ``to`` its amount, and ``fee`` to the validators, searching ``seconds``.

    ``seconds`` is how long to search for the transaction's nonce, which is
    searched for after that until it has the work a transaction needs
    (KarmaLedger §4.3).

    Raises:
        ValueError: it moves nothing, or more than there can be, or
            ``seconds`` is negative or not finite.
    """

    to: Mapping[ContentId, Kismet]
    fee: Kismet
    seconds: float

    def __post_init__(self) -> None:
        if not isfinite(self.seconds) or self.seconds < 0:
            raise ValueError(f'"{_SECONDS}" must be a number, not negative, got {self.seconds}')

        if sum(self.to.values(), self.fee) == Kismet(0):
            raise ValueError("A payment must move more than 0 Kismet")

    @classmethod
    def from_value(cls, value: object) -> PaymentRequest:
        """The payment a ``{"to", "fee_kismet", "seconds"}`` object asks for.

        ``fee_kismet`` is optional, and none if absent.

        Raises:
            InvalidRequestError: it is not such an object, or what it asks
                for is no payment.
        """
        if not isinstance(value, dict):
            raise InvalidRequestError("A payment must be a JSON object")

        try:
            return cls(
                amounts_member(value.get(_TO), named=f'"{_TO}"'),
                Kismet.from_value(value.get(_FEE, "0"), named=f'"{_FEE}"'),
                number_field(value, _SECONDS),
            )

        except ValueError as error:
            raise InvalidRequestError(str(error)) from None

    def transaction(self, sender: ContentId, generation: int, created: datetime) -> Transaction:
        """This payment, as ``sender``'s transaction made at ``created``, in ``generation``.

        Its nonce is not yet searched for (:meth:`Transaction.mined`).

        Raises:
            ValueError: ``generation`` is negative, or ``created`` is not a
                UTC time to the second.
        """
        return Transaction(sender, self.to, self.fee, generation, created)


@dataclass(frozen=True)
class StakeRequest:
    """Staking ``kismet`` on the transaction block ``transaction_block`` (KarmaLedger §6.2).

    Raises:
        ValueError: ``kismet`` is 0, which stakes nothing.
    """

    transaction_block: ContentId
    kismet: Kismet

    def __post_init__(self) -> None:
        if self.kismet == Kismet(0):
            raise ValueError("A stake must be of more than 0 Kismet")

    @classmethod
    def from_value(cls, value: object) -> StakeRequest:
        """The stake a ``{"transaction_block", "kismet"}`` object asks for.

        Raises:
            InvalidRequestError: it is not such an object, or what it asks
                for is no stake.
        """
        if not isinstance(value, dict):
            raise InvalidRequestError("A stake must be a JSON object")

        try:
            return cls(
                id_member(value.get(_TRANSACTION_BLOCK), named=f'"{_TRANSACTION_BLOCK}"'),
                Kismet.from_value(value.get(_KISMET), named=f'"{_KISMET}"'),
            )

        except ValueError as error:
            raise InvalidRequestError(str(error)) from None

    def stake(self, staker: ContentId, created: datetime) -> Stake:
        """This stake, as ``staker``'s, made at ``created``.

        Raises:
            ValueError: ``created`` is not a UTC time to the second.
        """
        return Stake(staker, self.transaction_block, self.kismet, created)
