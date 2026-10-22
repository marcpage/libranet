"""A generation's arithmetic: balances, reach, the pot, and shares (KarmaLedger §5.3, §6.6).

Nothing here reads the store. The balances a transaction block leaves are
worked out from each identity's balance before it, the latest stated, and
its reach from the balances it and the blocks before it state. The reward
of a generation's winning validation block, its pot, is what the next
transaction block's payout takes and pays: the generation's new Karma, the
fees of its transaction block, and its runner-up's stakes, less those of
every staker on the winning side.

The pot is shared among the winning block's stakers by effective stake:
each stake's Kismet times its staker's weight, a tenth for each generation
since the staker last won, up to the whole. Every node divides a pot alike,
to the Kismet, since a payout is known by the hash of what it states: each
share is rounded down, and the Kismet left over go one each to the largest
remainders, ties to the id that sorts first (§6.6).
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from typing import Final, Mapping, Sequence

from libranet.cas.content_id import ContentId
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import Balance, TransactionBlock, ValidationBlock
from libranet.karma.generations import new_kismet
from libranet.karma.transactions import Payout

#: How many generations after a win a staker's weight takes to grow back
#: whole, a tenth each generation (§6.6, Karma §23).
WEIGHT_GENERATIONS: Final = 10

#: The balance of an identity stated nowhere: it holds nothing, and has won
#: nothing (§5.3).
NO_BALANCE: Final = Balance(Kismet(0))


@dataclass(frozen=True, order=True)
class StakeWeight:
    """How much of a staker's stakes count: ``tenths`` tenths of each, from 1 to all of them.

    Raises:
        ValueError: ``tenths`` is under 1, or more than
            :data:`WEIGHT_GENERATIONS`.
    """

    tenths: int

    def __post_init__(self) -> None:
        if not 1 <= self.tenths <= WEIGHT_GENERATIONS:
            raise ValueError(
                f"A stake's weight must be from 1 to {WEIGHT_GENERATIONS} tenths, got {self.tenths}"
            )

    @classmethod
    def of(cls, balance: Balance, generation: int) -> StakeWeight:
        """The weight in validation block ``generation`` of a staker holding ``balance``.

        ``balance`` is the staker's as transaction block ``generation``
        leaves it. A staker weighs a tenth for each generation since its
        ``last_won``, up to the whole, and the whole if it has won none
        (§6.6): a tenth just after a win, two tenths a generation later, and
        so on (Karma §23).

        Raises:
            ValueError: ``balance`` last won ``generation`` or later, which no
                balance transaction block ``generation`` leaves has.
        """
        if balance.last_won is None:
            return cls(WEIGHT_GENERATIONS)

        since = generation - balance.last_won

        if since < 1:
            raise ValueError(
                f"A staker in generation {generation} must have last won before it, "
                f"got {balance.last_won}"
            )

        return cls(min(since, WEIGHT_GENERATIONS))

    @classmethod
    def effective_stakes(
        cls, validation: ValidationBlock, balances: Mapping[ContentId, Balance]
    ) -> dict[ContentId, int]:
        """Each staker's effective stake in ``validation``, in tenths of a Kismet (§6.5, §6.6).

        That is all the staker's stakes in it, which weigh alike, times its
        weight, from its balance in ``balances``: the latest stated through
        the transaction block ``validation`` validates. A staker
        ``balances`` lacks holds nothing, and has won nothing (§5.3).

        Raises:
            ValueError: a staker's balance last won ``validation``'s
                generation or later.
        """
        staked: dict[ContentId, int] = {}

        for signed in validation.stakes:
            staked[signed.staker] = staked.get(signed.staker, 0) + signed.stake.kismet.count

        return {
            staker: count * cls.of(balances.get(staker, NO_BALANCE), validation.generation).tenths
            for staker, count in staked.items()
        }


@dataclass(frozen=True)
class BalanceSheet:
    """The balances transaction block ``block`` is checked against, and those it leaves (§5.3).

    ``before`` is each identity's balance before the block, the latest
    stated, and one it lacks holds nothing. ``checked`` are those of the
    identities the block names, with its payout applied (§5.2), which its
    transactions are checked against. ``after`` are theirs after every
    transaction, which the block must state. ``overspent`` are the senders
    ``checked`` does not cover for all they send in the block, fees among
    it (§4.4): their transactions are not valid there, and are left out of
    ``after``. :meth:`of` works them out.
    """

    block: TransactionBlock
    before: Mapping[ContentId, Balance]
    checked: Mapping[ContentId, Balance]
    after: Mapping[ContentId, Balance]
    overspent: frozenset[ContentId]

    @classmethod
    def of(cls, block: TransactionBlock, before: Mapping[ContentId, Balance]) -> BalanceSheet:
        """The balances ``block`` is checked against and leaves, from ``before``.

        Its payout is applied first: each identity its ``from`` names gives
        what it names, and each its ``to`` names is paid its share, and has
        ``last_won`` g − 1. Then each transaction is: its sender less all it
        pays, its fee aside, which the next block's payout takes, and each
        recipient plus what it is paid.

        Raises:
            ValueError: the payout takes more from an identity than it holds,
                or an identity would hold more than there can be.
        """
        checked = cls._paid(block, before)
        held = dict(checked)
        sending: dict[ContentId, int] = {}

        for signed in block.signed:
            sender = signed.transaction.sender
            sending[sender] = sending.get(sender, 0) + signed.transaction.sends().count

        overspent = frozenset(
            sender for sender, count in sending.items() if count > checked[sender].kismet.count
        )

        for signed in block.signed:
            transaction = signed.transaction

            if transaction.sender not in overspent:
                paid = transaction.sends() - transaction.fee
                held[transaction.sender] = replace(
                    held[transaction.sender], kismet=held[transaction.sender].kismet - paid
                )

                for identity, amount in transaction.to.items():
                    held[identity] = replace(held[identity], kismet=held[identity].kismet + amount)

        return cls(block, before, checked, held, overspent)

    def balance(self, identity: ContentId) -> Balance:
        """``identity``'s balance after the block: as it leaves it, if it names it, or as before."""
        if identity in self.after:
            return self.after[identity]

        return self.before.get(identity, NO_BALANCE)

    def unstated(self) -> list[ContentId]:
        """The identities the block names but does not state, sorted: it must state each (§5.3)."""
        return sorted(set(self.after) - set(self.block.balances))

    def misstated(self) -> list[ContentId]:
        """The identities whose ``kismet`` or ``last_won`` the block states wrongly, sorted (§5.3).

        Each balance it states is checked, whether it must state it or
        states it only to shorten its reach.
        """
        return sorted(
            identity
            for identity, balance in self.block.balances.items()
            if balance != self.balance(identity)
        )

    @staticmethod
    def _paid(
        block: TransactionBlock, before: Mapping[ContentId, Balance]
    ) -> dict[ContentId, Balance]:
        """The balance of each identity ``block`` names, from ``before``, with its payout applied.

        Raises:
            ValueError: the payout takes more from an identity than it holds,
                or an identity would hold more than there can be.
        """
        held = {identity: before.get(identity, NO_BALANCE) for identity in block.named()}
        payout = block.payout

        if payout is None:
            return held

        # The new Karma, under None, is taken from no one (§5.2).
        taken = {source: amount for source, amount in payout.sources.items() if source is not None}

        for source, amount in taken.items():
            if amount > held[source].kismet:
                raise ValueError(
                    f"A payout takes {amount.value()} Kismet from {source}, which holds "
                    f"{held[source].kismet.value()}"
                )

            held[source] = replace(held[source], kismet=held[source].kismet - amount)

        for identity, amount in payout.to.items():
            held[identity] = Balance(held[identity].kismet + amount, block.generation - 1)

        return held


@dataclass(frozen=True)
class Reward:
    """The reward of ``validation``, a generation's winning validation block, as shared (§6.6).

    ``block`` is the transaction block it validates, and ``balances`` each
    staker's latest balance stated through it, as ``block`` leaves them.
    ``runner_up`` is the validation block it names as its runner-up, if it
    names one.

    Raises:
        ValueError: ``validation`` holds no stake, ``block`` or
            ``runner_up`` is of another generation, or ``runner_up`` is given
            when ``validation`` names none, or not given when it names one.
    """

    validation: ValidationBlock
    block: TransactionBlock
    balances: Mapping[ContentId, Balance]
    runner_up: ValidationBlock | None = None

    def __post_init__(self) -> None:
        if not self.validation.stakes:
            raise ValueError("A reward is shared among stakers, and its validation block has none")

        others = (self.block,) if self.runner_up is None else (self.block, self.runner_up)

        for other in others:
            if other.generation != self.validation.generation:
                raise ValueError(
                    f"A reward's blocks must be of generation {self.validation.generation}, "
                    f"got {other.generation}"
                )

        if (self.runner_up is None) != (self.validation.runner_up is None):
            raise ValueError(
                "A reward's runner-up must be given when its validation block names one, "
                "and only then"
            )

    def pot(self) -> dict[ContentId | None, Kismet]:
        """Whom the pot is taken from, and how much, as the payout's ``from`` states it (§5.2).

        That is the generation's new Karma, under ``None``; each sender's
        fees in ``block``; and each runner-up staker's stakes, unless that
        staker has a stake in ``validation`` too; summed by identity. One
        that gives nothing is left out.

        Raises:
            ValueError: one gives more than there can be.
        """
        given: dict[ContentId | None, int] = {None: new_kismet(self.validation.generation).count}

        for signed in self.block.signed:
            sender = signed.transaction.sender
            given[sender] = given.get(sender, 0) + signed.transaction.fee.count

        if self.runner_up is not None:
            winning = {stake.staker for stake in self.validation.stakes}

            for stake in self.runner_up.stakes:
                if stake.staker not in winning:
                    given[stake.staker] = given.get(stake.staker, 0) + stake.stake.kismet.count

        return {source: Kismet(count) for source, count in given.items() if count > 0}

    def shares(self) -> dict[ContentId, Kismet]:
        """Each staker's share of the pot, as the payout's ``to`` states it (§5.2, §6.6).

        The pot is divided by effective stake, or equally among the stakers
        when that totals 0, as the root's one stake of ``"0"`` does (§7.4).
        Each share is rounded down, and the Kismet left over go one each to
        the stakers of the largest remainders, ties to the id that sorts
        first, so that the shares total the pot. A staker whose share is 0
        is left out.

        Raises:
            ValueError: a staker's balance last won ``validation``'s
                generation or later.
        """
        stakes = StakeWeight.effective_stakes(self.validation, self.balances)
        total = sum(stakes.values())

        if total == 0:
            stakes = dict.fromkeys(stakes, 1)
            total = len(stakes)

        pot = sum(amount.count for amount in self.pot().values())
        counts = {staker: pot * stake // total for staker, stake in stakes.items()}
        largest = sorted(stakes, key=lambda staker: (-(pot * stakes[staker] % total), staker))

        for staker in largest[: pot - sum(counts.values())]:
            counts[staker] += 1

        return {staker: Kismet(count) for staker, count in sorted(counts.items()) if count > 0}

    def payout(self) -> Payout:
        """The payout of the next transaction block, which pays this reward (§5.2)."""
        return Payout.of(self.pot(), self.shares(), self.validation.generation + 1)


def reach_of(stated: Sequence[Mapping[ContentId, Balance]]) -> int:
    """The reach of transaction block g, from the balances it and those before it state (§5.3).

    ``stated[0]`` are block g's, ``stated[1]`` block g − 1's, and so on
    back. The reach is the least r such that each identity holding more
    than 0 Kismet, by the latest balance stated of it, is stated in one of
    blocks g − r through g. ``stated`` goes back at least as far as block
    g − 1's reach does from it, or to the root, since each identity holding
    anything before block g is stated there, and block g states the rest.
    """
    seen: set[ContentId] = set()
    reach = 0

    for back, balances in enumerate(stated):
        for identity, balance in balances.items():
            if identity not in seen and balance.kismet > Kismet(0):
                reach = back

            seen.add(identity)

    return reach
