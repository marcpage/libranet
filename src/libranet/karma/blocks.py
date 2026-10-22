"""Transaction blocks and validation blocks (KarmaLedger §5, §6).

Each generation has a transaction block, which records transfers of Kismet
and the balances they leave, and validation blocks, each a collection of
signed stakes on one transaction block. A block is a signed bundle, signed by
its builder, whose ``contents`` is JSON (§5.1, §6.1)::

    {"generation": 42, "previous": "sha256/…", "weight": "1288490188800",
     "reach": 3, "transactions": […], "balances": {"sha256/0a1b…":
     {"kismet": "1250000", "last_won": 40}}}

    {"generation": 42, "runner_up": "sha256/…", "stakes": […],
     "witnessed": ["…"]}

A block's builder answers for everything it states (§5.1). One that states
something false by itself, such as transactions out of order, is refused,
naming the statement, so that whoever reads it can distrust its builder
(§3.8). What it states falsely against its target and its chain is for
whoever checks it there. A block a builder could honestly make, such as one
breaking the half-free rule, states nothing false, and only scores zero
(§5.5).
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import pairwise
from re import compile as compile_pattern
from typing import Any, Final, Mapping, Sequence

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.json_fields import integer_field, is_integer
from libranet.json_format import compact_json
from libranet.karma.amounts import Kismet
from libranet.karma.members import contents_value, id_member
from libranet.karma.stakes import SignedStake
from libranet.karma.transactions import Payout, SignedTransaction, Transaction, TransactionKey
from libranet.karma.weight import ChainWeight

# The members of a block (§5.1, §6.1), and of a balance (§5.3).
_GENERATION: Final = "generation"
_PREVIOUS: Final = "previous"
_WEIGHT: Final = "weight"
_REACH: Final = "reach"
_TRANSACTIONS: Final = "transactions"
_BALANCES: Final = "balances"
_RUNNER_UP: Final = "runner_up"
_STAKES: Final = "stakes"
_WITNESSED: Final = "witnessed"
_KISMET: Final = "kismet"
_LAST_WON: Final = "last_won"

# The member of a transaction's contents naming its sender: an id, where a
# payout's names an object of them (§4.1, §5.2).
_FROM: Final = "from"

# How a witness is written: a transaction's hash, the SHA-256 of its
# contents in lower-case hex (§4.2, §6.4).
_HASH: Final = compile_pattern(r"[0-9a-f]{64}")

# The most balances a block may state, for each identity its transactions
# name as sender or recipient (§5.3).
_BALANCES_PER_IDENTITY: Final = 2


@dataclass(frozen=True)
class Balance:
    """The ``kismet`` an identity holds, and ``last_won``, the generation it last won a share in.

    ``last_won`` is ``None`` for one that has won none (§5.3).

    Raises:
        ValueError: ``last_won`` is negative.
    """

    kismet: Kismet
    last_won: int | None = None

    def __post_init__(self) -> None:
        if self.last_won is not None and self.last_won < 0:
            raise ValueError(
                f'A balance\'s "{_LAST_WON}" must not be negative, got {self.last_won}'
            )

    @classmethod
    def from_value(cls, value: object, *, named: str = "A balance") -> Balance:
        """The balance ``value``, an object of a block's ``balances``, is (§5.3).

        Raises:
            ValueError: ``value``, called ``named``, is not an object, its
                ``kismet`` is no amount, or its ``last_won`` is missing or is
                neither a generation nor ``null``.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        won = value.get(_LAST_WON)
        last_won: int | None = None

        if is_integer(won):
            last_won = won

        elif won is not None or _LAST_WON not in value:
            raise ValueError(f'{named}\'s "{_LAST_WON}" must be a generation or null')

        return cls(Kismet.from_value(value.get(_KISMET), named=f'{named}\'s "{_KISMET}"'), last_won)

    def value(self) -> dict[str, Any]:
        """This balance as a block states it (§5.3)."""
        return {_KISMET: self.kismet.value(), _LAST_WON: self.last_won}


@dataclass(frozen=True)
class TransactionBlock:
    """Transaction block ``generation``, built on the validation block ``previous`` (§5.1).

    ``previous`` is ``None`` in the root alone. ``weight`` is the chain's
    before it, and ``reach`` how many blocks back every balance is stated.
    ``transactions`` are sorted by hash, the payout among them, and
    ``balances`` are each identity's after them all.

    Raises:
        ValueError: it states something false by itself (§3.8):
            ``generation`` or ``reach`` is negative; its transactions are out
            of order or repeated, or two share a key; it holds no payout after
            the root, one in the root, more than one, or one of another
            generation; a transaction moves 0 Kismet outside the root; or it
            states more balances than §5.3 allows.
    """

    generation: int
    previous: ContentId | None
    weight: ChainWeight
    reach: int
    transactions: tuple[SignedTransaction | Payout, ...]
    balances: Mapping[ContentId, Balance]

    def __post_init__(self) -> None:
        if self.generation < 0 or self.reach < 0:
            raise ValueError(
                f'A transaction block\'s "{_GENERATION}" and "{_REACH}" must not be negative, '
                f"got {self.generation} and {self.reach}"
            )

        _check_sorted([transaction.hash for transaction in self.transactions], _TRANSACTIONS)
        self._check_payout()
        keys: set[TransactionKey] = set()

        for signed in self.signed:
            transaction = signed.transaction

            if transaction.key in keys:
                raise ValueError(
                    f"Transaction {signed.hash} shares its key with another in its block"
                )

            if transaction.sends() == Kismet(0) and not (
                self.generation == 0 and transaction.is_root
            ):
                raise ValueError(f"Transaction {signed.hash} moves 0 Kismet outside the root")

            keys.add(transaction.key)

        most = _BALANCES_PER_IDENTITY * len(self.named())

        if len(self.balances) > most:
            raise ValueError(
                f"A transaction block states at most {most} balances, twice the identities its "
                f"transactions name, got {len(self.balances)}"
            )

    @classmethod
    def from_value(cls, value: object) -> TransactionBlock:
        """The transaction block ``value``, the decoded ``contents`` of its builder's bundle, is.

        Members it does not know are passed over.

        Raises:
            UnsupportedBundleError: a transaction in it is hashed with an
                algorithm other than SHA-256, or names its signer under a hash
                algorithm this node lacks.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            BundleSignatureError: a transaction's ``hash`` is not the hash of
                its ``contents``.
            ValueError: ``value`` is not an object, or a member of it is
                missing or is not what it must be, as the error names; or it
                states something false, as :class:`TransactionBlock` says.
        """
        if not isinstance(value, dict):
            raise ValueError(
                f"A transaction block must be a JSON object, got {type(value).__name__}"
            )

        balances = value.get(_BALANCES)

        if not isinstance(balances, dict):
            raise ValueError(f'"{_BALANCES}" must be an object')

        stated: dict[ContentId, Balance] = {}

        for name, balance in balances.items():
            identity = id_member(name, named=f'A name in "{_BALANCES}"')

            if identity in stated:
                raise ValueError(f'"{_BALANCES}" names {identity} twice')

            stated[identity] = Balance.from_value(balance, named=f"The balance of {identity}")

        return cls(
            integer_field(value, _GENERATION),
            _optional_id(value, _PREVIOUS),
            ChainWeight.from_value(value.get(_WEIGHT), named=f'"{_WEIGHT}"'),
            integer_field(value, _REACH),
            tuple(_transaction(entry) for entry in _list_member(value, _TRANSACTIONS)),
            stated,
        )

    def value(self) -> dict[str, Any]:
        """This block as JSON, its balances sorted (§5.1)."""
        return {
            _GENERATION: self.generation,
            _PREVIOUS: None if self.previous is None else str(self.previous),
            _WEIGHT: self.weight.value(),
            _REACH: self.reach,
            _TRANSACTIONS: [transaction.value() for transaction in self.transactions],
            _BALANCES: {
                str(identity): balance.value()
                for identity, balance in sorted(self.balances.items())
            },
        }

    def text(self) -> str:
        """This block as compact JSON text, as its builder signs it."""
        return compact_json(self.value()).decode("utf-8")

    @property
    def signed(self) -> tuple[SignedTransaction, ...]:
        """Its transactions but the payout, each signed by its sender."""
        return tuple(entry for entry in self.transactions if isinstance(entry, SignedTransaction))

    @property
    def payout(self) -> Payout | None:
        """Its payout, which every block after the root holds (§5.2)."""
        return next((entry for entry in self.transactions if isinstance(entry, Payout)), None)

    @property
    def is_half_free(self) -> bool:
        """Whether at least half its transactions but the payout are free (§5.1)."""
        free = sum(1 for signed in self.signed if signed.transaction.fee == Kismet(0))
        return 2 * free >= len(self.signed)

    def named(self) -> set[ContentId]:
        """The identities its transactions name as sender or recipient, the payout's among them."""
        identities: set[ContentId] = set()

        for signed in self.signed:
            identities.add(signed.transaction.sender)
            identities.update(signed.transaction.to)

        if self.payout is not None:
            identities.update(source for source in self.payout.sources if source is not None)
            identities.update(self.payout.to)

        return identities

    def bundles(self) -> tuple[SignedBundle, ...]:
        """The signed bundles it holds: each transaction but the payout, as its sender signed it."""
        return tuple(signed.bundle for signed in self.signed)

    def _check_payout(self) -> None:
        """Raise unless it holds one payout of its generation, or none in the root (§5.2, §7.2).

        Raises:
            ValueError: it does not.
        """
        payouts = [entry for entry in self.transactions if isinstance(entry, Payout)]

        if self.generation == 0 and payouts:
            raise ValueError("The root's transaction block holds no payout")

        if self.generation > 0 and len(payouts) != 1:
            raise ValueError(
                f"A transaction block after the root holds one payout, got {len(payouts)}"
            )

        for payout in payouts:
            if payout.generation != self.generation:
                raise ValueError(
                    f"A payout's \"{_GENERATION}\" must be its block's, {self.generation}, "
                    f"got {payout.generation}"
                )


@dataclass(frozen=True)
class ValidationBlock:
    """Validation block ``generation``: ``stakes``, each on the one transaction block it validates.

    ``runner_up`` names a rival validation block whose stakes are taken, or
    is ``None``. ``witnessed`` lists the hashes of transactions its builder
    has seen. Its stakes are sorted by hash, and its witnesses sorted, each
    only once (§6.1).

    Raises:
        ValueError: ``generation`` is negative, its stakes or its witnesses
            are out of order or repeated, or a witness is no hash.
    """

    generation: int
    runner_up: ContentId | None
    stakes: tuple[SignedStake, ...]
    witnessed: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError(
                f'A validation block\'s "{_GENERATION}" must not be negative, got {self.generation}'
            )

        _check_sorted([stake.hash for stake in self.stakes], _STAKES)

        for witness in self.witnessed:
            if _HASH.fullmatch(witness) is None:
                raise ValueError(f'"{_WITNESSED}" must list hashes, got {witness!r}')

        _check_sorted(self.witnessed, _WITNESSED)

    @classmethod
    def from_value(cls, value: object) -> ValidationBlock:
        """The validation block ``value``, the decoded ``contents`` of its builder's bundle, is.

        Members it does not know are passed over.

        Raises:
            UnsupportedBundleError: a stake in it is hashed with an algorithm
                other than SHA-256, or names its signer under a hash algorithm
                this node lacks.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            BundleSignatureError: a stake's ``hash`` is not the hash of its
                ``contents``.
            ValueError: ``value`` is not an object, or a member of it is
                missing or is not what it must be, as the error names; or it
                is out of order, as :class:`ValidationBlock` says.
        """
        if not isinstance(value, dict):
            raise ValueError(
                f"A validation block must be a JSON object, got {type(value).__name__}"
            )

        witnessed = _list_member(value, _WITNESSED)
        witnesses = tuple(witness for witness in witnessed if isinstance(witness, str))

        if len(witnesses) < len(witnessed):
            raise ValueError(f'"{_WITNESSED}" must list hashes')

        return cls(
            integer_field(value, _GENERATION),
            _optional_id(value, _RUNNER_UP),
            tuple(SignedStake.from_value(stake) for stake in _list_member(value, _STAKES)),
            witnesses,
        )

    def value(self) -> dict[str, Any]:
        """This block as JSON (§6.1)."""
        return {
            _GENERATION: self.generation,
            _RUNNER_UP: None if self.runner_up is None else str(self.runner_up),
            _STAKES: [stake.value() for stake in self.stakes],
            _WITNESSED: list(self.witnessed),
        }

    def text(self) -> str:
        """This block as compact JSON text, as its builder signs it."""
        return compact_json(self.value()).decode("utf-8")

    def bundles(self) -> tuple[SignedBundle, ...]:
        """The signed bundles it holds: each stake, as its staker signed it."""
        return tuple(stake.bundle for stake in self.stakes)


def _optional_id(value: Mapping[str, object], name: str) -> ContentId | None:
    """The id ``value`` names as its member ``name``, or ``None`` if it names ``null``.

    Raises:
        UnknownAlgorithmError: it names an id under a hash algorithm this node
            lacks.
        ValueError: the member is missing, or is neither an id nor ``null``.
    """
    if name not in value:
        raise ValueError(f'"{name}" must be an id or null, and is missing')

    member = value[name]
    return None if member is None else id_member(member, named=f'"{name}"')


def _list_member(value: Mapping[str, object], name: str) -> list[object]:
    """The list ``value`` holds as its member ``name``.

    Raises:
        ValueError: it holds something else there, or nothing.
    """
    member = value.get(name)

    if not isinstance(member, list):
        raise ValueError(f'"{name}" must be a list')

    return member


def _transaction(value: object) -> SignedTransaction | Payout:
    """The transaction ``value``, an object of a block's ``transactions``, is, or its payout.

    A payout is told by its ``from``, an object, where a signed transaction's
    is an id (§5.2), and one that is signed is refused.

    Raises:
        UnsupportedBundleError: it is hashed with an algorithm other than
            SHA-256, or names its signer under a hash algorithm this node lacks.
        UnknownAlgorithmError: what it states names an id under a hash
            algorithm this node lacks.
        BundleSignatureError: its ``hash`` is not the hash of its ``contents``.
        ValueError: it is neither a transaction nor a payout, or it is a
            payout that is signed.
    """
    if not isinstance(value, dict):
        raise ValueError(f"Each of a block's {_TRANSACTIONS} must be an object")

    if "signature" not in value:
        return Payout.from_value(value)

    bundle = SignedBundle.from_value(value)
    terms = contents_value(bundle.contents, named="A transaction")

    if isinstance(terms, dict) and isinstance(terms.get(_FROM), dict):
        raise ValueError(f"A block's payout must not be signed, as {bundle.signer} signed it")

    return SignedTransaction(bundle, Transaction.from_value(terms))


def _check_sorted(keys: Sequence[str], named: str) -> None:
    """Raise unless ``keys``, of a block's ``named``, are sorted, none twice (§5.1, §6.1).

    Raises:
        ValueError: they are not.
    """
    if any(later <= earlier for earlier, later in pairwise(keys)):
        raise ValueError(f'A block\'s "{named}" must be sorted, none twice')
