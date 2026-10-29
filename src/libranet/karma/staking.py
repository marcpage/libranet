"""Staking, and building validation blocks (KarmaLedger §6, Phase 6 Step 136).

Once a full, correct transaction block is held at the head's transaction
target, the Karma module stakes on it, and builds a validation block of the
stakes it has, so that the chain goes on (§3.1). Every
``karma.build_interval_seconds``, it reads the blocks held at the head's two
targets, checked as the chain followed checks them (Phase 6 Step 126), and
takes the best scored transaction block there that is correct, and that no
identity the node distrusts signed (§3.8, §5.5); of two as good, the one
whose id sorts first.

What covers an identity's stakes on that block is its Kismet as the block
leaves it, less the fees of its transactions there, which the next block's
payout takes (§6.3). The node stakes ``karma.stake_percent`` of what covers
it, or the share its operator sets on ``/config`` (Phase 6 Step 138), read
each time it stakes, rounded down to whole Kismet, and nothing while that is
0. It signs its stake once at a head, and none if a validation block held at
the head's validation target holds one of its own on the block, as one it
built before it was started again does. Others' stakes are those on the same
block in the validation blocks held there, and those people make through the
node (Phase 6 Step 137), kept until the head changes, each whose signature
checks with its staker's key, but those of every staker the node distrusts
(§3.8). Of each staker's, the largest are taken first, while what covers the
staker covers them together.

The block names as its runner-up the first of the other validation blocks
held there that state nothing false, and so validate another transaction
block, whose stakes its reward would take are covered by the balances its
own transaction block leaves (§6.3, §6.5): the correct ones first, the best
first, and then the rest, by the bits they share with the target. It names
none if none is covered. It lists as witnesses the hashes of the
transactions the node carries, then of those in the transaction blocks held
at the head's transaction target, that no winning validation block of the
chain lists, and that the block it validates does not hold (§6.4), as many
as leave its drop, compressed as the node stores it, within 1 MiB (§5.4). It
is signed by the node, its nonce searched for for ``karma.block_seconds``,
and after that until it shares ``storage.search_min_bits`` with its target
(§2), and it is stored and announced, as a transaction block the node builds
is. The chain followed takes it as the head as the module next looks.

Nothing is built while a correct validation block held at the head's
validation target holds every stake that would be taken, so a block is built
again only while stakes come that those held lack.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from logging import getLogger
from time import time
from typing import Callable, Iterable, Mapping, Sequence

from libranet.bundle.errors import BundleError, BundleSignatureError, MissingContentError
from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.identity.node_identity import NodeIdentity
from libranet.json_format import moment_at
from libranet.karma.blocks import (
    BLOCK_BOUNDS,
    BlockBounds,
    BlockDrop,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import ChainView, TransactionBlockCheck
from libranet.karma.following import ChainFollower
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainHistory
from libranet.karma.ledger import Reward
from libranet.karma.placing import BlockPlacing
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.validating import ValidationBlockCheck, ValidationScore, balances_left
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance
from libranet.protocol.karma_stakes import SignedStake, Stake

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class StakeCover:
    """What covers the stakes on transaction block ``validated`` of those ``balances`` names.

    ``balances`` are theirs as ``validated`` leaves them, and those of the
    senders it holds, whose fees the next block's payout takes (§6.3).
    :meth:`of` reads them.
    """

    validated: TransactionBlock
    balances: Mapping[ContentId, Balance]

    @classmethod
    def of(
        cls, validated: TransactionBlock, stakers: Iterable[ContentId], chain: ChainView
    ) -> StakeCover:
        """What covers the stakes of ``stakers`` on ``validated``, built on ``chain``.

        Raises:
            MissingContentError: names the blocks within reach that ``chain``
                lacks.
        """
        identities = {*stakers, *(signed.transaction.sender for signed in validated.signed)}
        return cls(validated, balances_left(validated, identities, chain))

    def covering(self, staker: ContentId) -> Kismet:
        """What covers ``staker``'s stakes, together: its Kismet, less its fees in the block."""
        fees = sum(
            signed.transaction.fee.count
            for signed in self.validated.signed
            if signed.transaction.sender == staker
        )
        return Kismet(self.balances[staker].kismet.count - fees)

    def taken(self, offered: Iterable[SignedStake]) -> tuple[SignedStake, ...]:
        """Those of ``offered``, each once, that a validation block takes, sorted by hash (§6.1).

        Of each staker's, the largest are taken first, and of as large, the
        one whose hash sorts first, each while what covers the staker covers
        it and those taken before it together.
        """
        once = {signed.hash: signed for signed in offered}
        staked: dict[ContentId, int] = {}
        taken: list[SignedStake] = []

        for signed in sorted(once.values(), key=lambda each: (-each.stake.kismet.count, each.hash)):
            count = staked.get(signed.staker, 0) + signed.stake.kismet.count

            if count <= self.covering(signed.staker).count:
                staked[signed.staker] = count
                taken.append(signed)

        return tuple(sorted(taken, key=lambda signed: signed.hash))

    def runner_up(
        self, block: ValidationBlock, builder: ContentId, rivals: Iterable[ValidationBlockCheck]
    ) -> ContentId | None:
        """The first of ``rivals`` whose stakes ``block``'s reward would take are covered (§6.3).

        ``block``, which ``builder`` builds on ``validated``, names none.
        Naming a rival, its reward would take the stakes of each staker in
        the rival that has none in ``block``, and the fees of each sender,
        and each must hold what is taken of it (§5.2). Every staker in a
        rival is among those ``balances`` names.

        Returns:
            The rival's id, or ``None`` if none is covered.
        """
        for rival in rivals:
            named = replace(block, runner_up=rival.block_id)
            pot = Reward(named, builder, self.validated, self.balances, rival.drop.block).pot()

            if all(
                amount <= self.balances[identity].kismet
                for identity, amount in pot.items()
                if identity is not None
            ):
                return rival.block_id

        return None


@dataclass(frozen=True)
class StakedBlock:
    """The stakes a validation block on transaction block ``validated_id`` takes.

    ``cover`` is what covers them, and holds that block. ``stakes`` are
    sorted by hash, and at least one (§6.1).
    """

    validated_id: ContentId
    cover: StakeCover
    stakes: tuple[SignedStake, ...]


class HeadStakes:
    """Stakes on the best transaction block at the head's target, and takes others' stakes on it.

    The chain is the one ``history`` keeps, its blocks read from what
    ``reader`` holds. Whom the node distrusts, ``distrusts`` says (§3.8).
    The node stakes the share of what covers it ``stake_percent`` gives, in
    percent, as it stakes, its stake created at the time ``clock`` gives, in
    seconds since the epoch. The stakes people make through the node it is
    given (:meth:`take`).
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        *,
        distrusts: Callable[[ContentId], bool],
        stake_percent: Callable[[], float],
        clock: Callable[[], float] = time,
    ) -> None:
        self._history = history
        self._reader = reader
        self._distrusts = distrusts
        self._stake_percent = stake_percent
        self._clock = clock
        self._chain = HeldChain(history, reader)
        # The head these were found at: the node's stake on each transaction
        # block, or None where it stakes nothing, the stakes whose
        # signatures fail, each logged once, and the stakes given, by hash.
        self._head: ContentId | None = None
        self._own: dict[ContentId, SignedStake | None] = {}
        self._passed_over: set[str] = set()
        self._given: dict[str, SignedStake] = {}

    def take(self, signed: SignedStake) -> None:
        """Keep ``signed``, a stake a person made through the node, until the head changes.

        It is checked as each stake held at the head's validation target is,
        as it is taken (:meth:`staked`).
        """
        head = self._history.head
        self._at(None if head is None else head.validation)
        self._given[signed.hash] = signed
        _LOGGER.info(
            "Taking Karma stake %s of %s Kismet by %s on Karma transaction block %s",
            signed.hash,
            signed.stake.kismet.value(),
            signed.staker,
            signed.stake.transaction_block,
        )

    def staked(
        self,
        transactions: Mapping[ContentId, TransactionBlockCheck],
        identity: NodeIdentity,
        *,
        also: Iterable[ContentId] = (),
    ) -> StakedBlock | None:
        """The stakes a validation block takes on the best of ``transactions``, or ``None``.

        ``transactions`` are the transaction blocks held at the head's
        transaction target, as the chain followed checks them, and the best
        is the best scored that is correct, and that no distrusted identity
        built (:meth:`_validated`). The stakes are those on it held at the
        head's validation target, and those given (:meth:`_offered`), and
        ``identity``'s, the node's (:meth:`_own_stake`), each staker's
        covered together (:meth:`StakeCover.taken`). What covers ``also``,
        whose stakes a runner-up may hold, is read with them.

        Returns:
            Them, or ``None`` while no chain is followed, none of
            ``transactions`` is the best, or no stake is covered.

        Raises:
            MissingContentError: names the blocks within reach that the
                chain lacks.
        """
        head = self._history.head

        if head is None:
            return None

        self._at(head.validation)
        chosen = self._validated(transactions)

        if chosen is None:
            return None

        validated_id, validated = chosen
        offered = self._offered(validated_id, GenerationTargets.after(head.validation).validation)
        stakers = {identity.node_id, *also, *(signed.staker for signed in offered)}
        cover = StakeCover.of(validated, stakers, self._chain)

        if not any(signed.staker == identity.node_id for signed in offered):
            offered.extend(self._own_stake(validated_id, cover, identity))

        stakes = cover.taken(offered)
        return StakedBlock(validated_id, cover, stakes) if stakes else None

    def _validated(
        self, transactions: Mapping[ContentId, TransactionBlockCheck]
    ) -> tuple[ContentId, TransactionBlock] | None:
        """The best scored of ``transactions`` that is correct, and no distrusted identity built.

        Of two as good, it is the one whose id sorts first (§5.5).
        """
        scored = [
            (check.score, block_id, check.drop.block)
            for block_id, check in transactions.items()
            if check.score is not None and not self._distrusts(check.drop.builder)
        ]

        if not scored:
            return None

        top = max(score for score, _, _ in scored)
        _, block_id, block = min(
            (each for each in scored if each[0] == top), key=lambda each: each[1]
        )
        return block_id, block

    def _at(self, head: ContentId | None) -> None:
        """Forget what was found at the head before, and the stakes given, if ``head`` is new."""
        if head != self._head:
            self._head = head
            self._own.clear()
            self._passed_over.clear()
            self._given.clear()

    def _offered(self, validated_id: ContentId, target: DropTarget) -> list[SignedStake]:
        """The stakes on ``validated_id`` held at ``target``, and those given, once each.

        Those held are in the validation blocks there. Each is signed by its
        staker, whom the node does not distrust (§3.8). One whose staker's
        key is not held is passed over, and the key asked for. One whose
        signature fails is passed over, and logged once at a head. A block
        that is no validation block holds none.
        """
        offered: dict[str, SignedStake] = {}

        for held in self._reader.read(target, ValidationBlock):
            try:
                drop = BlockDrop.parsed(held.data, ValidationBlock)

            except (BundleError, ValueError) as error:
                # Not logged as a warning: the chain followed checks each block
                # at the target.
                _LOGGER.debug("Karma block %s holds no stakes: %s", held.block_id, error)
                continue

            self._offer(offered, validated_id, drop.block.stakes, held.keys)

        given = [
            signed
            for signed in self._given.values()
            if signed.stake.transaction_block == validated_id
        ]
        keys = self._reader.keys(
            {signed.staker for signed in given}, named_by="a stake made through the node"
        )
        self._offer(offered, validated_id, given, keys)
        return list(offered.values())

    def _offer(
        self,
        offered: dict[str, SignedStake],
        validated_id: ContentId,
        stakes: Iterable[SignedStake],
        keys: Mapping[ContentId, bytes],
    ) -> None:
        """Add to ``offered`` each of ``stakes`` on ``validated_id`` it lacks that may be taken.

        Each is signed by its staker, whom the node does not distrust, with
        their key, of ``keys``.
        """
        for signed in stakes:
            if (
                signed.stake.transaction_block == validated_id
                and signed.hash not in offered
                and not self._distrusts(signed.staker)
                and self._verified(signed, keys)
            ):
                offered[signed.hash] = signed

    def _verified(self, signed: SignedStake, keys: Mapping[ContentId, bytes]) -> bool:
        """Whether ``signed``'s signature checks with its staker's key, of ``keys``."""
        key = keys.get(signed.staker)

        if key is None:
            # Not logged: the reader asks for it, and the stake is taken once
            # it comes.
            return False

        try:
            signed.verified(key)

        except BundleSignatureError as error:
            if signed.hash not in self._passed_over:
                self._passed_over.add(signed.hash)
                _LOGGER.debug(
                    "Passing over Karma stake %s, whose signature must check: %s",
                    signed.hash,
                    error,
                )

            return False

        return True

    def _own_stake(
        self, validated_id: ContentId, cover: StakeCover, identity: NodeIdentity
    ) -> list[SignedStake]:
        """``identity``'s stake on ``validated_id``, signed once at a head, if it is more than 0."""
        if validated_id not in self._own:
            covering = cover.covering(identity.node_id)
            percent = self._stake_percent()
            kismet = covering.percent(percent)

            if kismet == Kismet(0):
                _LOGGER.info(
                    "Not staking on Karma transaction block %s: %s%% of the %s Kismet that "
                    "cover the node is none",
                    validated_id,
                    percent,
                    covering.value(),
                )
                self._own[validated_id] = None

            else:
                created = moment_at(self._clock())
                stake = Stake(identity.node_id, validated_id, kismet, created)
                self._own[validated_id] = SignedStake.of(identity.signed(stake.text()))
                _LOGGER.info(
                    "Staking %s of the %s Kismet that cover the node on Karma transaction "
                    "block %s",
                    kismet.value(),
                    covering.value(),
                    validated_id,
                )

        own = self._own[validated_id]
        return [] if own is None else [own]


class ValidationBuilder:
    """Builds a validation block of the stakes on the best block at the head's target (§6).

    The chain is the one ``history`` keeps, whose blocks at the head's
    targets ``follower`` checks, read from what ``reader`` holds, and what is
    carried is in ``carried``. The stakes are taken as :class:`HeadStakes`
    takes them, of the share ``stake_percent`` gives, as ``distrusts`` says
    whom the node distrusts, at the time ``clock`` gives. A block is signed,
    placed, and stored as ``placing`` says. It fits within ``bounds``: the
    specification's, unless a test gives others.
    """

    def __init__(
        self,
        history: ChainHistory,
        follower: ChainFollower,
        reader: TargetReader,
        carried: CarriedTransactions,
        placing: BlockPlacing,
        *,
        distrusts: Callable[[ContentId], bool],
        stake_percent: Callable[[], float],
        clock: Callable[[], float] = time,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._history = history
        self._follower = follower
        self._carried = carried
        self._placing = placing
        self._bounds = bounds
        self._chain = HeldChain(history, reader)
        self._stakes = HeadStakes(
            history, reader, distrusts=distrusts, stake_percent=stake_percent, clock=clock
        )

    def take(self, signed: SignedStake) -> None:
        """Keep ``signed``, a stake a person made through the node, for the blocks built.

        It is kept until the head changes (:meth:`HeadStakes.take`).
        """
        self._stakes.take(signed)

    def build(self) -> None:
        """Build a validation block at the head's validation target, if one is to be.

        Nothing is built while no chain is followed, no stake is taken
        (:meth:`HeadStakes.staked`), or a correct validation block held at
        the head's validation target holds every stake taken. A block of the
        chain it lacks is asked for, and it is built as this is next called;
        one that cannot be built for an error of the system, or because what
        it is built of cannot be read, is logged.
        """
        head = self._history.head

        if head is None:
            return

        try:
            self._build(GenerationTargets.after(head.validation).validation)

        except MissingContentError:
            # Not logged: what it lacks is asked for, and it is built once
            # that comes.
            return

        except OSError as error:
            _LOGGER.error("Cannot build a Karma validation block: %s", error)

        except (BundleError, ValueError) as error:
            _LOGGER.warning("Cannot build a Karma validation block: %s", error)

    def _build(self, target: DropTarget) -> None:
        """Build a validation block at ``target``, the head's validation target, if one is to be.

        Raises:
            MissingContentError: names the blocks of the chain it lacks.
            BundleError: what it is built of cannot be read, or it is larger
                than an object may be, even compressed.
            ValueError: what it is built of cannot be read, or its nonce
                cannot be searched for as asked.
            OSError: the node's key cannot be loaded, a searching process
                could not be started, or exited during the search, or the
                drop could not be written.
        """
        transactions, validations = self._follower.checked()
        identity = self._placing.identity()
        staked = self._stakes.staked(
            transactions,
            identity,
            also={signed.staker for check in validations for signed in check.drop.block.stakes},
        )

        if staked is None or self._held(staked.stakes, validations):
            return

        validated = staked.cover.validated
        block = ValidationBlock(validated.generation, None, staked.stakes, ())
        rivals = self._rivals(staked.validated_id, validations, target)
        drop = self._filled(
            replace(block, runner_up=staked.cover.runner_up(block, identity.node_id, rivals)),
            self._witnesses(validated, transactions),
            identity,
        )

        if drop is None:
            _LOGGER.warning(
                "Building no Karma validation block on %s: its %d stakes do not fit in %d bytes",
                staked.validated_id,
                len(staked.stakes),
                self._bounds.most_bytes,
            )
            return

        self._place(drop, target, staked.validated_id)

    @staticmethod
    def _held(stakes: Iterable[SignedStake], validations: Iterable[ValidationBlockCheck]) -> bool:
        """Whether one of ``validations`` that is correct holds every one of ``stakes``."""
        hashes = {signed.hash for signed in stakes}
        return any(
            hashes <= {signed.hash for signed in check.drop.block.stakes}
            for check in validations
            if check.is_correct
        )

    @staticmethod
    def _rivals(
        validated_id: ContentId, validations: Iterable[ValidationBlockCheck], target: DropTarget
    ) -> list[ValidationBlockCheck]:
        """The validation blocks of ``validations``, held at ``target``, that may be a runner-up.

        Those are the ones validating another transaction block than
        ``validated_id``: the correct ones first, the best first, and then
        the rest, by the most bits they share with ``target``; of as good,
        the one whose id sorts first (§6.5).
        """

        def rank(check: ValidationBlockCheck) -> tuple[bool, int, int, ContentId]:
            score = check.score or ValidationScore(
                matching_bits(target.hex, check.block_id.hash), 0
            )
            return (not check.is_correct, -score.bits, -score.stake, check.block_id)

        return sorted(
            (
                check
                for check in validations
                if check.drop.block.stakes[0].stake.transaction_block != validated_id
            ),
            key=rank,
        )

    def _witnesses(
        self, validated: TransactionBlock, transactions: Mapping[ContentId, TransactionBlockCheck]
    ) -> list[str]:
        """The hashes a block validating ``validated`` lists, those of most worth first (§6.4).

        Those of the transactions carried come first, and then those of the
        transactions in ``transactions``, the blocks held at the head's
        transaction target, sorted, each but those ``validated`` holds, and
        those a winning validation block of the chain lists.
        """
        holds = {signed.hash for signed in validated.signed}
        carried = [each.signed.hash for each in self._carried.readable_at(self._history)]
        seen = sorted(
            {signed.hash for check in transactions.values() for signed in check.drop.block.signed}
        )
        return [
            witness
            for witness in dict.fromkeys([*carried, *seen])
            if witness not in holds and self._chain.witnessed(witness) is None
        ]

    def _filled(
        self, block: ValidationBlock, witnesses: Sequence[str], identity: NodeIdentity
    ) -> BlockDrop[ValidationBlock] | None:
        """``block`` listing the most of ``witnesses``, in order, that fit, signed by ``identity``.

        It fits when its drop measures no more than the bounds allow
        (:meth:`~libranet.karma.blocks.BlockBounds.fullest`).

        Returns:
            The block, or ``None`` if it does not fit listing none.
        """

        def listing(count: int) -> BlockDrop[ValidationBlock]:
            listed = replace(block, witnessed=tuple(sorted(witnesses[:count])))
            return BlockDrop(identity.signed(listed.text()), listed)

        fullest = self._bounds.fullest(listing, len(witnesses))
        return None if fullest is None else fullest[0]

    def _place(
        self, drop: BlockDrop[ValidationBlock], target: DropTarget, validated_id: ContentId
    ) -> None:
        """Place ``drop``, validating ``validated_id``, at ``target``, and store it.

        Raises:
            BundleError: it is larger than an object may be, even compressed.
            ValueError: its nonce cannot be searched for as asked.
            OSError: a searching process could not be started, or exited
                during the search, or the drop could not be written.
        """
        placed = self._placing.placed(drop, target)
        _LOGGER.info(
            "Built Karma validation block %s of %d stakes and %d witnesses, validating %s, "
            "naming %s its runner-up, sharing %d bits with its target",
            ContentId.for_data(placed.data, HASH_ALGORITHM),
            len(drop.block.stakes),
            len(drop.block.witnessed),
            validated_id,
            drop.block.runner_up,
            placed.matching_bits,
        )
