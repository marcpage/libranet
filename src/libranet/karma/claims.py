"""The chain a head claims, as it is weighed (KarmaLedger §3.5.3, Phase 6 Step 129).

A head the node does not follow claims a chain: its validation block, the
transaction block that validates, whose ``weight`` the chain before it, and
so back through each block's ``previous``. A :class:`HeadClaim` is that
chain as far as it has been read. It is walked back
(:meth:`HeadClaim.walk`), from each validation block to the transaction
block its stakes name, and from that block's ``previous`` to the validation
block before, to the first the history of the chain followed holds, the
common ancestor, or to a root. Each transaction block's ``generation`` and
``weight``, and each validation block's ``generation``, are checked against
the validation block before, as the blocks are read, their signatures not
yet checked, so that a false one is found where it is first stated, and
nothing before it is read (:class:`FalseStatement`).

It is then checked forward from the ancestor (:meth:`HeadClaim.check`): each
transaction block, and the validation block validating it, as Phase 6 Steps
121 and 122 check a block at its target, against the chain followed through
the ancestor and the claim's generations after it, as each is checked
(:class:`ClaimedChain`). A transaction block is at the target its own
``previous`` places it at (§3.8), so the transaction blocks a runner-up's
stakes name are taken to be at its target when they are built on the same
validation block.

What a claim lacks, a block or a key, is named, as a check names it, and
the claim goes on from where it stopped once that comes.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from libranet.cas.content_id import ContentId
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.validating import ValidationBlockCheck
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_transactions import TransactionKey


@dataclass(frozen=True)
class FalseStatement:
    """Block ``block_id``, which a walk back finds states ``statement`` falsely, by its own word.

    Its signature is not yet checked. One ``invalid``, stating its
    ``generation`` or ``weight`` falsely, is no block of the ledger, which a
    node shuns once it is found signed (§3.7).
    """

    block_id: ContentId
    statement: str
    invalid: bool = True


@dataclass(frozen=True)
class ClaimedGeneration:
    """A generation of a chain claimed, as the walk back reads it, its blocks not yet checked.

    Validation block ``validation``, of generation ``validation_generation``,
    stakes on transaction block ``transaction``, of generation
    ``generation``, built on validation block ``previous``, and stating that
    the chain before it weighs ``weight``. The chain through the two weighs
    ``through``, as they claim.
    """

    validation: ContentId
    validation_generation: int
    transaction: ContentId
    generation: int
    previous: ContentId | None
    weight: ChainWeight
    through: ChainWeight

    @classmethod
    def read(cls, chain: HeldChain, validation_id: ContentId) -> ClaimedGeneration | FalseStatement:
        """Validation block ``validation_id``, and the block it stakes on, as ``chain`` holds them.

        Returns:
            The generation they are, or, for one holding no stake, what it
            states falsely.

        Raises:
            MissingContentError: names either, which is not held, and is
                asked for.
            BundleError: either is shunned, or what is held of it is not it,
                or holds no signed bundle.
            ValueError: what either signs is no block of its kind.
        """
        validation = chain.validation(validation_id).block

        if not validation.stakes:
            return FalseStatement(
                validation_id, "A validation block must hold a stake", invalid=False
            )

        transaction_id = validation.stakes[0].stake.transaction_block
        transaction = chain.transaction(transaction_id).block
        return cls(
            validation_id,
            validation.generation,
            transaction_id,
            transaction.generation,
            transaction.previous,
            transaction.weight,
            transaction.weight_through(transaction_id, validation_id),
        )

    def false_on(self, before: int, weight: ChainWeight) -> FalseStatement | None:
        """What it states falsely, built on a validation block of generation ``before``, or None.

        The chain through that block weighs ``weight``. For a root, built on
        none, ``before`` is -1, and ``weight`` 0 (§7.2, §7.3).
        """
        generation = before + 1
        named = "the root's" if self.previous is None else f"one more than that of {self.previous}"

        if self.generation != generation:
            return FalseStatement(
                self.transaction,
                f'A transaction block\'s "generation" must be {generation}, {named}, got '
                f"{self.generation}",
            )

        if self.validation_generation != generation:
            return FalseStatement(
                self.validation,
                f'A validation block\'s "generation" must be {generation}, {named}, got '
                f"{self.validation_generation}",
            )

        if self.weight != weight:
            return FalseStatement(
                self.transaction,
                f'A transaction block\'s "weight" must be {weight.value()}, '
                f"got {self.weight.value()}",
            )

        return None


class ClaimedChain:
    """The chain a claim is checked forward against, as :class:`HeldChain` reads it.

    That is ``history``, the chain followed, through generation
    ``ancestor``, the common ancestor, or -1 for a claim on another root, and
    the claim's generations after it, as each is checked (:meth:`check`).
    """

    def __init__(self, history: ChainHistory, ancestor: int) -> None:
        self.ancestor = ancestor
        self.generations: list[ChainGeneration] = []
        self._history = history
        self._keys: dict[TransactionKey, int] = {}
        self._witnessed: dict[str, int] = {}

    def generation(self, generation: int) -> ChainGeneration:
        """Generation ``generation``.

        Raises:
            IndexError: it holds no such generation.
        """
        if generation <= self.ancestor:
            return self._history.generation(generation)

        if generation - self.ancestor > len(self.generations):
            raise IndexError(f"The chain checked holds no generation {generation} yet")

        return self.generations[generation - self.ancestor - 1]

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        """Whether a transaction in its transaction blocks ``first`` to ``last`` has ``key``."""
        if self._history.has_key(key, first, min(last, self.ancestor)):
            return True

        generation = self._keys.get(key)
        return generation is not None and first <= generation <= last

    def witnessed(self, transaction_hash: str) -> int | None:
        """The generation whose validation block lists ``transaction_hash``, or ``None``."""
        generation = self._history.witnessed(transaction_hash)

        if generation is not None and generation <= self.ancestor:
            return generation

        return self._witnessed.get(transaction_hash)

    def check(
        self, claimed: ClaimedGeneration, reader: TargetReader, *, bounds: BlockBounds
    ) -> tuple[ContentId, TransactionBlockCheck | ValidationBlockCheck | BlockFinding] | None:
        """Check ``claimed``, the generation after the last this holds, and hold it if correct.

        Its blocks are read from what ``reader`` holds, and checked against
        ``bounds``.

        Returns:
            ``None`` if both its blocks are correct; or else the first that
            is not, and what was found of it.

        Raises:
            MissingContentError: names what a check lacks, which is asked for.
            BundleError: a block is shunned, or cannot be checked here.
            ValueError: a block names an id under a hash algorithm this node
                lacks, or a block that is no block of its kind.
        """
        chain = HeldChain(self, reader)
        transaction = self._transaction(
            claimed.transaction, claimed.previous, chain, reader, bounds
        )

        if not isinstance(transaction, TransactionBlockCheck) or not transaction.is_correct:
            return claimed.transaction, transaction

        validation = self._validation(claimed, chain, reader, transaction, bounds)

        if not isinstance(validation, ValidationBlockCheck) or not validation.is_correct:
            return claimed.validation, validation

        self._add(
            ChainGeneration.of(
                claimed.validation,
                validation.drop.block,
                claimed.transaction,
                transaction.drop.block,
            )
        )
        return None

    def _add(self, generation: ChainGeneration) -> None:
        """Hold ``generation``, checked, after the last."""
        self.generations.append(generation)

        for key in generation.keys:
            self._keys.setdefault(key, generation.generation)

        for witness in generation.witnessed:
            self._witnessed.setdefault(witness, generation.generation)

    @staticmethod
    def _transaction(
        block_id: ContentId,
        named_for: ContentId | None,
        chain: HeldChain,
        reader: TargetReader,
        bounds: BlockBounds,
    ) -> TransactionBlockCheck | BlockFinding:
        """Transaction block ``block_id``, built on ``named_for``, checked against ``chain``.

        Raises:
            MissingContentError: names what its check lacks.
            BundleError: it cannot be checked here.
            ValueError: it names an id under a hash algorithm this node lacks.
        """
        held = reader.block(block_id, TransactionBlock)
        return TransactionBlockCheck.of_held(held, named_for, chain, bounds=bounds)

    def _validation(
        self,
        claimed: ClaimedGeneration,
        chain: HeldChain,
        reader: TargetReader,
        validated: TransactionBlockCheck,
        bounds: BlockBounds,
    ) -> ValidationBlockCheck | BlockFinding:
        """``claimed``'s validation block checked against ``chain``, validating ``validated``.

        Its runner-up's stakes may name other transaction blocks at its
        target: those whose own ``previous`` places them there (§3.8).

        Raises:
            MissingContentError: names what its check lacks.
            BundleError: it, or a block it names, cannot be checked here.
            ValueError: it names an id under a hash algorithm this node
                lacks, or a block that is no block of its kind.
        """
        held = reader.block(claimed.validation, ValidationBlock)
        at_target: dict[ContentId, TransactionBlockCheck | BlockFinding] = {
            claimed.transaction: validated
        }
        runner_up = chain.validation(claimed.validation).block.runner_up
        rivals = [] if runner_up is None else chain.validation(runner_up).block.stakes

        for stake in rivals:
            block_id = stake.stake.transaction_block

            if (
                block_id not in at_target
                and chain.transaction(block_id).block.previous == claimed.previous
            ):
                at_target[block_id] = self._transaction(
                    block_id, claimed.previous, chain, reader, bounds
                )

        return ValidationBlockCheck.of_held(
            held, claimed.previous, chain, transactions=at_target, bounds=bounds
        )


@dataclass
class HeadClaim:
    """Head ``head``, a validation block not followed, as far as the chain it claims is read.

    ``walked`` are the generations read, from the head back, and ``chain``
    the chain it is checked forward against, once the walk has come to the
    common ancestor. ``lacking`` is what it last waited for.
    """

    head: ContentId
    walked: list[ClaimedGeneration] = field(default_factory=list)
    chain: ClaimedChain | None = None
    lacking: frozenset[ContentId] = frozenset()

    def claimed(self, chain: HeldChain) -> ClaimedGeneration | FalseStatement:
        """The head's generation, as ``chain`` holds it, read once.

        Raises:
            MissingContentError: names a block it lacks, which is asked for.
            BundleError: a block is shunned, or what is held of it is not it.
            ValueError: what a block signs is no block of its kind.
        """
        if not self.walked:
            read = ClaimedGeneration.read(chain, self.head)

            if isinstance(read, FalseStatement):
                return read

            self.walked.append(read)

        return self.walked[0]

    def walk(self, history: ChainHistory, chain: HeldChain) -> ClaimedChain | FalseStatement:
        """Walk back to the common ancestor with ``history``, or to a root, from the head read.

        Each block is read from ``chain``, and each generation read is held,
        so that a walk waiting for a block goes on from it.

        Returns:
            The chain to check it forward against, once the walk comes to
            the ancestor; or what a block walked states falsely.

        Raises:
            MissingContentError: names a block it lacks, which is asked for.
            BundleError: a block is shunned, or what is held of it is not it.
            ValueError: what a block signs is no block of its kind.
        """
        while self.chain is None:
            last = self.walked[-1]
            ancestor = last.generation - 1

            if last.previous is None:
                ancestor, weight = -1, ChainWeight(0)

            elif (
                0 <= ancestor < len(history)
                and history.generation(ancestor).validation == last.previous
            ):
                weight = history.generation(ancestor).weight

            else:
                before = ClaimedGeneration.read(chain, last.previous)

                if isinstance(before, FalseStatement):
                    return before

                false = last.false_on(before.validation_generation, before.through)

                if false is not None:
                    return false

                self.walked.append(before)
                continue

            false = last.false_on(ancestor, weight)

            if false is not None:
                return false

            self.chain = ClaimedChain(history, ancestor)

        return self.chain

    def check(
        self, chain: ClaimedChain, reader: TargetReader, *, bounds: BlockBounds = BLOCK_BOUNDS
    ) -> tuple[ContentId, TransactionBlockCheck | ValidationBlockCheck | BlockFinding] | None:
        """Check it forward against ``chain``, from the first generation not checked.

        Each generation correct is held by ``chain``.

        Returns:
            ``None`` once every block checks correct; or else the first that
            does not, and what was found of it.

        Raises:
            MissingContentError: names what a check lacks, which is asked for.
            BundleError: a block is shunned, or cannot be checked here.
            ValueError: a block names an id under a hash algorithm this node
                lacks, or a block that is no block of its kind.
        """
        for claimed in self.walked[::-1][len(chain.generations) :]:
            failed = chain.check(claimed, reader, bounds=bounds)

            if failed is not None:
                return failed

        return None
