"""Following the chain, a generation at a time (KarmaLedger §3.2, §3.3, §7.5, Phase 6 Step 126).

A node that follows no chain reads what is placed at the root's targets,
``karma transaction:0`` and ``karma validation:0``, checks each block there
(Phase 6 Steps 121 and 122), and follows the heaviest root it finds correct:
the one whose two drops weigh the most, and of two as heavy, the one whose
validation block's id sorts first (§3.3, §7.5). From then on it reads the
targets named for its head (§3.1), and follows the best correct validation
block there (§6.5), adding its generation to the history, and goes on at
once to the targets named for that block, until it finds none to follow. A
better validation block of a generation already followed is not looked for
here: it is a head the node does not follow, which
:class:`~libranet.karma.changing.HeadChanges` weighs (Phase 6 Step 129).

A block whose check needs what the node lacks, a signer's key or a block of
the chain, is checked again once that has come: the reader asks for it
(Phase 6 Step 124). A block checked is not checked again while the head is
the same, and what was found of it is said once. A block found unsigned, or
whose signature fails, is shunned (§3.7, §3.8); one found false is logged
as a warning, and said, for its builder, and the staker of a stake that
alone exceeds what covers it, to be distrusted (Phase 6 Step 127)::

    data.shunned       {"algorithm", "hash"}
    karma.false_block  {"block": "sha256/…", "builder": "sha256/…",
                        "staker": "sha256/…" or null, "statement": "…"}

A block this node cannot check, as one signed in a way it cannot check, or
naming an id under a hash algorithm it lacks, or one naming a block it
shuns, is passed over, and logged as a warning. A block of another target,
which anyone may place a copy of at this one, since a drop's nonce is not
signed, is passed over too, and logged: one of the other kind, or one built
on, or staking on one built on, another validation block than the one the
target is named for (:class:`~libranet.karma.errors.MisplacedBlockError`).
Nothing else is shunned: not a block for being on a lighter chain, nor for
not being chosen (§3.6).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from functools import partial
from logging import getLogger
from typing import Callable, Iterator, Mapping, TypeVar

from libranet.bundle.errors import BundleError, MissingContentError, UnsupportedBundleError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.errors import MisplacedBlockError
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.reading import HeldBlock, HeldChain, TargetReader
from libranet.karma.validating import ValidationBlockCheck
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish

_LOGGER = getLogger(__name__)

_Found = TypeVar("_Found")


@dataclass
class _Looking:
    """What was found of the blocks at ``targets``, those of the generation after the head's.

    A block passed over is found ``None``.
    """

    targets: GenerationTargets
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding | None] = field(
        default_factory=dict
    )
    validations: dict[ContentId, ValidationBlockCheck | BlockFinding | None] = field(
        default_factory=dict
    )


class _AtTarget(Mapping[ContentId, TransactionBlockCheck | BlockFinding]):
    """The checks of the transaction blocks read at a target, for a validation block's check.

    A block read there and not checked is among them too, so that a
    validation block staking on it is not taken to name one at another
    target: reading its check raises
    :class:`~libranet.bundle.errors.MissingContentError`, naming what
    ``lacking`` says its check lacks, so that the validation block waits for
    it too; or :class:`~libranet.bundle.errors.UnsupportedBundleError` for
    one passed over, so that the validation block is passed over too.
    """

    def __init__(
        self,
        checks: Mapping[ContentId, TransactionBlockCheck | BlockFinding | None],
        lacking: Mapping[ContentId, tuple[ContentId, ...]],
    ) -> None:
        self._checks = checks
        self._lacking = lacking

    def __getitem__(self, block_id: ContentId) -> TransactionBlockCheck | BlockFinding:
        if block_id in self._lacking:
            raise MissingContentError(self._lacking[block_id])

        check = self._checks[block_id]

        if check is None:
            raise UnsupportedBundleError(f"It names {block_id}, which is passed over")

        return check

    def __contains__(self, block_id: object) -> bool:
        return block_id in self._checks or block_id in self._lacking

    def __iter__(self) -> Iterator[ContentId]:
        return iter([*self._checks, *self._lacking])

    def __len__(self) -> int:
        return len(self._checks) + len(self._lacking)


class ChainFollower:
    """Follows the chain ``history`` keeps on from its head, with the blocks ``reader`` holds.

    What it finds of a block is said with ``publish``, and each block is
    checked against ``bounds``: the specification's, unless a test gives
    less.
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        publish: Publish,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._history = history
        self._reader = reader
        self._publish = publish
        self._bounds = bounds
        self._chain = HeldChain(history, reader)
        self._looking = _Looking(GenerationTargets.built_on(self._named_for()))

    def look(self) -> list[ChainGeneration]:
        """Follow each generation after the head's that the blocks held at its targets choose.

        Each is logged, and added to the history.

        Returns:
            The generations followed, in order, none while no correct
            validation block is held at the targets of the head's.

        Raises:
            OSError: the history could not be written, as
                :meth:`~libranet.karma.history.ChainHistory.add` says. The
                generations before are followed.
        """
        followed: list[ChainGeneration] = []

        while (generation := self._chosen()) is not None:
            self._history.add(generation)
            followed.append(generation)
            _LOGGER.info(
                "Following Karma generation %d: validation block %s, validating %s",
                generation.generation,
                generation.validation,
                generation.transaction,
            )

        return followed

    def checked(self) -> tuple[dict[ContentId, TransactionBlockCheck], list[ValidationBlockCheck]]:
        """The blocks held at the targets of the generation after the head's, stating nothing false.

        They are read and checked as :meth:`look` checks them, each not
        checked before at the head, and what is found of one said, but none
        is followed. One whose check lacks what is not held is left out.

        Returns:
            The transaction blocks, by id, and the validation blocks, each
            correct or not.
        """
        checks = self._checked_at(self._named_for())
        transactions = {
            block_id: check
            for block_id, check in self._looking.transactions.items()
            if isinstance(check, TransactionBlockCheck)
        }
        return transactions, checks

    def _named_for(self) -> ContentId | None:
        """The head, which the next generation's targets are named for, or ``None`` if none."""
        head = self._history.head
        return None if head is None else head.validation

    def _chosen(self) -> ChainGeneration | None:
        """The generation after the head's to follow, of those held at its targets, or ``None``.

        That is the heaviest correct root, while the node follows none (§7.5),
        and after the head, its best correct validation block (§6.5).
        """
        named_for = self._named_for()
        checks = self._checked_at(named_for)

        if named_for is None:
            roots = [root for check in checks if (root := self._generation(check)) is not None]
            return min(roots, key=lambda root: (-root.weight.work, root.validation), default=None)

        best = ValidationBlockCheck.best(checks)
        return None if best is None else self._generation(best)

    def _checked_at(self, named_for: ContentId | None) -> list[ValidationBlockCheck]:
        """The checks of the validation blocks held at the targets named for ``named_for``.

        The transaction blocks held at the same generation's transaction
        target are checked first. What was found at other targets is
        forgotten.
        """
        targets = GenerationTargets.built_on(named_for)

        if targets != self._looking.targets:
            self._looking = _Looking(targets)

        transactions = self._transactions(named_for, targets.transaction)
        return self._validations(named_for, targets.validation, transactions)

    def _transactions(self, named_for: ContentId | None, target: DropTarget) -> _AtTarget:
        """The checks of the transaction blocks held at ``target``, named for ``named_for``."""
        checks: dict[ContentId, TransactionBlockCheck | BlockFinding | None] = {}
        lacking: dict[ContentId, tuple[ContentId, ...]] = {}
        check = partial(self._transaction, named_for)

        for held in self._reader.read(target, TransactionBlock):
            try:
                checks[held.block_id] = self._found(self._looking.transactions, held, check)

            except MissingContentError as error:
                # Not logged: what it lacks is asked for, and it is checked
                # once that comes.
                lacking[held.block_id] = error.content_ids

        return _AtTarget(checks, lacking)

    def _validations(
        self, named_for: ContentId | None, target: DropTarget, transactions: _AtTarget
    ) -> list[ValidationBlockCheck]:
        """The checks of the validation blocks held at ``target``, named for ``named_for``.

        ``transactions`` are the checks of the transaction blocks at the
        transaction target of the same generation.
        """
        checks: list[ValidationBlockCheck] = []
        check = partial(self._validation, named_for, transactions)

        for held in self._reader.read(target, ValidationBlock):
            try:
                found = self._found(self._looking.validations, held, check)

            except MissingContentError:
                # Not logged: what it lacks is asked for, and it is checked
                # once that comes.
                continue

            if isinstance(found, ValidationBlockCheck):
                checks.append(found)

        return checks

    def _transaction(
        self, named_for: ContentId | None, held: HeldBlock
    ) -> TransactionBlockCheck | BlockFinding:
        """Transaction block ``held`` checked, read at the target named for ``named_for``.

        Raises:
            MisplacedBlockError: it is of another target.
            MissingContentError: names what its check lacks.
            BundleError: it cannot be checked here, or a block of the chain
                cannot be read.
            ValueError: it names an id under a hash algorithm this node
                lacks.
        """
        return TransactionBlockCheck.of_held(held, named_for, self._chain, bounds=self._bounds)

    def _validation(
        self, named_for: ContentId | None, transactions: _AtTarget, held: HeldBlock
    ) -> ValidationBlockCheck | BlockFinding:
        """Validation block ``held`` checked, read at the target named for ``named_for``.

        ``transactions`` are the checks of the transaction blocks at the
        transaction target of the same generation.

        Raises:
            MisplacedBlockError: it is of another target.
            MissingContentError: names what its check lacks.
            BundleError: it, or a block it names, cannot be checked here.
            ValueError: it names an id under a hash algorithm this node
                lacks, or a block that is no block of its kind.
        """
        return ValidationBlockCheck.of_held(
            held, named_for, self._chain, transactions=transactions, bounds=self._bounds
        )

    def _found(
        self,
        found: dict[ContentId, _Found | None],
        held: HeldBlock,
        check: Callable[[HeldBlock], _Found],
    ) -> _Found | None:
        """What ``found`` keeps of block ``held``, checked by ``check`` unless it was before.

        What is found of a block checked now is kept, and said
        (:func:`say_found`). One of another target, or that cannot be checked
        here, is passed over, as ``None``, and logged.

        Raises:
            MissingContentError: names what ``check`` lacks, which is asked
                for; nothing is kept.
        """
        block_id = held.block_id

        if block_id in found:
            return found[block_id]

        try:
            checked: _Found | None = check(held)

        except MissingContentError:
            raise

        except MisplacedBlockError as error:
            _LOGGER.info("Passing over Karma block %s, of another target: %s", block_id, error)
            checked = None

        except (BundleError, ValueError) as error:
            _LOGGER.warning(
                "Passing over Karma block %s, which cannot be checked: %s", block_id, error
            )
            checked = None

        found[block_id] = checked
        say_found(self._publish, block_id, checked)
        return checked

    def _generation(self, check: ValidationBlockCheck) -> ChainGeneration | None:
        """The generation in which ``check``'s validation block, correct, is the winner.

        Its transaction block is one checked at the target of the same
        generation, as a correct block's is. Were it not, that is logged,
        and there is no generation.
        """
        validated_id = check.drop.block.stakes[0].stake.transaction_block
        validated = self._looking.transactions.get(validated_id)

        if not isinstance(validated, TransactionBlockCheck):
            _LOGGER.error(
                "Not following Karma validation block %s: %s, which it validates, was not checked",
                check.block_id,
                validated_id,
            )
            return None

        return ChainGeneration.of(
            check.block_id, check.drop.block, validated_id, validated.drop.block
        )


def say_found(publish: Publish, block_id: ContentId, found: object) -> None:
    """Say with ``publish`` what block ``block_id`` was ``found``, if unsigned or false (§3.8).

    One unsigned is shunned, and one false said, for its builder to be
    distrusted; each is logged as a warning.
    """
    if not isinstance(found, BlockFinding):
        return

    if found.builder is None:
        _LOGGER.warning("Shunning Karma block %s, which is unsigned: %s", block_id, found.statement)
        publish(EventType.DATA_SHUNNED, block_id.fields())
        return

    _LOGGER.warning(
        "Karma block %s, built by %s, states something false: %s",
        block_id,
        found.builder,
        found.statement,
    )
    publish(
        EventType.KARMA_FALSE_BLOCK,
        {
            "block": str(block_id),
            "builder": str(found.builder),
            "staker": None if found.staker is None else str(found.staker),
            "statement": found.statement,
        },
    )
