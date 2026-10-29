"""Checking a transaction block against its target and its chain (KarmaLedger §3.8, §4.4, §5.5).

A transaction block that is unsigned, or states something false by itself,
is found so as it is read (:meth:`~libranet.karma.blocks.BlockDrop.decoded`).
One whose ``previous`` names another validation block than the one its
target is named for, as one that is a validation block, is of another
target, where anyone may have placed it, and is passed over
(:class:`~libranet.karma.blocks.MisplacedBlockError`). What any other states
against its target and the chain its ``previous`` names is checked here: its
``generation``, against the validation block its target is named for; and
its ``weight``, payout, balances, and ``reach``, and each transaction's key,
against the chain before it. A block stating any of these falsely distrusts
its builder (§3.8).

A block stating nothing false may still be incorrect, and score zero
(§5.5): one not full, one breaking the half-free rule, or one including a
transaction before its generation, each of which an honest builder may
make. A correct block is scored by the bits its drop shares with its target
hash, then its ``reach``, then the average generation its transactions were
witnessed in. The root's transaction block is checked by its shape alone
(§7.2).

The chain is read through a :class:`ChainView`, so that a block is checked
against what a node holds, or against a chain held in memory. What the view
does not hold, it names, and the check waits for it rather than judging. A
key older than the history it holds is not known to it, so a duplicate
older than that is neither false nor incorrect (§3.8).
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from functools import total_ordering
from typing import Collection, Mapping, Protocol

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockDrop,
    BlockFinding,
    MisplacedBlockError,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.generations import GenerationTargets
from libranet.karma.ledger import BalanceSheet, Reward, reach_of
from libranet.karma.reading import HeldBlock
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import NO_BALANCE, Balance
from libranet.protocol.karma_transactions import Payout, SignedTransaction, TransactionKey


@dataclass(frozen=True)
class ChainBefore:
    """What transaction block g is built on: validation block g − 1, ``validation``, in ``chain``.

    Its id is ``validation_id``, and it validates transaction block g − 1,
    ``validated``, whose id is ``validated_id``. What a block built on it
    states of it is checked against it here, and a block of its own is
    built on it as truly (Phase 6 Step 131).
    """

    chain: ChainView
    validation_id: ContentId
    validation: BlockDrop[ValidationBlock]
    validated_id: ContentId
    validated: TransactionBlock

    @classmethod
    def read(cls, chain: ChainView, validation_id: ContentId) -> ChainBefore:
        """What ``chain`` holds of validation block ``validation_id``, and the block it validates.

        Raises:
            MissingContentError: ``chain`` lacks either.
        """
        validation = chain.validation(validation_id)
        # A validation block of a chain holds a stake, and all its stakes
        # name the block it validates (§6.1).
        validated_id = validation.block.stakes[0].stake.transaction_block
        validated = chain.transaction(validated_id).block
        return cls(chain, validation_id, validation, validated_id, validated)

    @property
    def generation(self) -> int:
        """The generation built on it: one more than its validation block's."""
        return self.validation.block.generation + 1

    @property
    def weight(self) -> ChainWeight:
        """The chain's weight through it, which a block built on it states (§5.1)."""
        return self.validated.weight_through(self.validated_id, self.validation_id)

    def stakers(self) -> set[ContentId]:
        """The stakers of its validation block, whom its reward pays (§6.6)."""
        return {stake.staker for stake in self.validation.block.stakes}

    def payout(self, balances: Mapping[ContentId, Balance]) -> Payout:
        """The payout of a block built on it, which pays its validation block's reward (§5.2).

        ``balances`` are each staker's, as the block it validates leaves them.

        Raises:
            MissingContentError: ``chain`` lacks the runner-up it names.
        """
        named = self.validation.block.runner_up
        runner_up = None if named is None else self.chain.validation(named).block
        reward = Reward(
            self.validation.block, self.validation.builder, self.validated, balances, runner_up
        )
        return reward.payout()

    def reach(self, block: TransactionBlock) -> int:
        """The ``reach`` of ``block``, built on it (§5.3).

        It is read from the balances ``block`` states, and those of the
        blocks before it, as far back as the reach of the one it is built on.

        Raises:
            MissingContentError: names a block in that reach, which ``chain``
                lacks.
        """
        last = self.generation - 1
        back = min(self.validated.reach, last)
        older = [
            self.chain.stated(generation) for generation in range(last - 1, last - back - 1, -1)
        ]
        return reach_of([block.balances, self.validated.balances, *older])

    def false_basis(self, block: TransactionBlock) -> str:
        """What ``block``, read at the target named for it, states falsely of it, or ``""``.

        ``block``, whose ``previous`` names its validation block, must be one
        generation after it, and its ``weight`` must be the chain's through
        it (§3.8, §5.1).
        """
        if block.generation != self.generation:
            return (
                f'A transaction block\'s "generation" must be {self.generation}, one more than '
                f"that of {self.validation_id}, its target's, got {block.generation}"
            )

        weight = self.weight

        if block.weight != weight:
            return (
                f'A transaction block\'s "weight" must be {weight.value()}, '
                f"got {block.weight.value()}"
            )

        return ""

    def false_payout(self, block: TransactionBlock, balances: Mapping[ContentId, Balance]) -> str:
        """What ``block``'s payout states falsely, or ``""``: it pays its validation block's reward.

        ``balances`` are each staker's, as the block it validates leaves them.
        Two payouts are alike when they move the same, though their text
        may differ (§5.2, §6.6).

        Raises:
            MissingContentError: ``chain`` lacks the runner-up it names.
        """
        payout = self.payout(balances)
        stated = block.payout

        if stated is None or (stated.sources, stated.to) != (payout.sources, payout.to):
            return (
                f"A transaction block's payout must pay the reward of {self.validation_id}, "
                f"as {payout.contents}"
            )

        return ""

    def false_reach(self, block: TransactionBlock) -> str:
        """What ``block`` states falsely of its ``reach``, as :meth:`reach` reads it, or ``""``.

        Raises:
            MissingContentError: names a block in that reach, which ``chain``
                lacks.
        """
        reach = self.reach(block)

        if block.reach != reach:
            return f'A transaction block\'s "reach" must be {reach}, got {block.reach}'

        return ""


class ChainView(Protocol):
    """What a check reads of a chain: the chain through one validation block, as a node holds it.

    Its generations are numbered from the root's, 0. A block it does not
    hold, it raises :class:`~libranet.bundle.errors.MissingContentError`
    for, naming what it lacks, so that it is fetched and the check made
    again. The keys and witnesses it holds may not reach back to the root
    (KarmaLedger §9): what is older is not known to it.
    """

    def validation(self, block_id: ContentId) -> BlockDrop[ValidationBlock]:
        """Validation block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held.
        """
        ...

    def transaction(self, block_id: ContentId) -> BlockDrop[TransactionBlock]:
        """Transaction block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held.
        """
        ...

    def stated(self, generation: int) -> Mapping[ContentId, Balance]:
        """The balances the chain's transaction block ``generation`` states.

        Raises:
            MissingContentError: names that block, which is not held.
        """
        ...

    def balances(
        self, identities: Collection[ContentId], generation: int
    ) -> Mapping[ContentId, Balance]:
        """Each of ``identities``' balance, as the chain's transaction block ``generation`` left it.

        That is the latest stated of it through that block, or
        :data:`~libranet.protocol.karma_balances.NO_BALANCE` for one stated nowhere
        (§5.3).

        Raises:
            MissingContentError: names the blocks within that block's reach
                that are not held.
        """
        ...

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        """Whether a transaction in the chain's blocks ``first`` to ``last`` has ``key``.

        Those are its transaction blocks, ``first`` and ``last`` among them,
        as far back as the history it holds (§4.4).
        """
        ...

    def witnessed(self, transaction_hash: str) -> int | None:
        """The generation of the chain's winning validation block listing ``transaction_hash``.

        It is ``None`` when none it holds lists it (§4.5, §6.4).
        """
        ...


class TransactionVerdict(Enum):
    """A transaction's verdict in a transaction block (§4.4).

    A pending transaction is no error: it is carried forward, and tried in
    each later block, until its sender's balance covers it and the chain
    reaches its generation. An invalid one is never included.
    """

    VALID = "valid"
    PENDING = "pending"
    INVALID = "invalid"

    @classmethod
    def of(
        cls, signed: SignedTransaction, sheet: BalanceSheet, chain: ChainView
    ) -> TransactionVerdict:
        """``signed``'s verdict in ``sheet``'s block, which is built on ``chain``.

        It is invalid when its key is a transaction's in one of ``chain``'s
        blocks from its ``generation`` through the one before ``sheet``'s,
        of those ``chain`` holds. Otherwise, it is pending when its
        ``generation`` is later than its block's, or its sender is one
        ``sheet`` finds overspent. What it states by itself, its signature,
        its work, that it moves something, and that it shares no key with
        another in its block, is found as its block is read
        (:meth:`~libranet.karma.blocks.BlockDrop.decoded`).
        """
        transaction = signed.transaction
        generation = sheet.block.generation

        if transaction.generation < generation and chain.has_key(
            transaction.key, transaction.generation, generation - 1
        ):
            return cls.INVALID

        if transaction.generation > generation or transaction.sender in sheet.overspent:
            return cls.PENDING

        return cls.VALID


@total_ordering
@dataclass(frozen=True)
class BlockScore:
    """What a correct transaction block scores when validating (§5.5).

    ``bits`` are those its drop shares with its target hash, ``reach`` its
    own, and ``age`` the average generation its transactions, the payout
    aside, were witnessed in (§4.5). One score is above another with more
    bits; of as many, with a shorter reach; and of as short, with a lower
    age. There are no weights.
    """

    bits: int
    reach: int
    age: Fraction

    @classmethod
    def of(cls, block: TransactionBlock, bits: int, chain: ChainView) -> BlockScore:
        """The score of ``block``, on ``chain``, its drop sharing ``bits`` with its target hash.

        Each transaction was witnessed in the generation of the winning
        validation block of ``chain`` that lists its hash, or in
        ``block``'s, if that is earlier. A block holding none but the
        payout is as young as it can be: its own generation.
        """
        generation = block.generation
        ages: list[int] = []

        for signed in block.signed:
            witnessed = chain.witnessed(signed.hash)
            ages.append(generation if witnessed is None else min(witnessed, generation))

        age = Fraction(sum(ages), len(ages)) if ages else Fraction(generation)
        return cls(bits, block.reach, age)

    def __lt__(self, other: BlockScore) -> bool:
        return self._rank() < other._rank()

    def _rank(self) -> tuple[int, int, Fraction]:
        """This score as what ranks it, each the greater for the better."""
        return (self.bits, -self.reach, -self.age)


@dataclass(frozen=True)
class TransactionBlockCheck:
    """Transaction block ``drop``, checked and found to state nothing false: correct, or not.

    A correct block is ranked by its ``score``. An incorrect one scores zero,
    below every correct one, and is never validated: its ``score`` is
    ``None``, and ``incorrect`` says why (§5.5). ``full`` is ``False`` when
    it is incorrect for not being full, which its builder's copy, compressed
    otherwise, may be (§3.8, §5.4). :meth:`of` checks a block.

    Raises:
        ValueError: ``score`` and ``incorrect`` are both given, or neither.
    """

    drop: BlockDrop[TransactionBlock]
    score: BlockScore | None
    incorrect: str = ""
    full: bool = True

    def __post_init__(self) -> None:
        if (self.score is None) == (not self.incorrect):
            raise ValueError(
                "A checked block is scored when it is correct, and says why when it is not"
            )

    @classmethod
    def of(
        cls,
        block_id: ContentId,
        data: bytes,
        named_for: ContentId | None,
        chain: ChainView,
        keys: Mapping[ContentId, bytes],
        *,
        compressed_bytes: int,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> TransactionBlockCheck | BlockFinding:
        """Transaction block ``block_id``, the drop ``data``, checked; or what it states falsely.

        It was read at the transaction target named for validation block
        ``named_for``, or at the root's, ``karma transaction:0``, when that
        is ``None``, where it is checked by its shape (§7.2). ``chain`` is
        the chain through ``named_for``. Signatures are checked with
        ``keys``, and the work of each transaction, and whether the block is
        full, the copy held being ``compressed_bytes`` compressed, against
        ``bounds``.

        Returns:
            A :class:`BlockFinding` naming no builder if it is unsigned, and
            its builder if it states something false; or else the block,
            correct and scored, or incorrect.

        Raises:
            MisplacedBlockError: it is a validation block, or built on
                another validation block than ``named_for``, and so of
                another target, where it is passed over.
            MissingContentError: names the keys ``keys`` lacks, or the blocks
                ``chain`` lacks, which the check waits for.
            UnsupportedBundleError: it, or what it holds, is signed in a way
                this node cannot check.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
        """
        found = BlockDrop.decoded(data, TransactionBlock, keys, bounds=bounds)

        if isinstance(found, BlockFinding):
            return found

        if found.block.previous != named_for:
            raise MisplacedBlockError(
                f"It is built on {found.block.previous}, where a block at its target is built "
                f"on {named_for}"
            )

        target = GenerationTargets.built_on(named_for).transaction
        bits = matching_bits(target.hex, block_id.hash)

        if named_for is None:
            return cls._root(found, bits, chain)

        before = ChainBefore.read(chain, named_for)
        return cls._on_chain(found, bits, before, compressed_bytes=compressed_bytes, bounds=bounds)

    @classmethod
    def of_held(
        cls,
        held: HeldBlock,
        named_for: ContentId | None,
        chain: ChainView,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> TransactionBlockCheck | BlockFinding:
        """Transaction block ``held``, as the node holds it, checked, as :meth:`of` checks it.

        Raises:
            MisplacedBlockError: as :meth:`of`.
            MissingContentError: as :meth:`of`.
            UnsupportedBundleError: as :meth:`of`.
            UnknownAlgorithmError: as :meth:`of`.
        """
        return cls.of(
            held.block_id,
            held.data,
            named_for,
            chain,
            held.keys,
            compressed_bytes=held.compressed_bytes,
            bounds=bounds,
        )

    @property
    def is_correct(self) -> bool:
        """Whether it is correct, and so may be validated (§5.5)."""
        return self.score is not None

    @classmethod
    def _root(
        cls, drop: BlockDrop[TransactionBlock], bits: int, chain: ChainView
    ) -> TransactionBlockCheck | BlockFinding:
        """``drop``, at the root's target, checked by its shape (§7.1, §7.2).

        Its builder is the root's creator, who signs its one transaction,
        sending themselves 0, and whom it states holding 0, having won
        nothing. It need not be full.
        """
        block = drop.block
        creator = drop.builder
        signed = block.signed
        shaped = (
            block.generation == 0
            and block.previous is None
            and block.weight == ChainWeight(0)
            and block.reach == 0
            and len(signed) == 1
            and signed[0].transaction.is_root
            and signed[0].transaction.sender == creator
            and dict(block.balances) == {creator: NO_BALANCE}
        )

        if not shaped:
            return BlockFinding(
                "A transaction block at the root's target must be generation 0, built on none, "
                "of weight 0 and reach 0, holding only its builder's transaction sending "
                "themselves 0, and stating its builder holding 0",
                creator,
            )

        return cls(drop, BlockScore.of(block, bits, chain))

    @classmethod
    def _on_chain(
        cls,
        drop: BlockDrop[TransactionBlock],
        bits: int,
        before: ChainBefore,
        *,
        compressed_bytes: int,
        bounds: BlockBounds,
    ) -> TransactionBlockCheck | BlockFinding:
        """``drop``, built on ``before``, checked against it (§3.8, §5.5).

        Raises:
            MissingContentError: names the blocks ``before``'s chain lacks.
        """
        block = drop.block
        statement = before.false_basis(block)

        if statement:
            return BlockFinding(statement, drop.builder)

        named = block.named() | set(block.balances) | before.stakers()
        balances = before.chain.balances(named, before.generation - 1)
        statement = before.false_payout(block, balances)

        if statement:
            return BlockFinding(statement, drop.builder)

        sheet = BalanceSheet.of(block, balances)
        verdicts = {
            signed.hash: TransactionVerdict.of(signed, sheet, before.chain)
            for signed in block.signed
        }
        statement = cls._false_statement(sheet, verdicts) or before.false_reach(block)

        if statement:
            return BlockFinding(statement, drop.builder)

        incorrect = cls._incorrect(sheet, verdicts, compressed_bytes, bounds)

        if incorrect:
            return cls(drop, None, incorrect, bounds.is_full(compressed_bytes))

        return cls(drop, BlockScore.of(block, bits, before.chain))

    @staticmethod
    def _false_statement(sheet: BalanceSheet, verdicts: Mapping[str, TransactionVerdict]) -> str:
        """What ``sheet``'s block states falsely of its transactions and balances, or ``""``.

        A sender may not overspend, and no transaction may be invalid (§4.4).
        Every sender and recipient must be stated, and every balance stated
        must be right (§5.3).
        """
        overspent = sorted(sheet.overspent)

        if overspent:
            return f"{overspent[0]} sends more in a transaction block than its balance covers"

        invalid = sorted(
            transaction_hash
            for transaction_hash, verdict in verdicts.items()
            if verdict is TransactionVerdict.INVALID
        )

        if invalid:
            return f"Transaction {invalid[0]} has the key of one already in the chain"

        unstated = sheet.unstated()

        if unstated:
            return f"A transaction block must state the balance of {unstated[0]}, which it names"

        misstated = sheet.misstated()

        if misstated:
            identity = misstated[0]
            return (
                f"A transaction block must state the balance of {identity} as "
                f"{sheet.balance(identity).value()}, got {sheet.block.balances[identity].value()}"
            )

        return ""

    @staticmethod
    def _incorrect(
        sheet: BalanceSheet,
        verdicts: Mapping[str, TransactionVerdict],
        compressed_bytes: int,
        bounds: BlockBounds,
    ) -> str:
        """Why ``sheet``'s block is incorrect, though it states nothing false, or ``""`` (§5.5)."""
        block = sheet.block

        if not bounds.is_full(compressed_bytes):
            return (
                f"A transaction block must be full, more than "
                f"{bounds.most_bytes - bounds.room_bytes} bytes compressed and at most "
                f"{bounds.most_bytes}, got {compressed_bytes}"
            )

        if not block.is_half_free:
            return "At least half a transaction block's transactions but the payout must be free"

        # No sender overspends by now, so each pending transaction is of a
        # generation after its block's.
        pending = sorted(
            transaction_hash
            for transaction_hash, verdict in verdicts.items()
            if verdict is TransactionVerdict.PENDING
        )

        if pending:
            return f"Transaction {pending[0]} is included before the generation it names"

        # Not false, as the balance is right, but it only takes room (§5.3).
        idle = sorted(
            identity
            for identity, balance in block.balances.items()
            if balance.kismet == Kismet(0) and identity not in block.named()
        )

        if idle:
            return (
                f"A transaction block states a balance of 0 for {idle[0]}, which it does not name"
            )

        return ""
