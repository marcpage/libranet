"""Amounts of Karma, counted in Kismet, its smallest unit (Karma §4, KarmaLedger §4.1).

An amount is written as a string of decimal digits, with no sign, and with
no leading zero unless it is ``"0"``. Ten octillion Kismet, the most there
can be, does not fit a 64-bit integer, and a JavaScript number holds no whole
number above 2^53 exactly. A chain's weight is written the same way
(:mod:`libranet.karma.weight`).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final

from libranet.json_fields import decimal_count

#: How many Kismet make a Karma (Karma §4), for what is logged.
KISMET_PER_KARMA: Final = 10**14

#: The most Kismet there can be: 100 trillion Karma (Karma §4).
MAX_KISMET: Final = 10**28


@dataclass(frozen=True, order=True)
class Kismet:
    """An amount: ``count`` Kismet, from 0 to :data:`MAX_KISMET`.

    Raises:
        ValueError: ``count`` is negative, or more than there can be.
    """

    count: int

    def __post_init__(self) -> None:
        if not 0 <= self.count <= MAX_KISMET:
            raise ValueError(f"An amount must be from 0 to {MAX_KISMET} Kismet, got {self.count}")

    @classmethod
    def from_value(cls, value: object, *, named: str = "An amount") -> Kismet:
        """The amount ``value``, a string of decimal digits, writes (§4.1).

        Raises:
            ValueError: ``value``, called ``named``, is not a string of
                decimal digits, with no sign and no leading zero unless it is
                ``"0"``, or it is more than there can be.
        """
        count = decimal_count(value, named=named)

        if count > MAX_KISMET:
            raise ValueError(f"{named} must be at most {MAX_KISMET} Kismet, got {value}")

        return cls(count)

    def value(self) -> str:
        """This amount as a block or transaction states it: decimal digits (§4.1)."""
        return str(self.count)

    def __add__(self, other: Kismet) -> Kismet:
        """This amount and ``other`` together.

        Raises:
            ValueError: together they are more than there can be.
        """
        return Kismet(self.count + other.count)

    def __sub__(self, other: Kismet) -> Kismet:
        """This amount, with ``other`` taken from it.

        Raises:
            ValueError: ``other`` is more than this amount, since no amount
                is below 0.
        """
        return Kismet(self.count - other.count)
