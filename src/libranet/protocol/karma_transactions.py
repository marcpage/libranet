"""Transactions and payouts: what a transaction block records (KarmaLedger §4, §5.2).

A transaction is a signed bundle (BundleSpecification §5), signed by its
sender, whose ``contents`` is the JSON object::

    {"from": "sha256/0a1b…", "to": {"sha256/9f8e…": "250"}, "fee_kismet": "0",
     "generation": 41, "created": "2026-10-21T14:03:22Z", "nonce": "…"}

It is known by its ``hash``, the hash of its ``contents``, so the same
contents signed again are the same transaction (§4.2). Its key is its
``from`` and its ``created``: two transactions with one key are one payment,
whatever else they hold. Its ``nonce`` is written last, so that a search for
one hashes what comes before it once (:meth:`Transaction.mined`), and the
number of leading bits its ``hash`` shares with the SHA-256 of
``transaction`` is its proof of work (§4.3).

The payout of each transaction block after the root moves the reward of the
generation before (§5.2). No one signs it: it is written as a signed
transaction is, with ``algorithm``, ``hash``, and ``contents``, but no
``signer`` and no ``signature``, and its ``from`` names the new Karma, which
comes from no one, as ``"null"``.

Nothing here reads the chain: whether a transaction is valid in a block, and
whether a payout is right, are for whoever checks the block.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass, field, replace
from datetime import datetime
from hashlib import sha256
from time import monotonic
from typing import Any, Callable, Final, Mapping

from libranet.bundle.errors import (
    BundleSignatureError,
    MalformedBundleError,
    UnsupportedBundleError,
)
from libranet.bundle.shapes import is_utf8
from libranet.bundle.signing import SIGNED_HASH_ALGORITHM, SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, DropTarget, NonceSearchers
from libranet.cas.nonce_search import NonceSearch
from libranet.cas.prefix import matching_bits
from libranet.json_fields import integer_field, string_field
from libranet.json_format import MOMENT_FORMAT, compact_json
from libranet.protocol.karma_amounts import MAX_KISMET, Kismet
from libranet.protocol.karma_members import (
    CREATED_MEMBER,
    amounts_member,
    check_moment,
    contents_value,
    id_member,
    moment_member,
)

#: The fewest leading bits a transaction's ``hash`` may share with
#: :data:`TRANSACTION_WORK_TARGET` (§4.3).
MIN_TRANSACTION_BITS: Final = 24

#: What a transaction's work is measured against: the SHA-256 of
#: ``transaction``, which is no block's target (§4.3).
TRANSACTION_WORK_TARGET: Final = DropTarget.of("transaction")

# The members of a transaction (§4.1), and of a payout, which has the first
# three (§5.2).
_FROM: Final = "from"
_TO: Final = "to"
_GENERATION: Final = "generation"
_FEE: Final = "fee_kismet"
_NONCE: Final = "nonce"

# What follows a transaction's nonce in its text: the quote that ends the
# string, and the brace that ends the object.
_AFTER_NONCE: Final = b'"}'

# The name a payout's "from" gives the new Karma, which comes from no one
# (§5.2).
_NEW_KARMA: Final = "null"

# The members of a payout as a block holds it, each a string, and those a
# signed transaction has that a payout does not (§5.2).
_PAYOUT_MEMBERS: Final = ("algorithm", "contents", "hash")
_SIGNED_ONLY: Final = ("signer", "signature")


@dataclass(frozen=True)
class TransactionKey:
    """What makes two transactions one payment: their ``sender``, and when they were ``created``.

    A sender stamps ``created`` once, and reuses it however often the
    payment is signed or mined again (§4.2).
    """

    sender: ContentId
    created: datetime

    @classmethod
    def from_value(cls, value: object, *, named: str = "A transaction's key") -> TransactionKey:
        """The key ``value`` writes, its ``from`` and ``created`` as a transaction writes them.

        Raises:
            UnknownAlgorithmError: it names a sender under a hash algorithm
                this node lacks.
            ValueError: ``value``, called ``named``, is not an object, or its
                ``from`` or ``created`` is not what a transaction's must be.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        return cls(
            id_member(value.get(_FROM), named=f'{named}\'s "{_FROM}"'),
            moment_member(value.get(CREATED_MEMBER), named=f'{named}\'s "{CREATED_MEMBER}"'),
        )

    def value(self) -> dict[str, str]:
        """This key as JSON: its ``from`` and ``created``, as its transaction writes them."""
        return {_FROM: str(self.sender), CREATED_MEMBER: self.created.strftime(MOMENT_FORMAT)}


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
        so that a search for a nonce hashes all before it once
        (:meth:`mined`).
        """
        return compact_json(self.value()).decode("utf-8")

    def mined(
        self,
        seconds: float,
        *,
        minimum_bits: int = MIN_TRANSACTION_BITS,
        clock: Callable[[], float] = monotonic,
        searchers: NonceSearchers = NONCE_SEARCHERS,
    ) -> Transaction:
        """This transaction, with the nonce, of those tried, that gives it the most work (§4.3).

        Its work is the leading bits its hash, that of its :meth:`text`,
        shares with :data:`TRANSACTION_WORK_TARGET`. Nonces are tried for
        ``seconds``, by ``clock``, and after that until the best gives at
        least ``minimum_bits``: by default :data:`MIN_TRANSACTION_BITS`, the
        fewest a valid transaction has. ``searchers`` try them, as they do a drop's
        (:meth:`~libranet.cas.drops.NonceSearchers.found`), hashing the text
        before the nonce once, and the ``"}`` after it with each. The nonce
        it had is not kept.

        Raises:
            ValueError: ``seconds`` is negative, or ``minimum_bits`` is
                negative or more than a hash has.
            OSError: a searching process could not be started, or exited
                during the search.
        """
        text = replace(self, nonce="").text().encode("utf-8")
        search = NonceSearch(
            text[: -len(_AFTER_NONCE)], TRANSACTION_WORK_TARGET.digest, suffix=_AFTER_NONCE
        )
        nonce = searchers.found(search, seconds, minimum_bits=minimum_bits, clock=clock)
        return replace(self, nonce=nonce.decode("ascii"))

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

        That is the SHA-256 of ``transaction``, :data:`TRANSACTION_WORK_TARGET` (§4.3).
        """
        return matching_bits(TRANSACTION_WORK_TARGET.hex, self.hash)

    def verified(self, public_key: bytes) -> None:
        """Raise unless its sender signed it, with ``public_key``, the key their id names.

        Raises:
            BundleSignatureError: ``public_key`` is not the sender's, or does
                not verify the signature (:meth:`SignedBundle.verified`).
        """
        self.bundle.verified(public_key)


@dataclass(frozen=True)
class Payout:
    """A transaction block's payout: the pot of the generation before, from ``sources`` to ``to``.

    ``sources`` maps each identity the pot comes from to all it gives, and
    ``None`` to the new Karma, which comes from no one. ``to`` maps each
    staker paid to their share. It is in transaction block ``generation``
    (§5.2). No one signs it: ``contents`` is the JSON text it is written
    as, known by its :attr:`hash`, which :meth:`of` writes and
    :meth:`from_value` reads.

    Raises:
        ValueError: ``generation`` is under 1, since the root holds no
            payout (§7.2), or ``to`` does not total ``sources``.
    """

    sources: Mapping[ContentId | None, Kismet]
    to: Mapping[ContentId, Kismet]
    generation: int
    contents: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ValueError(
                f'A payout\'s "{_GENERATION}" must be at least 1, since the root has none, '
                f"got {self.generation}"
            )

        given = sum(self.sources.values(), Kismet(0))
        paid = sum(self.to.values(), Kismet(0))

        if paid != given:
            raise ValueError(
                f'A payout\'s "{_TO}" must total its "{_FROM}", {given.value()} Kismet, '
                f"got {paid.value()}"
            )

    @classmethod
    def of(
        cls,
        sources: Mapping[ContentId | None, Kismet],
        to: Mapping[ContentId, Kismet],
        generation: int,
    ) -> Payout:
        """The payout of transaction block ``generation``, from ``sources`` to ``to``.

        Its contents are written as compact JSON text, each map sorted.

        Raises:
            ValueError: it is no payout, as :class:`Payout` says.
        """
        given = {
            _NEW_KARMA if source is None else str(source): amount.value()
            for source, amount in sources.items()
        }
        contents = {
            _FROM: dict(sorted(given.items())),
            _TO: {str(identity): amount.value() for identity, amount in sorted(to.items())},
            _GENERATION: generation,
        }
        return cls(sources, to, generation, compact_json(contents).decode("utf-8"))

    @classmethod
    def from_value(cls, value: object) -> Payout:
        """The payout ``value``, an object of a block's ``transactions``, is (§5.2).

        Members of its contents it does not know are passed over.

        Raises:
            MalformedBundleError: ``value`` is not an object whose
                ``algorithm``, ``contents``, and ``hash`` are strings, or it
                has a ``signer`` or a ``signature``.
            UnsupportedBundleError: it is hashed with an algorithm other than
                SHA-256.
            BundleSignatureError: its ``hash`` is not the hash of its
                ``contents``.
            UnknownAlgorithmError: its contents name an id under a hash
                algorithm this node lacks.
            ValueError: its contents are no payout, as the error names.
        """
        if not isinstance(value, dict) or any(name in value for name in _SIGNED_ONLY):
            raise MalformedBundleError('A payout is an object with no "signer" and no "signature"')

        algorithm, contents, stated = (
            string_field(value, name, error=MalformedBundleError) for name in _PAYOUT_MEMBERS
        )

        if algorithm != SIGNED_HASH_ALGORITHM:
            raise UnsupportedBundleError(
                f"A payout hashed with {algorithm!r} cannot be checked here"
            )

        if not is_utf8(contents):
            raise MalformedBundleError("A payout's contents must be UTF-8")

        if stated != _hash(contents):
            raise BundleSignatureError("The hash of a payout's contents is not its hash")

        terms = contents_value(contents, named="A payout")

        if not isinstance(terms, dict) or not isinstance(terms.get(_FROM), dict):
            raise ValueError(f'A payout must be a JSON object with a "{_FROM}" object')

        given = terms[_FROM]
        sources: dict[ContentId | None, Kismet] = dict(
            amounts_member(
                {name: amount for name, amount in given.items() if name != _NEW_KARMA},
                named=f'"{_FROM}"',
            ).items()
        )

        if _NEW_KARMA in given:
            sources[None] = Kismet.from_value(given[_NEW_KARMA], named=f'"{_FROM}" for null')

        return cls(
            sources,
            amounts_member(terms.get(_TO), named=f'"{_TO}"'),
            integer_field(terms, _GENERATION),
            contents,
        )

    def value(self) -> dict[str, str]:
        """This payout as a block's ``transactions`` holds it, unsigned (§5.2)."""
        return {"algorithm": SIGNED_HASH_ALGORITHM, "contents": self.contents, "hash": self.hash}

    @property
    def hash(self) -> str:
        """What it is known by, as a transaction is: the SHA-256 of its contents, in hex."""
        return _hash(self.contents)


def _hash(contents: str) -> str:
    """The SHA-256 of ``contents``' UTF-8 bytes, in lower-case hex, as a signed bundle hashes."""
    return sha256(contents.encode("utf-8")).hexdigest()
