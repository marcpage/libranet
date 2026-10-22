"""Transactions: what a transaction block records (KarmaLedger §4).

A transaction is a signed bundle (BundleSpecification §5), signed by its
sender, whose ``contents`` is the JSON object::

    {"from": "sha256/0a1b…", "to": {"sha256/9f8e…": "250"}, "fee_kismet": "0",
     "generation": 41, "created": "2026-10-21T14:03:22Z", "nonce": "…"}

It is known by its ``hash``, the hash of its ``contents``, so the same
contents signed again are the same transaction (§4.2). Its key is its
``from`` and its ``created``: two transactions with one key are one payment,
whatever else they hold. Its ``nonce`` is written last, so that a search for
one hashes what comes before it once (Phase 6 Step 118), and the number of
leading bits its ``hash`` shares with the SHA-256 of ``transaction`` is its
proof of work (§4.3).

Nothing here reads the chain: whether a transaction is valid in a block is
for whoever checks the block.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from datetime import datetime
from typing import Any, Final, Mapping

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.json_fields import integer_field, string_field
from libranet.json_format import MOMENT_FORMAT, compact_json
from libranet.karma.amounts import MAX_KISMET, Kismet
from libranet.karma.generations import TRANSACTION_WORK_TARGET
from libranet.karma.members import (
    CREATED_MEMBER,
    amounts_member,
    check_moment,
    contents_value,
    id_member,
    moment_member,
)

# The members of a transaction (§4.1).
_FROM: Final = "from"
_TO: Final = "to"
_GENERATION: Final = "generation"
_FEE: Final = "fee_kismet"
_NONCE: Final = "nonce"


@dataclass(frozen=True)
class TransactionKey:
    """What makes two transactions one payment: their ``sender``, and when they were ``created``.

    A sender stamps ``created`` once, and reuses it however often the
    payment is signed or mined again (§4.2).
    """

    sender: ContentId
    created: datetime


@dataclass(frozen=True)
class Transaction:
    """``sender`` paying each of ``to`` its amount, and ``fee`` to the validators (§4.1).

    It was created in ``generation``, at ``created``, both its sender's
    word, and its ``nonce`` is searched for to give it its proof of work
    (§4.3). The root's transaction is the only one that may move 0 Kismet
    (§7.1), which a block checks, since a transaction cannot say which
    block holds it.

    Raises:
        ValueError: ``generation`` is negative, ``created`` is not a UTC
            time to the second, or it sends more than there can be.
    """

    sender: ContentId
    to: Mapping[ContentId, Kismet]
    fee: Kismet
    generation: int
    created: datetime
    _: KW_ONLY
    nonce: str = ""

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError(
                f'A transaction\'s "{_GENERATION}" must not be negative, got {self.generation}'
            )

        check_moment(self.created, named=f'A transaction\'s "{CREATED_MEMBER}"')
        sent = self.fee.count + sum(amount.count for amount in self.to.values())

        if sent > MAX_KISMET:
            raise ValueError(f"A transaction must send at most {MAX_KISMET} Kismet, got {sent}")

    @classmethod
    def from_value(cls, value: object, *, named: str = "A transaction") -> Transaction:
        """The transaction ``value``, the decoded ``contents`` of a signed bundle, is (§4.1).

        Members it does not know are passed over.

        Raises:
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            ValueError: ``value``, called ``named``, is not an object, or a
                member of it is missing or is not what it must be, as the
                error names; or it is no transaction, as
                :class:`Transaction` says.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        return cls(
            id_member(value.get(_FROM), named=f'"{_FROM}"'),
            amounts_member(value.get(_TO), named=f'"{_TO}"'),
            Kismet.from_value(value.get(_FEE), named=f'"{_FEE}"'),
            integer_field(value, _GENERATION),
            moment_member(value.get(CREATED_MEMBER), named=f'"{CREATED_MEMBER}"'),
            nonce=string_field(value, _NONCE),
        )

    def value(self) -> dict[str, Any]:
        """This transaction as JSON, its ``to`` sorted, and its ``nonce`` last (§4.1)."""
        return {
            _FROM: str(self.sender),
            _TO: {str(identity): amount.value() for identity, amount in sorted(self.to.items())},
            _FEE: self.fee.value(),
            _GENERATION: self.generation,
            CREATED_MEMBER: self.created.strftime(MOMENT_FORMAT),
            _NONCE: self.nonce,
        }

    def text(self) -> str:
        """This transaction as compact JSON text, as its sender signs it.

        It ends with the nonce and the two characters that close it, ``"}``,
        so that a search for a nonce hashes all before it once (Phase 6 Step
        118).
        """
        return compact_json(self.value()).decode("utf-8")

    @property
    def key(self) -> TransactionKey:
        """Its key: its sender and ``created``, not its ``nonce`` (§4.2)."""
        return TransactionKey(self.sender, self.created)

    @property
    def is_root(self) -> bool:
        """Whether it has the root's shape: its sender sends themselves 0, in generation 0.

        It pays no fee. That is the one transaction that may move 0 Kismet
        (§7.1).
        """
        return (
            self.generation == 0
            and self.fee == Kismet(0)
            and dict(self.to) == {self.sender: Kismet(0)}
        )

    def sends(self) -> Kismet:
        """All it takes from its sender's balance: what it pays ``to``, and its fee (§4.4)."""
        return sum(self.to.values(), self.fee)


@dataclass(frozen=True)
class SignedTransaction:
    """``transaction``, as its sender signed it in ``bundle``, known by its :attr:`hash` (§4.2).

    :meth:`of` and :meth:`from_value` build one, reading ``transaction``
    from what ``bundle`` signs. Its signature is checked only by
    :meth:`verified`, given its sender's key.

    Raises:
        ValueError: ``transaction``'s sender is not ``bundle``'s signer.
    """

    bundle: SignedBundle
    transaction: Transaction

    def __post_init__(self) -> None:
        if self.transaction.sender != self.bundle.signer:
            raise ValueError(
                f'A transaction\'s "{_FROM}" must be its signer, {self.bundle.signer}, '
                f"got {self.transaction.sender}"
            )

    @classmethod
    def of(cls, bundle: SignedBundle) -> SignedTransaction:
        """The transaction ``bundle`` signs.

        Raises:
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            ValueError: what it signs is no transaction
                (:meth:`Transaction.from_value`), or its sender is not its
                signer.
        """
        transaction = contents_value(bundle.contents, named="A transaction")
        return cls(bundle, Transaction.from_value(transaction))

    @classmethod
    def from_value(cls, value: object) -> SignedTransaction:
        """The transaction ``value``, an object of a block's ``transactions``, is (§5.1).

        Raises:
            MalformedBundleError: ``value`` is not a signed bundle's object
                (:meth:`SignedBundle.from_value`).
            UnsupportedBundleError: it is hashed with an algorithm other than
                SHA-256, or its signer is named under a hash algorithm this
                node lacks.
            BundleSignatureError: its ``hash`` is not the hash of its
                ``contents``.
            UnknownAlgorithmError: what it signs names an id under a hash
                algorithm this node lacks.
            ValueError: what it signs is no transaction, or its sender is not
                its signer.
        """
        return cls.of(SignedBundle.from_value(value))

    def value(self) -> dict[str, str]:
        """This transaction as a block's ``transactions`` holds it: its signed bundle, an object."""
        return self.bundle.value()

    @property
    def hash(self) -> str:
        """What it is known by: the SHA-256 of what its sender signed, in lower-case hex (§4.2)."""
        return self.bundle.hash

    @property
    def work_bits(self) -> int:
        """Its proof of work: the leading bits its :attr:`hash` shares with the target of its work.

        That is the SHA-256 of ``transaction``,
        :data:`~libranet.karma.generations.TRANSACTION_WORK_TARGET` (§4.3).
        """
        return matching_bits(TRANSACTION_WORK_TARGET.hex, self.hash)

    def verified(self, public_key: bytes) -> None:
        """Raise unless its sender signed it, with ``public_key``, the key their id names.

        Raises:
            BundleSignatureError: ``public_key`` is not the sender's, or does
                not verify the signature (:meth:`SignedBundle.verified`).
        """
        self.bundle.verified(public_key)
