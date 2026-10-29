"""Every holder's Karma, as the chain the node follows states it (Phase 6 Step 128).

An identity's Karma, to a node, is its balance as the chain the node follows
states it: the latest stated of it in the head's transaction block, or in
those before it as far back as that block's reach, or ten blocks, whichever is
further (KarmaLedger §5.3). No peer's claim of its own Karma is read.

Each time its head changes, the Karma module writes every balance so stated
to ``balances.json`` in ``storage.karma_dir``, with the head it read them
at, as ``head.json`` names it (:mod:`libranet.protocol.karma_head`)::

    {"head": "sha256/…", "generation": 41, "weight": "…",
     "balances": {"sha256/…": {"kismet": "250", "last_won": 40}}}

each of the head's members ``null``, and ``balances`` empty, while it
follows no chain. An identity it does not name holds nothing, and has won
nothing: ``{"kismet": "0", "last_won": null}``. The web server reads it here,
and the stats module, to order the nodes it lists by Karma (Phase 6 Step
141), since neither loads the Karma module's code (Module System §2).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from json import loads
from pathlib import Path
from typing import Any, Final, Mapping

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.json_fields import decimal_count, is_integer
from libranet.json_format import compact_json
from libranet.protocol.karma_head import KarmaHead

#: The file of the Karma module's history stating every holder's balance.
KARMA_BALANCES_FILE: Final = "balances.json"

#: The member of ``balances.json`` stating each identity's balance.
BALANCES_FIELD: Final = "balances"

# The members of a balance (KarmaLedger §5.3).
_KISMET: Final = "kismet"
_LAST_WON: Final = "last_won"


@dataclass(frozen=True)
class KarmaBalance:
    """The ``kismet`` an identity holds, and ``last_won``, the generation it last won a share in.

    ``last_won`` is ``None`` for one that has won none (KarmaLedger §5.3).

    Raises:
        ValueError: ``kismet`` or ``last_won`` is negative.
    """

    kismet: int = 0
    last_won: int | None = None

    def __post_init__(self) -> None:
        if self.kismet < 0:
            raise ValueError(f"A balance must not be negative, got {self.kismet}")

        if self.last_won is not None and self.last_won < 0:
            raise ValueError(
                f'A balance\'s "{_LAST_WON}" must not be negative, got {self.last_won}'
            )

    @classmethod
    def from_value(cls, value: object, *, named: str = "A balance") -> KarmaBalance:
        """The balance ``value``, as a transaction block states one, is.

        Raises:
            ValueError: ``value``, called ``named``, is not an object, its
                ``kismet`` is not decimal digits, or its ``last_won`` is
                missing or is neither a generation nor ``null``.
        """
        if not isinstance(value, dict):
            raise ValueError(f"{named} must be a JSON object, got {type(value).__name__}")

        won = value.get(_LAST_WON)

        if not (is_integer(won) or (won is None and _LAST_WON in value)):
            raise ValueError(f'{named}\'s "{_LAST_WON}" must be a generation or null')

        return cls(decimal_count(value.get(_KISMET), named=f'{named}\'s "{_KISMET}"'), won)

    def value(self) -> dict[str, Any]:
        """This balance as a transaction block states it, its Kismet in decimal digits."""
        return {_KISMET: str(self.kismet), _LAST_WON: self.last_won}


@dataclass(frozen=True)
class KarmaBalances:
    """Each identity's balance in ``balances``, as the chain stated it at ``head``.

    Raises:
        ValueError: it states balances with no head.
    """

    head: KarmaHead = KarmaHead()
    balances: Mapping[ContentId, KarmaBalance] = field(default_factory=dict)

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
                ContentId.parse(name): KarmaBalance.from_value(
                    balance, named=f"The balance of {name}"
                )
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

        return cls.from_value(loads(data))

    def balance(self, identity: ContentId) -> KarmaBalance:
        """``identity``'s balance, as stated, or else none: it holds nothing (KarmaLedger §5.3)."""
        return self.balances.get(identity, KarmaBalance())

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
