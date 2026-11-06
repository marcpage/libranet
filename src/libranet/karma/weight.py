"""A chain's weight: the work it took to build, and the stake behind it (KarmaLedger §3.3).

A drop whose id shares b leading bits with its target hash takes about 2^b
hashes to find. A chain weighs the sum, over its generations, of 2^b for the
transaction block's drop, and 2^b for the validation block's times its total
effective stake, in Kismet × generations (§6.6), or once where that is 0, as
only the root's is. So a validation block holding twice the stake weighs as
one sharing a bit more. Summing bits instead would let two 12-bit drops weigh
as much as one of 24 bits, which took 4,096 times the work of either. No
more than ``COUNTED_BITS`` are counted: a bit past them is luck, or hashing
bought, and would otherwise let one block searched for again outweigh every
generation after it. A transaction block states the weight of the chain
before it as a string of decimal digits, as an amount is written (§5.1).

A validation block's total effective stake counts in the chain's weight only
up to :data:`STAKE_BASELINE_MULTIPLE` times the stake baseline its
transaction block states: a running average of what the chain's recent
winning validation blocks counted (:class:`StakeBaseline`). So stake held
back from staking, and saved up, cannot stand in for more than that of
work: a block of saved-up stake weighs at most that many honest blocks of
the same work (§3.3, §8).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final

from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.config.models import TARGET_BITS
from libranet.json_fields import decimal_count

# The most bits a drop's weight counts (KarmaLedger §3.3): the 24 a search
# returns by default, and 3 more. So luck may make a block weigh up to 8 times
# as much as another of the same stake, and still decide between rivals, but no
# block weighs more than 8 of 24 bits holding the same stake do.
COUNTED_BITS: Final = 27


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

        Bits past ``COUNTED_BITS`` add nothing, so a drop of more weighs
        2^``COUNTED_BITS``.

        Raises:
            ValueError: ``bits`` is negative, or more than a hash has.
        """
        if not 0 <= bits <= TARGET_BITS:
            raise ValueError(f"bits must be from 0 to {TARGET_BITS}, got {bits}")

        return cls(2 ** min(bits, COUNTED_BITS))

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
        in Kismet × generations, or once if that is 0, as only the root's is.

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


#: How many times the stake baseline a validation block's total effective
#: stake counts in the chain's weight, at most (§3.3).
STAKE_BASELINE_MULTIPLE: Final = 2

#: The most generations the stake baseline is a running average over (§3.3).
STAKE_BASELINE_GENERATIONS: Final = 1_000

# How many times faster than the generations since the root the stake
# baseline's window grows, until it is STAKE_BASELINE_GENERATIONS long, so
# that it keeps up with a new chain, whose stake grows as its Kismet does.
_WINDOW_DIVISOR: Final = 4


@dataclass(frozen=True, order=True)
class StakeBaseline:
    """What a validation block's total effective stake counts against: ``stake`` (§3.3).

    ``stake``, in Kismet × generations as an effective stake is, is a running
    average of what the chain's recent winning validation blocks counted in
    its weight. A transaction block states the baseline of the validation
    blocks staking on it. It is 0 for the root's, and for generation 1's,
    whose validation blocks count all their stake, the first after the root
    then setting it.

    Raises:
        ValueError: ``stake`` is negative.
    """

    stake: int

    def __post_init__(self) -> None:
        if self.stake < 0:
            raise ValueError(f"A stake baseline must not be negative, got {self.stake}")

    @classmethod
    def from_value(cls, value: object, *, named: str = "A stake baseline") -> StakeBaseline:
        """The baseline ``value``, a string of decimal digits, writes (§5.1).

        Raises:
            ValueError: ``value``, called ``named``, is not a string of
                decimal digits, with no sign and no leading zero unless it is
                ``"0"``.
        """
        return cls(decimal_count(value, named=named))

    def value(self) -> str:
        """This baseline as a transaction block states it: decimal digits (§5.1)."""
        return str(self.stake)

    def counted(self, total: int) -> int:
        """What a validation block's total effective stake of ``total`` counts in chain weight.

        That is no more than :data:`STAKE_BASELINE_MULTIPLE` times this
        baseline, or all of it while the baseline is 0.
        """
        if self.stake == 0:
            return total

        return min(total, STAKE_BASELINE_MULTIPLE * self.stake)

    def after(self, total: int, generation: int) -> StakeBaseline:
        """The next generation's baseline, after winning validation block ``generation``.

        ``total`` is that block's total effective stake, and this its
        baseline. What it counts moves the baseline toward it by one part in
        the window, ``generation`` divided by 4, plus 1, up to
        :data:`STAKE_BASELINE_GENERATIONS`, rounded down; while the baseline
        is 0, it is what that block counts. So it rises no more than a part
        in the window a generation, as what a block counts is no more than
        twice it, and the most ``total`` can be bounds it.
        """
        counted = self.counted(total)

        if self.stake == 0:
            return StakeBaseline(counted)

        window = min(STAKE_BASELINE_GENERATIONS, generation // _WINDOW_DIVISOR + 1)
        return StakeBaseline(self.stake + (counted - self.stake) // window)


#: The stake baseline of the root's transaction block, and generation 1's,
#: against which a validation block counts all its stake (§3.3).
NO_STAKE_BASELINE: Final = StakeBaseline(0)
