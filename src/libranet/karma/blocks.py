"""Transaction blocks and validation blocks, each a drop (KarmaLedger §2, §5, §6).

Each generation has a transaction block, which records transfers of Kismet
and the balances they leave, and validation blocks, each a collection of
signed stakes on one transaction block. A block is a signed bundle, signed by
its builder, whose ``contents`` is JSON (§5.1, §6.1)::

    {"generation": 42, "previous": "sha256/…", "weight": "1288490188800",
     "reach": 3, "transactions": […], "balances": {"sha256/0a1b…":
     {"kismet": "1250000", "last_won": 40}}}

    {"generation": 42, "runner_up": "sha256/…", "stakes": […],
     "witnessed": ["…"]}

It is made a drop: the signed bundle's JSON, a null byte, and a nonce, which
lies outside the signature, so a builder signs once and then searches for a
nonce (§2).

A block that is not signed, or whose signature fails, can be attributed to
no one, and is shunned, distrusting no one. One its builder signed that
states something false distrusts its builder (§3.8). What a block states
falsely by itself is found here, before anything of the chain is read: what
it states falsely against its target and its chain is for whoever checks it
there. Since a drop's nonce is not signed, anyone may place a copy of a
signed block at any target, so one read at a target not its own, as one of
the other kind is, states nothing false there, and is passed over
(:class:`MisplacedBlockError`). A block a builder could honestly make, such as one not full or one
breaking the half-free rule, states nothing false, and only scores zero
(§5.5).
"""

from __future__ import annotations
from dataclasses import dataclass
from itertools import pairwise
from re import compile as compile_pattern
from typing import Any, Final, Generic, Mapping, Sequence, TypeVar
from zlib import compress

from libranet.bundle.errors import (
    BundleError,
    BundleSignatureError,
    MalformedBundleError,
    MissingContentError,
)
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import STORAGE_COMPRESSION_LEVEL
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR
from libranet.cas.errors import UnknownAlgorithmError
from libranet.config.models import KIB, MIB, TARGET_BITS
from libranet.json_fields import integer_field, is_integer
from libranet.json_format import compact_json
from libranet.karma.amounts import Kismet
from libranet.karma.generations import MIN_TRANSACTION_BITS, GenerationTargets
from libranet.karma.members import contents_value, id_member
from libranet.karma.stakes import SignedStake
from libranet.karma.transactions import Payout, SignedTransaction, Transaction, TransactionKey
from libranet.karma.weight import ChainWeight

#: The most a block's drop may be, compressed: 1 MiB (§5.4).
MAX_BLOCK_BYTES: Final = 1 * MIB

#: How far under :data:`MAX_BLOCK_BYTES` a block's drop, compressed, may be
#: and still be full: 128 KiB, room for what its builder cannot control (§5.4).
FULL_BLOCK_ROOM_BYTES: Final = 128 * KIB

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

# The room left in a block's drop, compressed, for its nonce, which is
# searched for once the block is filled: a count in decimal, which takes
# fewer bytes than this, compressed, however long the search.
_NONCE_ROOM_BYTES: Final = 32


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

        most = self.most_balances

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

    @property
    def most_balances(self) -> int:
        """The most balances it may state: two for each identity its transactions name (§5.3)."""
        return _BALANCES_PER_IDENTITY * len(self.named())

    def bundles(self) -> tuple[SignedBundle, ...]:
        """The signed bundles it holds: each transaction but the payout, as its sender signed it."""
        return tuple(signed.bundle for signed in self.signed)

    def weight_through(self, block_id: ContentId, validation_id: ContentId) -> ChainWeight:
        """The chain's weight through this block, ``block_id``, and the block validating it.

        That is its ``weight``, the chain's before it, and 2^b for each of
        the two drops, it and validation block ``validation_id``, each
        matching b bits of its target hash (§3.5).
        """
        targets = GenerationTargets.built_on(self.previous)
        return (
            self.weight
            + ChainWeight.of_drop(block_id, targets.transaction)
            + ChainWeight.of_drop(validation_id, targets.validation)
        )

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


@dataclass(frozen=True)
class BlockBounds:
    """What a block is measured against: how large it may be, and how much work it holds.

    A block's drop, compressed, is full when it is no larger than
    ``most_bytes`` and larger than ``most_bytes`` less ``room_bytes`` (§5.4),
    and each transaction in a block shares at least ``transaction_bits``
    with the target of its work (§4.3). :data:`BLOCK_BOUNDS` are the
    specification's: tests give smaller, so that a few transactions fill a
    block, and each is mined in moments.

    Raises:
        ValueError: ``most_bytes`` is under 1, ``room_bytes`` is negative or
            more than ``most_bytes``, or ``transaction_bits`` is negative or
            more than a hash has.
    """

    most_bytes: int = MAX_BLOCK_BYTES
    room_bytes: int = FULL_BLOCK_ROOM_BYTES
    transaction_bits: int = MIN_TRANSACTION_BITS

    def __post_init__(self) -> None:
        if self.most_bytes < 1 or not 0 <= self.room_bytes <= self.most_bytes:
            raise ValueError(
                f"A block's bounds must be at least 1 byte, with room from 0 to it, got "
                f"{self.most_bytes} and {self.room_bytes}"
            )

        if not 0 <= self.transaction_bits <= TARGET_BITS:
            raise ValueError(
                f"transaction_bits must be from 0 to {TARGET_BITS}, got {self.transaction_bits}"
            )

    def is_full(self, compressed_bytes: int) -> bool:
        """Whether a block whose drop the node holds compressed in ``compressed_bytes`` is full.

        A node measures the copy it holds, since copies compressed otherwise
        measure otherwise, and only a full block is validated (§5.4).
        """
        return self.most_bytes - self.room_bytes < compressed_bytes <= self.most_bytes


#: The bounds the specification sets: full within 128 KiB under 1 MiB, and
#: 24 bits of work to each transaction (§4.3, §5.4).
BLOCK_BOUNDS: Final = BlockBounds()


class MisplacedBlockError(ValueError):
    """A block was read at a target its own contents do not place it at (§3.8).

    It is of the other kind than the target's, or built on another block
    than the one the target is named for. A drop's nonce is not signed, so
    anyone may place a copy of a signed block at any target: such a block is
    passed over, and states nothing false of the target it is read at.
    """


@dataclass(frozen=True)
class BlockFinding:
    """Why a block is not taken, by itself: it is unsigned, or it states ``statement`` falsely.

    ``builder`` is whoever signed a block found false, whom a node then
    distrusts. It is ``None`` for a block that is unsigned, or whose
    signature fails, which can be attributed to no one: a node shuns it, and
    distrusts no one for it (§3.8). ``staker`` is, of a validation block
    found false for a stake that alone exceeds what covers it, whoever
    signed that stake, whom a node distrusts too, since that never changes
    (§6.3).
    """

    statement: str
    builder: ContentId | None = None
    staker: ContentId | None = None


_Block = TypeVar("_Block", TransactionBlock, ValidationBlock)


@dataclass(frozen=True)
class BlockDrop(Generic[_Block]):
    """``block``, as its builder signed it in ``bundle``: a drop's content, before its nonce (§2).

    :meth:`of` and :meth:`decoded` build one, reading ``block`` from what
    ``bundle`` signs. A builder signs the block's text once, and hands
    :meth:`content` to :meth:`~libranet.cas.drops.DropTarget.placed`, which
    searches for a nonce.
    """

    bundle: SignedBundle
    block: _Block

    @classmethod
    def of(cls, bundle: SignedBundle, kind: type[_Block]) -> BlockDrop[_Block]:
        """The block of ``kind`` that ``bundle`` signs, its signatures not yet checked.

        Raises:
            UnsupportedBundleError: what it holds is signed in a way this node
                cannot check.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            BundleSignatureError: the ``hash`` of what it holds is not the
                hash of its ``contents``.
            ValueError: what it signs is no block of ``kind``, as the error
                names.
        """
        return cls(bundle, kind.from_value(contents_value(bundle.contents, named="A block")))

    @classmethod
    def parsed(cls, data: bytes, kind: type[_Block]) -> BlockDrop[_Block]:
        """The block of ``kind`` the drop ``data`` holds, its signatures not checked.

        A block already checked, as one of the chain a node follows is, is
        read again so.

        Raises:
            MalformedBundleError: ``data`` holds no null byte before its
                nonce, or what is before it is not a signed bundle's JSON.
            UnsupportedBundleError: it, or what it holds, is signed in a way
                this node cannot check.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
            BundleSignatureError: the ``hash`` of what it holds is not the
                hash of its ``contents``.
            ValueError: what it signs is no block of ``kind``, as the error
                names.
        """
        content, separator, _ = data.rpartition(DROP_SEPARATOR)

        if not separator:
            raise MalformedBundleError("A block's drop must hold a null byte before its nonce")

        return cls.of(SignedBundle.decoded(content), kind)

    @classmethod
    def decoded(
        cls,
        data: bytes,
        kind: type[_Block],
        keys: Mapping[ContentId, bytes],
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> BlockDrop[_Block] | BlockFinding:
        """The block of ``kind`` the drop ``data`` holds, or what it states falsely by itself.

        Each signature in it is checked with the key ``keys`` gives its
        signer, and each transaction's work against ``bounds``. A signature
        whose key is not given is neither unsigned nor false: the caller
        fetches the key, and reads the block again.

        Returns:
            The block its builder signed, if nothing in it is found false; or
            a :class:`BlockFinding`, naming no builder if it is unsigned or
            its signature fails, and its builder if it is signed and states
            something false.

        Raises:
            MisplacedBlockError: it is no block of ``kind``, but one of the
                other kind, placed at another's target.
            MissingContentError: names the keys, of its builder and of each
                signer in it, that ``keys`` lacks, when nothing is found false
                without them.
            UnsupportedBundleError: it, or what it holds, is signed in a way
                this node cannot check.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
        """
        content, separator, _ = data.rpartition(DROP_SEPARATOR)

        if not separator:
            return BlockFinding("A block's drop must hold a null byte before its nonce")

        try:
            bundle = SignedBundle.decoded(content)

        except (MalformedBundleError, BundleSignatureError) as error:
            # Not logged: it is the finding, for the caller to log.
            return BlockFinding(f"A block's drop must be a signed bundle: {error}")

        signers = [bundle.signer]
        drop: BlockDrop[_Block] | None = None
        statement = ""

        # Read before its builder's signature is checked, so that every key
        # it needs is asked for at once.
        try:
            drop = cls.of(bundle, kind)
            signers.extend(signed.signer for signed in drop.block.bundles())

        except UnknownAlgorithmError:
            raise

        except (ValueError, BundleSignatureError) as error:
            other = cls._other_kind(bundle, kind)

            if other:
                raise MisplacedBlockError(f"It is a {other}, not a {kind.__name__}") from error

            # Not logged: it is the finding, once the builder's signature checks.
            statement = str(error)

        wanted = tuple(dict.fromkeys(signer for signer in signers if signer not in keys))

        if bundle.signer in wanted:
            raise MissingContentError(wanted)

        try:
            bundle.verified(keys[bundle.signer])

        except BundleSignatureError as error:
            # Not logged: it is the finding, for the caller to log.
            return BlockFinding(f"A block's signature must check: {error}")

        if drop is None:
            return BlockFinding(statement, bundle.signer)

        statement = cls._false_statement(drop.block, keys, bounds)

        if statement:
            return BlockFinding(statement, bundle.signer)

        if wanted:
            raise MissingContentError(wanted)

        return drop

    def content(self) -> bytes:
        """The drop's content: the signed bundle's JSON, which a null byte and a nonce follow."""
        return self.bundle.encoded()

    def measured_bytes(self) -> int:
        """What its drop measures once placed, as its builder fills it (§5.4).

        That is its content and null byte, compressed as the node stores
        what it stores, with room for the nonce searched for after.
        """
        compressed = compress(self.content() + DROP_SEPARATOR, STORAGE_COMPRESSION_LEVEL)
        return len(compressed) + _NONCE_ROOM_BYTES

    @property
    def builder(self) -> ContentId:
        """Who built the block: its signer, who answers for everything it states (§5.1)."""
        return self.bundle.signer

    @staticmethod
    def _other_kind(bundle: SignedBundle, kind: type[_Block]) -> str:
        """The name of the kind of block ``bundle`` signs, other than ``kind``, or ``""``."""
        other: type[TransactionBlock] | type[ValidationBlock] = (
            ValidationBlock if kind is TransactionBlock else TransactionBlock
        )

        try:
            other.from_value(contents_value(bundle.contents, named="A block"))

        except (BundleError, ValueError):
            # Not logged: it is no block of either kind, which its reader finds false.
            return ""

        return other.__name__

    @staticmethod
    def _false_statement(
        block: TransactionBlock | ValidationBlock,
        keys: Mapping[ContentId, bytes],
        bounds: BlockBounds,
    ) -> str:
        """What ``block`` states falsely in what it holds, or ``""``, as far as ``keys`` check it.

        A transaction under ``bounds``' work is false (§4.3), and so is a
        transaction or a stake whose signature fails, under the key ``keys``
        gives its signer. One whose key is not given is passed over.
        """
        # Only a transaction block holds work to count.
        if isinstance(block, TransactionBlock):
            for signed in block.signed:
                if signed.work_bits < bounds.transaction_bits:
                    return (
                        f"Transaction {signed.hash} shares {signed.work_bits} bits with the "
                        f"target of its work, under {bounds.transaction_bits}"
                    )

        for bundle in block.bundles():
            if bundle.signer in keys:
                try:
                    bundle.verified(keys[bundle.signer])

                except BundleSignatureError as error:
                    # Not logged: it is the finding, for the caller to log.
                    return f"What {bundle.signer} signed in a block must check: {error}"

        return ""


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
