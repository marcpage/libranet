"""Checking a validation block, and choosing a generation's best (KarmaLedger §3.8, §6.5, §7.3).

A validation block that is unsigned, or states something false by itself,
is found so as it is read (:meth:`~libranet.karma.blocks.BlockDrop.decoded`).
One whose stakes name a transaction block built on another validation block
than the one its target is named for, as one that is a transaction block, is
of another target, where anyone may have placed it, and is passed over
(:class:`~libranet.karma.blocks.MisplacedBlockError`). What any other states
against its target and its chain is checked here: that its stakes name one
transaction block, at the transaction target of its generation, which is
correct; its ``generation``, against the validation block its target is
named for; that its ``runner_up`` is another validation block there,
validating another transaction block; that every stake, each
staker's together, and the runner-up's stakes its reward would take are
covered (§6.3); and that it lists no witness an earlier winning validation
block of its chain lists. A block stating any of these falsely distrusts
its builder, and a stake that alone exceeds what covers it distrusts its
staker too (§3.8).

A block stating nothing false may still not be correct: one validating a
transaction block not full, or one larger than a block may be, each as the
node holds it, which its builder's copy, compressed otherwise, may not be
(§5.4). The root's validation block is checked by its shape, and one at its
target that is not the root's is passed over (§7.3). Of a generation's
correct blocks, the best shares the most bits with its target hash, then
holds the greatest total effective stake, then has the id that sorts first.

The chain is read through a :class:`~libranet.karma.checking.ChainView`, and
the transaction blocks at the same generation's target are given as the
checks :class:`~libranet.karma.checking.TransactionBlockCheck` made of them.
What the view does not hold, it names, and the check waits for it rather
than judging.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Collection, Iterable, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    Balance,
    BlockBounds,
    BlockDrop,
    BlockFinding,
    MisplacedBlockError,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import ChainView, TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.ledger import Reward, StakeWeight


def balances_left(
    validated: TransactionBlock, identities: Collection[ContentId], chain: ChainView
) -> dict[ContentId, Balance]:
    """Each of ``identities``' balance as transaction block ``validated``, on ``chain``, leaves it.

    That is the one ``validated`` states, or else the latest stated through
    the block before it (§5.3), as what covers a stake is read (§6.3).

    Raises:
        MissingContentError: names the blocks within reach that ``chain``
            lacks.
    """
    stated = set(identities) & set(validated.balances)
    return {
        **chain.balances(set(identities) - stated, validated.generation - 1),
        **{identity: validated.balances[identity] for identity in stated},
    }


@dataclass(frozen=True, order=True)
class ValidationScore:
    """What a correct validation block ranks by among its generation's (§6.5).

    ``bits`` are those its drop shares with its target hash, and ``stake`` its
    total effective stake, in tenths of a Kismet: the sum, over its stakes,
    of each one's Kismet times its staker's weight (§6.6). One score is above
    another with more bits, and of as many, with more stake. Of two blocks
    scoring alike, the one whose id sorts first is the better
    (:meth:`ValidationBlockCheck.best`).
    """

    bits: int
    stake: int


@dataclass(frozen=True)
class _Validating:
    """Validation block ``drop``, whose id is ``block_id``, checked as generation ``generation``'s.

    It was read at the validation target named for validation block
    ``named_for``, or at the root's when that is ``None``, and ``chain`` is
    the chain through that block. ``transactions`` are the checks of the
    transaction blocks read at the transaction target of the same
    generation, by id.
    """

    block_id: ContentId
    drop: BlockDrop[ValidationBlock]
    named_for: ContentId | None
    generation: int
    chain: ChainView
    transactions: Mapping[ContentId, TransactionBlockCheck | BlockFinding]

    def at_target(self, block_id: ContentId) -> TransactionBlockCheck | BlockFinding | None:
        """The check of transaction block ``block_id``, or ``None`` if it is at another target.

        One at the target is among :attr:`transactions`. One that is not is
        read from :attr:`chain`, so that one not held is fetched, and judged
        only once it has been (§3.8).

        Raises:
            MissingContentError: it is not at the target, and ``chain`` lacks
                it.
        """
        if block_id in self.transactions:
            return self.transactions[block_id]

        self.chain.transaction(block_id)
        return None

    def false_basis(self) -> str:
        """What it states falsely by itself of what it stakes on, or ``""`` (§3.8, §6.1).

        It must hold a stake, its stakes must all name one transaction block,
        and its ``runner_up`` must not name itself.
        """
        block = self.drop.block
        named = {stake.stake.transaction_block for stake in block.stakes}

        if len(named) != 1:
            return (
                f"A validation block must hold a stake, all its stakes on one transaction "
                f"block, got {len(block.stakes)} on {len(named)}"
            )

        if block.runner_up == self.block_id:
            return "A validation block's runner_up must name another validation block, not itself"

        return ""

    def check_placed(self, validated_id: ContentId) -> None:
        """Raise unless transaction block ``validated_id``, which it stakes on, is of its target.

        That is one built on the validation block its target is named for.

        Raises:
            MisplacedBlockError: it is built on another, so that this block
                is of that one's target, and is passed over at this.
            MissingContentError: names it, which ``chain`` lacks.
        """
        previous = self.chain.transaction(validated_id).block.previous

        if previous != self.named_for:
            raise MisplacedBlockError(
                f"Its stakes name {validated_id}, built on {previous}, where a block at its "
                f"target stakes on one built on {self.named_for}"
            )

    def false_generation(self) -> str:
        """What it states falsely of its generation, or ``""`` (§3.8, §6.1).

        It must be one more than that of the validation block its target is
        named for.
        """
        generation = self.drop.block.generation

        if generation != self.generation:
            return (
                f'A validation block\'s "generation" must be {self.generation}, one more than '
                f"that of {self.named_for}, its target's, got {generation}"
            )

        return ""

    def runner_up(self) -> ValidationBlock | None:
        """The validation block it names as its runner-up, or ``None`` if it names none.

        Raises:
            MissingContentError: names the runner-up, which ``chain`` lacks.
        """
        named = self.drop.block.runner_up
        return None if named is None else self.chain.validation(named).block

    def false_runner_up(self, validated_id: ContentId, runner_up: ValidationBlock | None) -> str:
        """What it states falsely of ``runner_up``, the block it names as its runner-up, or ``""``.

        A runner-up is a validation block of the same generation, whose
        stakes name a transaction block at the same target, and not
        ``validated_id``, the one this block validates (§6.1, §6.5). It need
        not be correct.

        Raises:
            MissingContentError: names a transaction block its stakes name,
                which ``chain`` lacks.
        """
        if runner_up is None:
            return ""

        named = self.drop.block.runner_up
        staked_on = sorted({stake.stake.transaction_block for stake in runner_up.stakes})

        if validated_id in staked_on:
            return (
                f"A validation block's runner_up must validate another transaction block, "
                f"and {named} validates {validated_id} too"
            )

        if (
            runner_up.generation != self.generation
            or not staked_on
            or any(self.at_target(block_id) is None for block_id in staked_on)
        ):
            return f"A validation block's runner_up must be at its target, got {named}"

        return ""

    def balances(
        self, validated: TransactionBlock, runner_up: ValidationBlock | None
    ) -> dict[ContentId, Balance]:
        """The balances its reward is worked out from, as ``validated`` leaves them (§5.3, §6.6).

        They are its stakers', the stakers' of ``runner_up``, and those of
        the senders in ``validated``, whose fees the reward takes, each the
        latest stated through ``validated``.

        Raises:
            MissingContentError: names the blocks within reach that ``chain``
                lacks.
        """
        identities = {stake.staker for stake in self.drop.block.stakes}
        identities.update(signed.transaction.sender for signed in validated.signed)

        if runner_up is not None:
            identities.update(stake.staker for stake in runner_up.stakes)

        return balances_left(validated, identities, self.chain)

    def false_cover(self, reward: Reward) -> BlockFinding | None:
        """What it states falsely of what covers its stakes and its runner-up's, or ``None`` (§6.3).

        What covers an identity is its Kismet as the block it validates
        leaves it, less what ``reward``'s payout takes from it: the fees of
        its transactions there, and, if it stakes only in the runner-up, its
        stakes there too. Each stake must be covered alone, each staker's
        stakes together, and what is taken of the runner-up's stakers by
        their balances. A stake that alone is not names its staker too, since
        that never changes.
        """
        builder = self.drop.builder
        pot = reward.pot()
        left = {
            identity: balance.kismet.count - pot.get(identity, Kismet(0)).count
            for identity, balance in reward.balances.items()
        }
        staked: dict[ContentId, int] = {}

        for signed in self.drop.block.stakes:
            count = signed.stake.kismet.count
            staker = signed.staker

            if count > left[staker]:
                return BlockFinding(
                    f"Stake {signed.hash} is of {count} Kismet, more than the {left[staker]} "
                    f"that cover {staker}",
                    builder,
                    staker,
                )

            staked[staker] = staked.get(staker, 0) + count

        for staker, count in sorted(staked.items()):
            if count > left[staker]:
                return BlockFinding(
                    f"The stakes of {staker} in a validation block are {count} Kismet, more than "
                    f"the {left[staker]} that cover them",
                    builder,
                )

        short = sorted(identity for identity, count in left.items() if count < 0)

        if short:
            return BlockFinding(
                f"A validation block's runner_up holds stakes of {short[0]} that the reward "
                f"would take, more than its balance covers",
                builder,
            )

        return None

    def false_witness(self) -> str:
        """What it states falsely of its witnesses, or ``""`` (§6.4).

        No witness may be one an earlier winning validation block of its
        chain lists, of those ``chain`` holds.
        """
        for witness in self.drop.block.witnessed:
            if (generation := self.chain.witnessed(witness)) is not None:
                return (
                    f"Transaction {witness} is witnessed already, in generation {generation} "
                    f"of the chain"
                )

        return ""


@dataclass(frozen=True)
class ValidationBlockCheck:
    """Validation block ``block_id``, ``drop``, checked and found to state nothing false.

    A correct block is ranked by its ``score``, and the best of a
    generation's is the one the next is built on (:meth:`best`). One that is
    not correct is never chosen: its ``score`` is ``None``, and ``incorrect``
    says why. That is so of one validating a transaction block not full, or
    one larger than a block may be, each as held, since its builder's copy,
    compressed otherwise, may measure otherwise (§3.8, §5.4); and of one at
    the root's target that is not the root's, which is passed over (§7.3).
    :meth:`of` checks a block.

    Raises:
        ValueError: ``score`` and ``incorrect`` are both given, or neither.
    """

    block_id: ContentId
    drop: BlockDrop[ValidationBlock]
    score: ValidationScore | None
    incorrect: str = ""

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
        transactions: Mapping[ContentId, TransactionBlockCheck | BlockFinding],
        compressed_bytes: int,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> ValidationBlockCheck | BlockFinding:
        """Validation block ``block_id``, the drop ``data``, checked; or what it states falsely.

        It was read at the validation target named for validation block
        ``named_for``, or at the root's, ``karma validation:0``, when that is
        ``None``, where it is checked by its shape (§7.3). ``chain`` is the
        chain through ``named_for``, and ``transactions`` the checks of the
        transaction blocks read at the transaction target of the same
        generation, by id: one its stakes name that is not among them is at
        another target. Signatures are checked with ``keys``, and its size,
        the copy held being ``compressed_bytes`` compressed, against
        ``bounds``.

        Returns:
            A :class:`BlockFinding` naming no builder if it is unsigned, and
            its builder if it states something false, with the staker of a
            stake that alone exceeds what covers it; or else the block,
            correct and scored, or not correct.

        Raises:
            MisplacedBlockError: it is a transaction block, or its stakes
                name a transaction block built on another validation block
                than ``named_for``, and so it is of another target, where it
                is passed over.
            MissingContentError: names the keys ``keys`` lacks, or the blocks
                ``chain`` lacks, which the check waits for.
            UnsupportedBundleError: it, or what it holds, is signed in a way
                this node cannot check.
            UnknownAlgorithmError: it names an id under a hash algorithm this
                node lacks.
        """
        found = BlockDrop.decoded(data, ValidationBlock, keys, bounds=bounds)

        if isinstance(found, BlockFinding):
            return found

        target = GenerationTargets.built_on(named_for).validation
        bits = matching_bits(target.hex, block_id.hash)

        if named_for is None:
            return cls._root(_Validating(block_id, found, None, 0, chain, transactions), bits)

        generation = chain.validation(named_for).block.generation + 1
        checking = _Validating(block_id, found, named_for, generation, chain, transactions)
        return cls._on_chain(checking, bits, compressed_bytes=compressed_bytes, bounds=bounds)

    @property
    def is_correct(self) -> bool:
        """Whether it is correct, and so may be chosen (§6.5)."""
        return self.score is not None

    @staticmethod
    def best(checks: Iterable[ValidationBlockCheck]) -> ValidationBlockCheck | None:
        """The best of ``checks``, a generation's, that are correct, or ``None`` if none is (§6.5).

        That is the one of the highest score, and of those, the one whose id
        sorts first, as of two chains as heavy (§3.3).
        """
        ranked = [(check.score, check) for check in checks if check.score is not None]

        if not ranked:
            return None

        top = max(score for score, _ in ranked)
        tied = [check for score, check in ranked if score == top]
        return min(tied, key=lambda check: check.block_id)

    @classmethod
    def _root(cls, checking: _Validating, bits: int) -> ValidationBlockCheck:
        """``checking``'s block, at the root's target, checked by its shape (§7.3).

        It is generation 0's, built by the root's creator, who built the
        transaction block it validates, and signed its one stake, of 0, on
        that block. It names no runner-up, and lists no witness. Otherwise it
        is passed over, and states nothing false.

        Raises:
            MissingContentError: names the transaction block its stake names,
                which is not at the root's target, and which its chain lacks.
        """
        drop = checking.drop
        block = drop.block
        stakes = block.stakes
        validated = (
            None if len(stakes) != 1 else checking.at_target(stakes[0].stake.transaction_block)
        )
        shaped = (
            block.generation == 0
            and block.runner_up is None
            and not block.witnessed
            and isinstance(validated, TransactionBlockCheck)
            and validated.is_correct
            and stakes[0].stake.kismet == Kismet(0)
            and stakes[0].staker == drop.builder == validated.drop.builder
        )

        if not shaped:
            return cls(
                checking.block_id,
                drop,
                None,
                "A validation block at the root's target is passed over unless it is generation "
                "0, built by the root's creator, holding only their stake of 0 on the root's "
                "transaction block, with no runner-up and no witness",
            )

        return cls(checking.block_id, drop, ValidationScore(bits, 0))

    @classmethod
    def _on_chain(
        cls, checking: _Validating, bits: int, *, compressed_bytes: int, bounds: BlockBounds
    ) -> ValidationBlockCheck | BlockFinding:
        """``checking``'s block checked against its target, and the transaction block it names.

        Raises:
            MisplacedBlockError: its stakes name a transaction block of
                another target.
            MissingContentError: names the blocks ``checking``'s chain lacks.
        """
        builder = checking.drop.builder
        statement = checking.false_basis()

        if statement:
            return BlockFinding(statement, builder)

        validated_id = checking.drop.block.stakes[0].stake.transaction_block
        validated = checking.at_target(validated_id)

        if validated is None:
            checking.check_placed(validated_id)
            return BlockFinding(
                f"A validation block's stakes must name a transaction block at its target, "
                f"got {validated_id}",
                builder,
            )

        statement = checking.false_generation()

        if statement:
            return BlockFinding(statement, builder)

        reason = validated.statement if isinstance(validated, BlockFinding) else validated.incorrect

        # A block found not full, as held, may be full in its builder's copy
        # (§3.8), so validating it is no false statement.
        if isinstance(validated, BlockFinding) or (validated.full and not validated.is_correct):
            return BlockFinding(
                f"A validation block must validate a correct transaction block, and "
                f"{validated_id} is not: {reason}",
                builder,
            )

        return cls._validating(
            checking,
            validated_id,
            validated,
            bits,
            compressed_bytes=compressed_bytes,
            bounds=bounds,
        )

    @classmethod
    def _validating(
        cls,
        checking: _Validating,
        validated_id: ContentId,
        validated: TransactionBlockCheck,
        bits: int,
        *,
        compressed_bytes: int,
        bounds: BlockBounds,
    ) -> ValidationBlockCheck | BlockFinding:
        """``checking``'s block, validating ``validated``, whose id is ``validated_id``, checked.

        ``validated`` is at its target, and correct, or not correct only for
        not being full, as held (§3.8, §6.5).

        Raises:
            MissingContentError: names the blocks ``checking``'s chain lacks.
        """
        drop = checking.drop
        runner_up = checking.runner_up()
        statement = checking.false_runner_up(validated_id, runner_up)

        if statement:
            return BlockFinding(statement, drop.builder)

        balances = checking.balances(validated.drop.block, runner_up)
        reward = Reward(drop.block, drop.builder, validated.drop.block, balances, runner_up)
        finding = checking.false_cover(reward)

        if finding is not None:
            return finding

        statement = checking.false_witness()

        if statement:
            return BlockFinding(statement, drop.builder)

        incorrect = cls._incorrect(validated_id, validated, compressed_bytes, bounds)

        if incorrect:
            return cls(checking.block_id, drop, None, incorrect)

        stake = sum(StakeWeight.effective_stakes(drop.block, balances).values())
        return cls(checking.block_id, drop, ValidationScore(bits, stake))

    @staticmethod
    def _incorrect(
        validated_id: ContentId,
        validated: TransactionBlockCheck,
        compressed_bytes: int,
        bounds: BlockBounds,
    ) -> str:
        """Why a block validating ``validated`` is not correct, stating nothing false, or ``""``.

        The block is held ``compressed_bytes`` compressed, and may be no larger
        than ``bounds`` allow (§5.4, §6.5). ``validated_id`` is the id of
        ``validated``.
        """
        # By now, one it validates that is not correct is so only for not
        # being full, as held.
        if not validated.is_correct:
            return (
                f"A validation block must validate a full transaction block, and {validated_id} "
                f"is not, as held: {validated.incorrect}"
            )

        if compressed_bytes > bounds.most_bytes:
            return (
                f"A validation block must be at most {bounds.most_bytes} bytes compressed, "
                f"got {compressed_bytes}"
            )

        return ""
