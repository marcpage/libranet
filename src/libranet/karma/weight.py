"""A chain's weight: the work it took to build (KarmaLedger §3.3).

A drop whose id shares b leading bits with its target hash takes about 2^b
hashes to find, so a chain weighs the sum of 2^b over both drops of every
generation. Summing bits instead would let two 15-bit drops weigh as much as
one of 30 bits, which took 32,768 times the work of either. A transaction
block states the weight of the chain before it as a string of decimal
digits, as an amount is written, since one drop alone may add 2^256 (§5.1).
"""

from __future__ import annotations
from dataclasses import dataclass

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.config.models import TARGET_BITS
from libranet.json_fields import decimal_count


@dataclass(frozen=True, order=True)
class ChainWeight:
    """A chain's weight, ``work``: the sum of 2^b over its drops, each matching b bits.

    Raises:
        ValueError: ``work`` is negative.
    """

    work: int

    def __post_init__(self) -> None:
        if self.work < 0:
            raise ValueError(f"A chain's weight must not be negative, got {self.work}")

    @classmethod
    def of_bits(cls, bits: int) -> ChainWeight:
        """The weight of a drop matching ``bits`` leading bits: 2^``bits``.

        Raises:
            ValueError: ``bits`` is negative, or more than a hash has.
        """
        if not 0 <= bits <= TARGET_BITS:
            raise ValueError(f"bits must be from 0 to {TARGET_BITS}, got {bits}")

        return cls(2**bits)

    @classmethod
    def of_drop(cls, drop: ContentId, target: DropTarget) -> ChainWeight:
        """The weight of the drop ``drop``, placed at ``target``.

        Its bits are those its hash shares with the target hash, counted as
        a search counts them (:mod:`libranet.cas.prefix`).
        """
        return cls.of_bits(matching_bits(target.hex, drop.hash))

    @classmethod
    def from_value(cls, value: object, *, named: str = "A chain's weight") -> ChainWeight:
        """The weight ``value``, a string of decimal digits, writes (§5.1).

        Raises:
            ValueError: ``value``, called ``named``, is not a string of
                decimal digits, with no sign and no leading zero unless it is
                ``"0"``.
        """
        return cls(decimal_count(value, named=named))

    def value(self) -> str:
        """This weight as a transaction block states it: decimal digits (§5.1)."""
        return str(self.work)

    def __add__(self, other: ChainWeight) -> ChainWeight:
        """This weight and ``other`` together."""
        return ChainWeight(self.work + other.work)

    def __sub__(self, other: ChainWeight) -> ChainWeight:
        """This weight, with ``other`` taken from it.

        Raises:
            ValueError: ``other`` is more than this weight, since no weight
                is negative.
        """
        return ChainWeight(self.work - other.work)
