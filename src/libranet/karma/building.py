"""Combining transaction blocks (KarmaLedger §3.4, §5.3, §5.4, §5.6, Phase 6 Step 134).

Builders combine the blocks put up at a generation's targets into larger
ones, until one is full. Every ``karma.build_interval_seconds``, the Karma
module reads every transaction block at its head's transaction target, and
at the transaction targets of the head's rivals, the other validation blocks
at the target the head was chosen from, whose blocks are false starts
(§3.4), and builds one block of what they hold, and of what it carries
(:mod:`libranet.karma.carried`).

Each transaction is taken on its own, whoever built the block it is read
from, a distrusted identity among them (§3.8, §4.4): one whose signature
checks, with its sender's key held, whose work meets the bounds, and that
moves something. One of a generation after the block's, or whose key the
chain holds, is left out (§4.4), and of those sharing a key, all but the
one of most bits. Free ones are taken by most bits first, and paid ones by
the largest fees, a paid one only while as many free ones are taken, so
that at least half of them are free (§5.1, §5.4), and each only while its
sender's balance covers it, and all of the sender's taken before it. As
many are taken as leave the block's drop, compressed as the node stores it,
with room for its nonce, no larger than a full block may be.

The block states its payout, the balances of everyone it names, its
``reach``, and its ``weight``, each as it is. It states the balances of
others holding Kismet too, those stated longest ago first, as many as
shorten its reach within the cap of twice those it names (§5.3). It is
signed by the node, its drop's nonce searched for for
``karma.block_seconds``, and after that until it shares
``storage.search_min_bits`` with its target (§2), and it is stored and
announced, as a block put up for a transaction alone is.

Nothing is built while one block held at the head's transaction target,
correct but perhaps for not being full, holds every transaction that would
be taken: the same blocks build the same block, so each new transaction
builds one more. With no transaction to take, and no such block held, a
block holding only the payout is built, so that the balances the head's
reward leaves are stated at the target, where those it pays may spend it
(§7.4).

A block held at the head's transaction target whose transactions are all in
another held there of the same generation, correct but perhaps for not
being full, is superseded by it, so that the node may shun it (§3.7, Phase 6
Step 135): if the other holds more of them, or as many and shares more bits
with the target. One that a validation block held at the head's validation
target names is never superseded, nor one full, as the copy held measures it
(§5.4).
"""

from __future__ import annotations
from collections import deque
from dataclasses import dataclass, replace
from logging import getLogger
from typing import Iterable, Mapping, Sequence
from zlib import compress

from libranet.bundle.errors import BundleError, BundleSignatureError, MissingContentError
from libranet.bundle.storing import HASH_ALGORITHM, STORAGE_COMPRESSION_LEVEL
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockDrop,
    TransactionBlock,
    ValidationBlock,
    unchecked_level,
)
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import ChainBefore, ChainView, TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.ledger import BalanceSheet, reach_of
from libranet.karma.placing import BlockPlacing
from libranet.karma.reading import HeldBlock, HeldChain, TargetReader
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance
from libranet.protocol.karma_transactions import Payout, SignedTransaction, TransactionKey

_LOGGER = getLogger(__name__)


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


@dataclass(frozen=True)
class FilledBlock:
    """``drop``, a combined block as its builder signed it, and the bytes it is measured at.

    ``measured_bytes`` is its drop's content compressed as the node stores
    it, with room for the nonce searched for after (§5.4). :meth:`of` fills
    one.
    """

    drop: BlockDrop[TransactionBlock]
    measured_bytes: int

    @classmethod
    def of(
        cls,
        combined: CombinedBlock,
        identity: NodeIdentity,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> FilledBlock | None:
        """The block of the most transactions ``combined`` takes, in order, that ``bounds`` hold.

        It is signed by ``identity``, and measured as :attr:`measured_bytes`
        says (:meth:`~libranet.karma.blocks.BlockBounds.fullest`).

        Returns:
            The block, holding only the payout if ``combined`` takes none,
            or ``None`` if not one it takes fits.
        """
        fullest = bounds.fullest(
            lambda count: cls._signed(combined.block(count), identity),
            len(combined.taken),
            fewest=min(len(combined.taken), 1),
        )
        return None if fullest is None else cls(*fullest)

    @staticmethod
    def _signed(block: TransactionBlock, identity: NodeIdentity) -> BlockDrop[TransactionBlock]:
        """``block``, signed by ``identity``."""
        return BlockDrop(identity.signed(block.text()), block)


class BlockBuilder:
    """Combines the blocks at the head's targets, and what is carried, into one (§5.6).

    The chain is the one ``history`` keeps, its blocks read from what
    ``reader`` holds, and what is carried is in ``carried``. A block is
    signed, placed, and stored as ``placing`` says. It is filled within
    ``bounds``: the specification's, unless a test gives others. It also
    finds the blocks held at the head's transaction target that another held
    there supersedes, for the node to shun (§3.7).
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        carried: CarriedTransactions,
        placing: BlockPlacing,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._history = history
        self._reader = reader
        self._carried = carried
        self._placing = placing
        self._bounds = bounds
        self._chain = HeldChain(history, reader)
        # The head these were found at: the blocks and transactions passed
        # over, each logged once, whether each block held at the head's
        # transaction target is correct, but perhaps for being full, and the
        # blocks found superseded there, each named once.
        self._head: ContentId | None = None
        self._passed_over: set[ContentId | str] = set()
        self._correct: dict[ContentId, bool] = {}
        self._superseded: set[ContentId] = set()

    def build(self) -> None:
        """Build a block at the head's transaction target, unless one held there holds it all.

        It is built of the transactions in the blocks held at that target,
        and at the targets of the head's false starts (§3.4), and those
        carried, or of the payout alone, if there are none, and no correct
        block is held there. A block of the chain it lacks is asked for, and
        it is built as this is next called; one that cannot be built for an
        error of the system, or because what it is built of cannot be read,
        is logged.
        """
        head = self._history.head

        if head is None:
            return

        at_target = self._at_target(head)
        offered = [
            signed
            for _, drop in [*at_target, *self._parsed(self._false_starts(head))]
            for signed in drop.block.signed
        ]
        offered.extend(each.signed for each in self._carried.readable_at(self._history))

        try:
            combined = CombinedBlock.of(
                self._verified(offered), head.validation, self._chain, bounds=self._bounds
            )

            if self._held(combined.taken, at_target):
                return

            filled = FilledBlock.of(combined, self._placing.identity(), bounds=self._bounds)

            if filled is None:
                _LOGGER.warning(
                    "Building no Karma transaction block at head %s: not one transaction fits "
                    "in %d bytes",
                    head.validation,
                    self._bounds.most_bytes,
                )
                return

            if not self._held(filled.drop.block.signed, at_target):
                self._place(filled, head.validation, combined.before.holders())

        except MissingContentError:
            # Not logged: what it lacks is asked for, and it is built once
            # that comes.
            return

        except OSError as error:
            _LOGGER.error("Cannot build a Karma transaction block: %s", error)

        except (BundleError, ValueError) as error:
            _LOGGER.warning("Cannot build a Karma transaction block: %s", error)

    def superseded(self) -> dict[ContentId, ContentId]:
        """The blocks held at the head's transaction target that another held there supersedes.

        Each is a transaction block built on the head, whose transactions
        are all in another built on it, correct but perhaps for being full,
        holding more of them, or as many and sharing more bits with the
        target (§3.7). One that a validation block held at the head's
        validation target names is not, nor one full, as the copy held
        measures it. Each is named once at a head. None is while how many
        hold Kismet, which sets how large a full one is, cannot be read: a
        block of the chain lacked is asked for, and one that cannot be read
        is logged.

        Returns:
            Each block superseded, and the block of most transactions, and of
            those, most bits, that supersedes it.
        """
        head = self._history.head

        if head is None:
            return {}

        try:
            holders = ChainBefore.read(self._chain, head.validation).holders()

        except MissingContentError:
            # Not logged: what it lacks is asked for, and it is read once
            # that comes.
            return {}

        except (BundleError, ValueError) as error:
            _LOGGER.warning("Cannot read how many hold Karma at the head: %s", error)
            return {}

        targets = GenerationTargets.after(head.validation)
        # Each block built on the head, those of most transactions, and of
        # as many, most bits, first.
        ranked = sorted(
            (
                (
                    (
                        len(drop.block.signed),
                        matching_bits(targets.transaction.hex, held.block_id.hash),
                    ),
                    held,
                    frozenset(signed.hash for signed in drop.block.signed),
                )
                for held, drop in self._at_target(head)
                if drop.block.previous == head.validation
            ),
            key=lambda each: each[0],
            reverse=True,
        )
        named = self._named(targets.validation)
        superseded: dict[ContentId, ContentId] = {}

        for rank, held, hashes in ranked:
            if (
                held.block_id in self._superseded
                or held.block_id in named
                or self._bounds.is_full(held.compressed_bytes, holders)
            ):
                continue

            keeper = next(
                (
                    other.block_id
                    for other_rank, other, kept in ranked
                    if other_rank > rank and hashes <= kept and self._is_correct(other)
                ),
                None,
            )

            if keeper is not None:
                self._superseded.add(held.block_id)
                superseded[held.block_id] = keeper

        return superseded

    def _at_target(
        self, head: ChainGeneration
    ) -> list[tuple[HeldBlock, BlockDrop[TransactionBlock]]]:
        """The transaction blocks held at ``head``'s transaction target, read (:meth:`_parsed`).

        What was found at another head is forgotten.
        """
        if head.validation != self._head:
            self._head = head.validation
            self._passed_over.clear()
            self._correct.clear()
            self._superseded.clear()

        target = GenerationTargets.after(head.validation).transaction
        return self._parsed(self._reader.read(target, TransactionBlock))

    def _named(self, target: DropTarget) -> set[ContentId]:
        """The transaction blocks the validation blocks held at ``target`` stake on, unchecked.

        One that is no validation block names none.
        """
        named: set[ContentId] = set()

        for held in self._reader.read(target, ValidationBlock):
            try:
                drop = BlockDrop.parsed(held.data, ValidationBlock)

            except (BundleError, ValueError) as error:
                # Not logged as a warning: the chain followed checks each block
                # at the target.
                _LOGGER.debug("Karma block %s names no transaction block: %s", held.block_id, error)
                continue

            named.update(signed.stake.transaction_block for signed in drop.block.stakes)

        return named

    def _false_starts(self, head: ChainGeneration) -> list[HeldBlock]:
        """The transaction blocks held at the transaction targets of ``head``'s rivals (§3.4).

        Its rivals are the other validation blocks at the target it was
        chosen from.
        """
        named_for = (
            None
            if head.generation == 0
            else self._history.generation(head.generation - 1).validation
        )
        target = GenerationTargets.built_on(named_for).validation
        rivals = self._reader.read(target, ValidationBlock)
        return [
            held
            for rival in rivals
            if rival.block_id != head.validation
            for held in self._reader.read(
                GenerationTargets.after(rival.block_id).transaction, TransactionBlock
            )
        ]

    def _parsed(
        self, held: Iterable[HeldBlock]
    ) -> list[tuple[HeldBlock, BlockDrop[TransactionBlock]]]:
        """Each of ``held`` that is a transaction block, read, its signatures not checked.

        Its transactions are each checked on their own. Each other is
        logged, once at a head, and passed over.
        """
        parsed: list[tuple[HeldBlock, BlockDrop[TransactionBlock]]] = []

        for each in held:
            try:
                parsed.append((each, BlockDrop.parsed(each.data, TransactionBlock)))

            except (BundleError, ValueError) as error:
                if each.block_id not in self._passed_over:
                    self._passed_over.add(each.block_id)
                    _LOGGER.log(
                        unchecked_level(error),
                        "Taking no Karma transactions from %s, which is no transaction block: %s",
                        each.block_id,
                        error,
                    )

        return parsed

    def _verified(self, offered: Iterable[SignedTransaction]) -> list[SignedTransaction]:
        """Each of ``offered`` whose signature checks with its sender's key, once each (§4.4).

        One whose sender's key is not held is passed over, and the key asked
        for. One whose signature fails is passed over, and logged once at a
        head.
        """
        keys: dict[ContentId, bytes | None] = {}
        verified: dict[str, SignedTransaction] = {}

        for signed in offered:
            sender = signed.transaction.sender

            if signed.hash in verified or signed.hash in self._passed_over:
                continue

            if sender not in keys:
                keys[sender] = self._key(sender)

            key = keys[sender]

            if key is None:
                continue

            try:
                signed.verified(key)

            except BundleSignatureError as error:
                self._passed_over.add(signed.hash)
                _LOGGER.debug(
                    "Passing over Karma transaction %s, whose signature must check: %s",
                    signed.hash,
                    error,
                )
                continue

            verified[signed.hash] = signed

        return list(verified.values())

    def _key(self, sender: ContentId) -> bytes | None:
        """The key of ``sender``, if it is held; it is asked for if it is not."""
        try:
            return self._reader.held(sender)

        except MissingContentError:
            # Not logged: it is asked for, and the sender's transactions are
            # taken once it comes.
            return None

        except BundleError as error:
            _LOGGER.warning(
                "Passing over the Karma transactions of %s, whose key cannot be read: %s",
                sender,
                error,
            )
            return None

    def _held(
        self,
        taken: Sequence[SignedTransaction],
        at_target: Sequence[tuple[HeldBlock, BlockDrop[TransactionBlock]]],
    ) -> bool:
        """Whether a block ``at_target``, correct but perhaps for being full, holds ``taken``."""
        hashes = {signed.hash for signed in taken}
        return any(
            hashes <= {signed.hash for signed in drop.block.signed} and self._is_correct(held)
            for held, drop in at_target
        )

    def _is_correct(self, held: HeldBlock) -> bool:
        """Whether ``held``, at the head's transaction target, is correct, but perhaps for fullness.

        One whose check lacks what is not held is not, until it comes.
        """
        if held.block_id in self._correct:
            return self._correct[held.block_id]

        try:
            check = TransactionBlockCheck.of_held(
                held,
                self._head,
                self._chain,
                bounds=replace(self._bounds, room_bytes=self._bounds.most_bytes),
            )

        except MissingContentError:
            # Not logged: what it lacks is asked for, and it is checked again
            # once that comes.
            return False

        except (BundleError, ValueError) as error:
            # Not logged as a warning: the chain followed checks each block at
            # the target.
            _LOGGER.debug("Karma block %s cannot be checked: %s", held.block_id, error)
            check = None

        self._correct[held.block_id] = isinstance(check, TransactionBlockCheck) and check.is_correct
        return self._correct[held.block_id]

    def _place(self, filled: FilledBlock, head: ContentId, holders: int) -> None:
        """Place ``filled`` at the transaction target named for ``head``, and store it.

        It is logged as full, or not, as ``holders`` holding Kismet in the
        balances it is checked against have it (§5.4).

        Raises:
            BundleError: it is larger than an object may be, even compressed.
            ValueError: its nonce cannot be searched for as asked.
            OSError: a searching process could not be started, or exited
                during the search, or the drop could not be written.
        """
        drop = self._placing.placed(filled.drop, GenerationTargets.after(head).transaction)
        compressed_bytes = len(compress(drop.data, STORAGE_COMPRESSION_LEVEL))
        _LOGGER.info(
            "Built Karma transaction block %s of %d transactions, %d bytes compressed, %s, "
            "sharing %d bits with the transaction target of %s",
            ContentId.for_data(drop.data, HASH_ALGORITHM),
            len(filled.drop.block.signed),
            compressed_bytes,
            "full" if self._bounds.is_full(compressed_bytes, holders) else "not full",
            drop.matching_bits,
            head,
        )


def _most_bits_first(signed: SignedTransaction) -> tuple[int, str]:
    """What orders transactions by most work first, and of as much, by hash (§4.3, §5.4)."""
    return (-signed.work_bits, signed.hash)
