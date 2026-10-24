"""Combining transaction blocks (KarmaLedger §3.4, §5.3, §5.4, §5.6, Phase 6 Step 134).

Builders combine the blocks put up at a generation's targets, false starts
among them (§3.4), into larger ones, until one is full. What a block built
on a head takes of the transactions they hold, and of those the node
carries, is chosen here, in the order it takes them, and the block of the
first few of them written (:class:`CombinedBlock`).

Each transaction is taken on its own, whoever built the block it is read
from, a distrusted identity among them (§3.8, §4.4): one whose signature
checks, with its sender's key held, whose work meets the bounds, and that
moves something. One of a generation after the block's, or whose key the
chain holds, is left out (§4.4), and of those sharing a key, all but the
one of most bits. Free ones are taken by most bits first, and paid ones by
the largest fees, a paid one only while as many free ones are taken, so
that at least half of them are free (§5.1, §5.4), and each only while its
sender's balance covers it, and all of the sender's taken before it.

The block states its payout, the balances of everyone it names, its
``reach``, and its ``weight``, each as it is. It states the balances of
others holding Kismet too, those stated longest ago first, as many as
shorten its reach within the cap of twice those it names (§5.3).
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, replace
from typing import Iterable, Mapping

from libranet.cas.content_id import ContentId
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import BLOCK_BOUNDS, Balance, BlockBounds, TransactionBlock
from libranet.karma.checking import ChainBefore, ChainView
from libranet.karma.ledger import BalanceSheet, reach_of
from libranet.karma.transactions import Payout, SignedTransaction, TransactionKey


def _most_bits_first(signed: SignedTransaction) -> tuple[int, str]:
    """What orders transactions by most work first, and of as much, by hash (§4.3, §5.4)."""
    return (-signed.work_bits, signed.hash)


@dataclass(frozen=True)
class CombinedBlock:
    """The transactions a block built on ``before`` takes, in the order it takes them (§5.4).

    ``payout`` pays the reward of the validation block it is built on.
    ``balances`` are those of everyone ``payout`` and ``taken`` name, as the
    block before leaves them, and ``stated`` the balances the blocks before
    state, from the one before back as far as its reach. The first few of
    ``taken``, however many, keep the half-free rule, and are covered by
    their senders' balances, so that :meth:`block` of them is correct, but
    perhaps for being full. :meth:`of` chooses them.
    """

    before: ChainBefore
    payout: Payout
    taken: tuple[SignedTransaction, ...]
    balances: Mapping[ContentId, Balance]
    stated: tuple[Mapping[ContentId, Balance], ...]

    @classmethod
    def of(
        cls,
        offered: Iterable[SignedTransaction],
        head: ContentId,
        chain: ChainView,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> CombinedBlock:
        """What a block built on validation block ``head`` of ``chain`` takes of ``offered``.

        Each of ``offered`` is signed by its sender, its signature checked.
        One whose work is under ``bounds``', that moves nothing, of a
        generation after the block's, or whose key ``chain`` holds already,
        is left out, and of those sharing a key, all but the one of most
        bits, and of as many, the one whose hash sorts first (§4.4).

        Raises:
            MissingContentError: names the blocks ``chain`` lacks.
            ValueError: the payout takes more from an identity than it holds.
        """
        before = ChainBefore.read(chain, head)
        last = before.generation - 1
        payout = before.payout(chain.balances(before.stakers(), last))
        valid = cls._valid(offered, before.generation, chain, bounds)
        every = cls._holding(before, (payout, *valid))
        balances = chain.balances(every.named(), last)
        back = min(before.validated.reach, last)
        older = [chain.stated(earlier) for earlier in range(last - 1, last - back - 1, -1)]
        return cls(
            before,
            payout,
            cls._taken(valid, BalanceSheet.of(every, balances).checked),
            balances,
            (before.validated.balances, *older),
        )

    def block(self, count: int) -> TransactionBlock:
        """The block of the first ``count`` transactions taken, stating everything as it is (§5.3).

        It states the balances of everyone it names, and of those holding
        Kismet that shorten its reach (:meth:`_shortening`).
        """
        block = self._holding(self.before, (self.payout, *self.taken[:count]))
        balances = dict(BalanceSheet.of(block, self.balances).after)
        balances.update(self._shortening(balances, block.most_balances - len(balances)))
        return replace(block, balances=balances, reach=reach_of([balances, *self.stated]))

    @staticmethod
    def _holding(
        before: ChainBefore, entries: Iterable[SignedTransaction | Payout]
    ) -> TransactionBlock:
        """The block built on ``before`` holding ``entries``, stating no balances, and no reach."""
        return TransactionBlock(
            before.generation,
            before.validation_id,
            before.weight,
            0,
            tuple(sorted(entries, key=lambda entry: entry.hash)),
            {},
        )

    @staticmethod
    def _valid(
        offered: Iterable[SignedTransaction], generation: int, chain: ChainView, bounds: BlockBounds
    ) -> list[SignedTransaction]:
        """Those of ``offered`` that may be valid in block ``generation`` of ``chain``, one a key.

        Each must have the work of ``bounds``, move something, be of no later
        a generation, and have a key ``chain`` does not hold (§4.4). Of those
        sharing a key, the one of most bits is kept.
        """
        valid: dict[TransactionKey, SignedTransaction] = {}

        for signed in sorted(offered, key=_most_bits_first):
            transaction = signed.transaction

            if (
                signed.work_bits >= bounds.transaction_bits
                and transaction.sends() > Kismet(0)
                and transaction.generation <= generation
                and not chain.has_key(transaction.key, transaction.generation, generation - 1)
            ):
                valid.setdefault(transaction.key, signed)

        return list(valid.values())

    @staticmethod
    def _taken(
        valid: Iterable[SignedTransaction], checked: Mapping[ContentId, Balance]
    ) -> tuple[SignedTransaction, ...]:
        """``valid`` in the order a builder takes them, but those their senders cannot cover.

        Free ones are taken by most bits first, and paid ones by the largest
        fee, then most bits: a paid one each time more free ones than paid
        are taken before it, so that at least half of any first few are free
        (§5.1, §5.4). Each is taken only if its sender's balance in
        ``checked`` covers it, with all the sender's taken before (§4.4).
        """
        free = deque(
            sorted(
                (signed for signed in valid if signed.transaction.fee == Kismet(0)),
                key=_most_bits_first,
            )
        )
        paid = deque(
            sorted(
                (signed for signed in valid if signed.transaction.fee > Kismet(0)),
                key=lambda signed: (-signed.transaction.fee.count, -signed.work_bits, signed.hash),
            )
        )
        sending: dict[ContentId, int] = {}
        taken: list[SignedTransaction] = []
        free_taken = 0

        while queue := (paid if paid and 2 * free_taken > len(taken) else free):
            signed = queue.popleft()
            sender = signed.transaction.sender
            sent = sending.get(sender, 0) + signed.transaction.sends().count

            if sent > checked[sender].kismet.count:
                # Pending: its sender's balance does not cover it here (§4.4).
                continue

            sending[sender] = sent
            taken.append(signed)

            if queue is free:
                free_taken += 1

        return tuple(taken)

    def _shortening(
        self, stated: Mapping[ContentId, Balance], room: int
    ) -> dict[ContentId, Balance]:
        """The balances of those holding Kismet ``stated`` lacks that shorten the reach (§5.3).

        Those stated longest ago are added first, and all of those stated
        as long ago together, while ``room`` holds them: the reach is only
        shortened once every one of them is stated.
        """
        latest: dict[ContentId, tuple[int, Balance]] = {}

        for back, balances in enumerate(self.stated, start=1):
            for identity, balance in balances.items():
                latest.setdefault(identity, (back, balance))

        unstated: dict[int, dict[ContentId, Balance]] = {}

        for identity, (back, balance) in latest.items():
            if identity not in stated and balance.kismet > Kismet(0):
                unstated.setdefault(back, {})[identity] = balance

        added: dict[ContentId, Balance] = {}

        for back in sorted(unstated, reverse=True):
            if len(added) + len(unstated[back]) > room:
                break

            added.update(unstated[back])

        return added
