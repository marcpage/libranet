"""Every holder's Karma, as the chain the node follows states it (Phase 6 Step 128).

An identity's Karma, to a node, is its balance as the chain the node follows
states it: the latest stated of it in the head's transaction block, or in
those before it as far back as that block's reach (KarmaLedger §5.3), with
the reward of the head's validation block
paid, as the next generation's transactions are checked against it (§2). No
peer's claim of its own Karma is read.

Each time its head changes, the Karma module writes every balance so read
to ``balances.json`` in ``storage.karma_dir``, with the head it read them
at, as ``head.json`` names it (:mod:`libranet.protocol.karma_head`)::

    {"head": "sha256/…", "generation": 41, "weight": "…",
     "balances": {"sha256/…": {"kismet": "250", "last_won": "39.125"}}}

each of the head's members ``null``, and ``balances`` empty, while it
follows no chain. An identity it does not name holds nothing:
``{"kismet": "0", "last_won": null}``. The web server reads it here,
and the stats module, to order the nodes it lists by Karma (Phase 6 Step
141), since neither loads the Karma module's code (Module System §2).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from json import loads
from pathlib import Path
from re import compile as compile_pattern
from typing import Any, Final, Mapping

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.json_format import compact_json
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_head import KarmaHead

#: The file of the Karma module's history stating every holder's balance.
KARMA_BALANCES_FILE: Final = "balances.json"

#: The member of ``balances.json`` stating each identity's balance.
BALANCES_FIELD: Final = "balances"

#: How many decimal places of a generation a ``last_won`` is stated to (KarmaLedger §5.3).
LAST_WON_PLACES: Final = 3

#: The parts of a generation a ``last_won``, and so a stake's age, is counted
#: in: thousandths (KarmaLedger §5.3, §6.6).
GENERATION_PARTS: Final = 10**LAST_WON_PLACES

# The members of a balance (KarmaLedger §5.3).
_KISMET: Final = "kismet"
_LAST_WON: Final = "last_won"

# A ``last_won`` as written: a generation in decimal digits, with no leading
# zero unless it is 0, and up to LAST_WON_PLACES decimal places, the last of
# them not 0, so that each is written one way only (KarmaLedger §5.3).
_LAST_WON_FORMAT: Final = compile_pattern(
    rf"(0|[1-9][0-9]*)(\.[0-9]{{0,{LAST_WON_PLACES - 1}}}[1-9])?"
)


@dataclass(frozen=True, order=True)
class LastWon:
    """The effective generation a balance last won, in ``thousandths`` of a generation.

    It is written as a string of the generation in decimal digits, with up
    to three decimal places, such as ``"40"`` or ``"39.125"`` (KarmaLedger
    §5.3).

    Raises:
        ValueError: ``thousandths`` is negative.
    """

    thousandths: int

    def __post_init__(self) -> None:
        if self.thousandths < 0:
            raise ValueError(
                f'A balance\'s "{_LAST_WON}" must not be negative, '
                f"got {self.thousandths} thousandths of a generation"
            )

    @classmethod
    def of(cls, generation: int) -> LastWon:
        """Generation ``generation``, whole: the ``last_won`` of a staker that won it.

        Raises:
            ValueError: ``generation`` is negative.
        """
        return cls(generation * GENERATION_PARTS)

    @classmethod
    def from_value(cls, value: object, *, named: str = 'A balance\'s "last_won"') -> LastWon:
        """The ``last_won`` ``value``, a string of a generation to a thousandth, writes (§5.3).

        Raises:
            ValueError: ``value``, called ``named``, is not a string of
                decimal digits, with no leading zero unless the generation is
                0, and up to three decimal places, the last of them not 0.
        """
        if not isinstance(value, str) or _LAST_WON_FORMAT.fullmatch(value) is None:
            raise ValueError(
                f"{named} must be a generation or null, written as a string of decimal digits "
                f"with up to {LAST_WON_PLACES} decimal places and no trailing zero, got {value!r}"
            )

        whole, _, places = value.partition(".")
        return cls(int(whole) * GENERATION_PARTS + int(places.ljust(LAST_WON_PLACES, "0")))

    def value(self) -> str:
        """This ``last_won`` as a balance states it: the generation, to a thousandth (§5.3)."""
        whole, part = divmod(self.thousandths, GENERATION_PARTS)

        if part == 0:
            return str(whole)

        return f"{whole}.{part:0{LAST_WON_PLACES}d}".rstrip("0")

    def moved(self, kept: Kismet, received: Kismet, generation: int) -> LastWon:
        """This, held for ``kept``, moved toward ``generation`` by ``received``'s share (§5.3).

        It is rounded up, to a thousandth of a generation, so that rounding
        never makes what is held older, and costs it no more than a
        thousandth of a generation of age: however little is received, it
        moves this only by its share.
        """
        weighted = kept.count * self.thousandths + received.count * generation * GENERATION_PARTS
        return LastWon(-(-weighted // (kept.count + received.count)))

    def since(self, generation: int) -> int:
        """The thousandths of a generation from this to generation ``generation``."""
        return generation * GENERATION_PARTS - self.thousandths


@dataclass(frozen=True)
class Balance:
    """The ``kismet`` an identity holds, and ``last_won``, the effective generation it last won.

    ``last_won`` is the generation it last won a share in, moved later by the
    Kismet it has received since, by amount (:meth:`received`), and is
    ``None`` for one holding nothing (KarmaLedger §5.3).

    Raises:
        ValueError: ``last_won`` is given for a balance holding nothing.
    """

    kismet: Kismet
    last_won: LastWon | None = None

    def __post_init__(self) -> None:
        if self.last_won is not None and self.kismet == Kismet(0):
            raise ValueError(
                f'A balance holding no Kismet has no "{_LAST_WON}", got {self.last_won.value()}'
            )

    def sent(self, amount: Kismet) -> Balance:
        """This balance less ``amount``: its ``last_won`` as it was, or ``None`` if nothing is left.

        Raises:
            ValueError: ``amount`` is more than it holds.
        """
        if amount == Kismet(0):
            return self

        left = self.kismet - amount
        return Balance(left, None if left == Kismet(0) else self.last_won)

    def received(self, amount: Kismet, generation: int) -> Balance:
        """This balance and ``amount``, received as if won in generation ``generation`` (§5.3).

        Its ``last_won`` moves toward ``generation`` by ``amount``'s share of
        what it then holds, rounded up to a thousandth of a generation, so
        that rounding never makes what it holds older (:meth:`LastWon.moved`).
        One holding nothing takes ``generation``.

        Raises:
            ValueError: it would hold more than there can be.
        """
        if amount == Kismet(0):
            return self

        held = self.kismet + amount

        if self.last_won is None:
            return Balance(held, LastWon.of(generation))

        return Balance(held, self.last_won.moved(self.kismet, amount, generation))

    @classmethod
    def from_value(cls, value: object, *, named: str = "A balance") -> Balance:
        """The balance ``value``, an object of a block's ``balances``, is (KarmaLedger §5.3).

        Raises:
            ValueError: ``value``, called ``named``, is not an object, its
                ``kismet`` is no amount, or its ``last_won`` is missing or is
                neither a generation, as :meth:`LastWon.from_value` reads one,
                nor ``null``.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        if _LAST_WON not in value:
            raise ValueError(f'{named}\'s "{_LAST_WON}" must be a generation or null')

        won = value[_LAST_WON]
        last_won = (
            None if won is None else LastWon.from_value(won, named=f'{named}\'s "{_LAST_WON}"')
        )
        return cls(Kismet.from_value(value.get(_KISMET), named=f'{named}\'s "{_KISMET}"'), last_won)

    def value(self) -> dict[str, Any]:
        """This balance as a block states it (KarmaLedger §5.3)."""
        return {
            _KISMET: self.kismet.value(),
            _LAST_WON: None if self.last_won is None else self.last_won.value(),
        }


#: The balance of an identity stated nowhere, and of every one holding
#: nothing: it holds nothing, and has no ``last_won`` (KarmaLedger §5.3).
NO_BALANCE: Final = Balance(Kismet(0))


@dataclass(frozen=True)
class KarmaBalances:
    """Each identity's balance in ``balances``, as the chain stated it at ``head``.

    Raises:
        ValueError: it states balances with no head.
    """

    head: KarmaHead = KarmaHead()
    balances: Mapping[ContentId, Balance] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.head.follows and self.balances:
            raise ValueError("Only the chain followed states balances")

    @classmethod
    def from_value(cls, value: object) -> KarmaBalances:
        """The balances ``value``, the JSON of ``balances.json``, states.

        Members it does not know are passed over.

        Raises:
            ValueError: ``value`` is not an object, or names no head, as
                :meth:`KarmaHead.from_value` says, or its ``balances`` is not
                an object of balances, each named by an identity.
        """
        if not isinstance(value, dict):
            raise ValueError(f"Karma balances must be a JSON object, got {type(value).__name__}")

        stated = value.get(BALANCES_FIELD)

        if not isinstance(stated, dict):
            raise ValueError(f'"{BALANCES_FIELD}" must be an object')

        return cls(
            KarmaHead.from_value(value),
            {
                ContentId.parse(name): Balance.from_value(balance, named=f"The balance of {name}")
                for name, balance in stated.items()
            },
        )

    @classmethod
    def read(cls, karma_dir: Path) -> KarmaBalances:
        """The balances ``balances.json`` in ``karma_dir`` states, or none before it is written.

        Raises:
            OSError: it could not be read, other than for there being none.
            ValueError: it is not JSON, or states no balances, as
                :meth:`from_value` says.
        """
        try:
            data = (karma_dir / KARMA_BALANCES_FILE).read_bytes()

        except FileNotFoundError:
            # Not logged: the Karma module has not read a head's balances yet.
            return cls()

        return cls.parsed(data)

    @classmethod
    def parsed(cls, data: bytes) -> KarmaBalances:
        """The balances ``data``, what ``balances.json`` holds, states.

        Raises:
            ValueError: it is not JSON, or states no balances, as
                :meth:`from_value` says.
        """
        return cls.from_value(loads(data))

    def balance(self, identity: ContentId) -> Balance:
        """``identity``'s balance, as stated, or else none: it holds nothing (KarmaLedger §5.3)."""
        return self.balances.get(identity, NO_BALANCE)

    def holders(self) -> int:
        """How many identities hold Kismet, which sets how large a full block is (KarmaLedger §5.4).

        These are the balances the next generation's transactions are
        checked against, so their holders are those its blocks are measured
        by.
        """
        return sum(1 for balance in self.balances.values() if balance.kismet != Kismet(0))

    def value(self) -> dict[str, Any]:
        """These balances as ``balances.json`` states them, sorted by identity."""
        return {
            **self.head.value(),
            BALANCES_FIELD: {
                str(identity): balance.value()
                for identity, balance in sorted(self.balances.items())
            },
        }

    def write(self, karma_dir: Path) -> None:
        """Write ``balances.json`` in ``karma_dir``, replacing it whole.

        Raises:
            OSError: it could not be written, which leaves the one before.
        """
        write_atomically(karma_dir / KARMA_BALANCES_FILE, compact_json(self.value()))
