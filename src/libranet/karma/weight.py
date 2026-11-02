"""A chain's weight: the work it took to build, and the stake behind it (KarmaLedger §3.3).

A drop whose id shares b leading bits with its target hash takes about 2^b
hashes to find. A chain weighs the sum, over its generations, of 2^b for the
transaction block's drop, and 2^b for the validation block's times its total
effective stake, in tenths of a Kismet (§6.6), or once where that is 0, as
only the root's is. So a validation block holding twice the stake weighs as
one sharing a bit more. Summing bits instead would let two 15-bit drops weigh
as much as one of 30 bits, which took 32,768 times the work of either. A
transaction block states the weight of the chain before it as a string of
decimal digits, as an amount is written, since one drop alone may add more
than 2^256 (§5.1).
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
    """A chain's weight, ``work``: the sum of what each of its drops weighs (§3.3).

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
    def of_staked(cls, bits: int, stake: int) -> ChainWeight:
        """The weight of a validation drop matching ``bits`` bits, its block holding ``stake``.

        That is 2^``bits`` times ``stake``, its block's total effective stake
        in tenths of a Kismet, or once if that is 0, as only the root's is.

        Raises:
            ValueError: ``bits`` is negative, or more than a hash has, or
                ``stake`` is negative.
        """
        if stake < 0:
            raise ValueError(f"A validation block's stake must not be negative, got {stake}")

        return cls(cls.of_bits(bits).work * max(stake, 1))

    @classmethod
    def of_validation(cls, drop: ContentId, target: DropTarget, stake: int) -> ChainWeight:
        """The weight of the validation drop ``drop``, placed at ``target``, holding ``stake``.

        Raises:
            ValueError: ``stake`` is negative.
        """
        return cls.of_staked(matching_bits(target.hex, drop.hash), stake)

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
