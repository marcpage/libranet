"""A generation's arithmetic: balances, reach, the pot, and shares (KarmaLedger §5.3, §6.6).

Nothing here reads the store. The balances a transaction block leaves are
worked out from each identity's balance before it, the latest stated, and
its reach from the balances it and the blocks before it state. The reward
of a generation's winning validation block, its pot, is what the next
transaction block's payout takes and pays: the generation's new Karma, the
fees of its transaction block, and its runner-up's stakes, less those of
every staker on the winning side.

The pot is shared among the winning block's stakers by effective stake:
each stake's Kismet times its staker's age, the generations since its
``last_won``, to a thousandth, up to the age limit its transaction block
states, rounded down to whole Kismet × generations. Every node
divides a pot alike, to the Kismet, since a payout is known by the hash of
what it states: each share is rounded down, and the Kismet left over go to
the block's builder (§6.6).
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Collection, Final, Mapping, Sequence

from libranet.cas.content_id import ContentId
from libranet.karma.blocks import TransactionBlock, ValidationBlock
from libranet.karma.generations import new_kismet
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import GENERATION_PARTS, NO_BALANCE, Balance, LastWon
from libranet.protocol.karma_transactions import Payout

#: The age limit while every stake fits in a validation block: the most
#: generations of age that count, however few hold Kismet (§6.6).
AGE_LIMIT_FLOOR: Final = 10

#: How many times the identities holding Kismet, for each stake a validation
#: block holds, the age limit is, so that a staker holding a hundredth of the
#: average can still win (§6.6).
AGE_LIMIT_FACTOR: Final = 100

#: About how many stakes fit in a validation block (KarmaScaling §2).
STAKES_PER_BLOCK: Final = 5_000


@dataclass(frozen=True, order=True)
class StakeAge:
    """How long a staker's stakes count for: ``thousandths`` of a generation, up to the age limit.

    It is 0 only for a staker holding nothing, as the root's creator does
    (§6.6, §7.3).

    Raises:
        ValueError: ``thousandths`` is negative.
    """

    thousandths: int

    def __post_init__(self) -> None:
        if self.thousandths < 0:
            raise ValueError(
                f"A stake's age must not be negative, got {self.thousandths} thousandths"
            )

    @classmethod
    def of(cls, balance: Balance, generation: int, limit: int) -> StakeAge:
        """The age in validation block ``generation`` of a staker holding ``balance``.

        ``balance`` is the staker's as transaction block ``generation``
        leaves it, and ``limit`` the age limit that block states. A staker's
        age is the generations since its ``last_won``, to a thousandth, up
        to ``limit``: one just after a win, or after receiving all it holds,
        two a generation later, and so on (§6.6, Karma §23).

        Raises:
            ValueError: ``balance`` last won less than a generation before
                ``generation``, which no balance transaction block
                ``generation`` leaves has.
        """
        if balance.last_won is None:
            return cls(0)

        since = balance.last_won.since(generation)

        if since < GENERATION_PARTS:
            raise ValueError(
                f"A staker in generation {generation} must have last won a generation or more "
                f"before it, got {balance.last_won.value()}"
            )

        return cls(min(since, limit * GENERATION_PARTS))

    def effective(self, kismet: int) -> int:
        """``kismet`` staked at this age, in Kismet × generations, rounded down (§6.6)."""
        return kismet * self.thousandths // GENERATION_PARTS

    @staticmethod
    def limit(holders: int) -> int:
        """The age limit of a transaction block leaving ``holders`` holding Kismet (§6.6).

        That is :data:`AGE_LIMIT_FACTOR` times ``holders``, for each of
        :data:`STAKES_PER_BLOCK`, rounded down, and at least
        :data:`AGE_LIMIT_FLOOR`.
        """
        return max(AGE_LIMIT_FLOOR, AGE_LIMIT_FACTOR * holders // STAKES_PER_BLOCK)

    @classmethod
    def effective_stakes(
        cls, validation: ValidationBlock, balances: Mapping[ContentId, Balance], limit: int
    ) -> dict[ContentId, int]:
        """Each staker's effective stake in ``validation``, in Kismet × generations (§6.5, §6.6).

        That is all the staker's stakes in it, which count alike, times its
        age, from its balance in ``balances``, the latest stated through the
        transaction block ``validation`` validates, up to ``limit``, the age
        limit that block states, rounded down (:meth:`effective`). A staker
        ``balances`` lacks holds nothing (§5.3).

        Raises:
            ValueError: a staker's balance last won less than a generation
                before ``validation``'s.
        """
        staked: dict[ContentId, int] = {}

        for signed in validation.stakes:
            staked[signed.staker] = staked.get(signed.staker, 0) + signed.stake.kismet.count

        return {
            staker: cls.of(
                balances.get(staker, NO_BALANCE), validation.generation, limit
            ).effective(count)
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
    def of(
        cls,
        block: TransactionBlock,
        before: Mapping[ContentId, Balance],
        *,
        stakers: Collection[ContentId],
    ) -> BalanceSheet:
        """The balances ``block`` is checked against and leaves, from ``before``.

        ``stakers`` are those of validation block g − 1, whose reward its
        payout pays. The payout is applied first: each identity its ``from``
        names gives what it names, and each its ``to`` names is paid what it
        names. A staker paid has won generation g − 1, and has ``last_won``
        g − 1; the builder, if it is no staker, receives what it is paid.
        Then each transaction is: its sender less all it pays, its fee aside,
        which the next block's payout takes, and each recipient receives what
        it is paid. What an identity receives in the block moves its
        ``last_won`` toward g − 1, by amount, after all it sends
        (:meth:`~libranet.protocol.karma_balances.Balance.received`), so that
        the order of the block's transactions does not matter.

        Raises:
            ValueError: the payout takes more from an identity than it holds,
                or an identity would hold more than there can be.
        """
        checked = cls._paid(block, before, stakers)
        sending: dict[ContentId, int] = {}

        for signed in block.signed:
            sender = signed.transaction.sender
            sending[sender] = sending.get(sender, 0) + signed.transaction.sends().count

        overspent = frozenset(
            sender for sender, count in sending.items() if count > checked[sender].kismet.count
        )
        sent, received = cls._moved(block, overspent)
        held = {
            identity: balance.sent(sent.get(identity, Kismet(0))).received(
                received.get(identity, Kismet(0)), block.generation - 1
            )
            for identity, balance in checked.items()
        }
        return cls(block, before, checked, held, overspent)

    def holders_after(self, holders: int) -> int:
        """How many hold Kismet after the block, ``holders`` holding it as the block is checked.

        Those are in every identity's balance the block is checked against,
        with the payout applied (§5.3, §5.4), so only those the block names
        change from them.
        """
        return (
            holders
            - sum(1 for balance in self.checked.values() if balance.kismet != Kismet(0))
            + sum(1 for balance in self.after.values() if balance.kismet != Kismet(0))
        )

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
    def _moved(
        block: TransactionBlock, overspent: frozenset[ContentId]
    ) -> tuple[dict[ContentId, Kismet], dict[ContentId, Kismet]]:
        """What each identity sends in ``block``, fees aside, and receives, but ``overspent``'s.

        Raises:
            ValueError: an identity would receive more than there can be.
        """
        sent: dict[ContentId, Kismet] = {}
        received: dict[ContentId, Kismet] = {}

        for signed in block.signed:
            transaction = signed.transaction

            if transaction.sender not in overspent:
                paid = transaction.sends() - transaction.fee
                sent[transaction.sender] = sent.get(transaction.sender, Kismet(0)) + paid

                for identity, amount in transaction.to.items():
                    received[identity] = received.get(identity, Kismet(0)) + amount

        return sent, received

    @staticmethod
    def _paid(
        block: TransactionBlock, before: Mapping[ContentId, Balance], stakers: Collection[ContentId]
    ) -> dict[ContentId, Balance]:
        """The balance of each identity ``block`` names, from ``before``, with its payout applied.

        Each of ``stakers`` it pays has won, and any other it pays, the
        builder, receives what it is paid.

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

            held[source] = held[source].sent(amount)

        won = block.generation - 1

        for identity, amount in payout.to.items():
            if identity in stakers:
                held[identity] = Balance(held[identity].kismet + amount, LastWon.of(won))

            else:
                held[identity] = held[identity].received(amount, won)

        return held


@dataclass(frozen=True)
class Reward:
    """The reward of ``validation``, a generation's winning validation block, as shared (§6.6).

    ``builder`` signed it. ``block`` is the transaction block it validates,
    and ``balances`` each staker's latest balance stated through it, as
    ``block`` leaves them. ``runner_up`` is the validation block it names as
    its runner-up, if it names one.

    Raises:
        ValueError: ``validation`` holds no stake, ``block`` or
            ``runner_up`` is of another generation, or ``runner_up`` is given
            when ``validation`` names none, or not given when it names one.
    """

    validation: ValidationBlock
    builder: ContentId
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
        """What the pot pays each staker, and ``builder``, as the payout's ``to`` states it (§6.6).

        The pot is divided by effective stake, each staker's age up to the
        age limit ``block`` states, or equally among the stakers when that
        totals 0, as only the root's one stake of ``"0"`` does, every other
        stake being of Kismet at least a generation old (§7.4). Each share is
        rounded down, and the Kismet left over, fewer than there are stakers,
        go to ``builder``, whether it stakes or not, so that the shares total
        the pot. One paid 0 is left out.

        Raises:
            ValueError: a staker's balance last won ``validation``'s
                generation or later.
        """
        stakes = StakeAge.effective_stakes(self.validation, self.balances, self.block.age_limit)
        total = sum(stakes.values())

        if total == 0:
            stakes = dict.fromkeys(stakes, 1)
            total = len(stakes)

        pot = sum(amount.count for amount in self.pot().values())
        counts = {staker: pot * stake // total for staker, stake in stakes.items()}
        counts[self.builder] = counts.get(self.builder, 0) + pot - sum(counts.values())
        return {identity: Kismet(count) for identity, count in sorted(counts.items()) if count > 0}

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
