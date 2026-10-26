"""Staking on the best block at the head's target (KarmaLedger §6.2, §6.3, Phase 6 Step 136).

Once a full, correct transaction block is held at the head's transaction
target, the Karma module stakes on it, and takes the stakes a validation
block on it holds, so that the chain goes on (§3.1). It reads the blocks
held at the head's two targets, checked as the chain followed checks them
(Phase 6 Step 126), and takes the best scored transaction block there that
is correct, and that no identity the node distrusts signed (§3.8, §5.5); of
two as good, the one whose id sorts first.

What covers an identity's stakes on that block is its Kismet as the block
leaves it, less the fees of its transactions there, which the next block's
payout takes (§6.3). The node stakes ``karma.stake_percent`` of what covers
it, rounded down to whole Kismet, and nothing while that is 0. It signs its
stake once at a head, and none if a validation block held at the head's
validation target holds one of its own on the block, as one it built before
it was started again does. Others' stakes are those on the same block in the
validation blocks held there, each whose signature checks with its staker's
key, but those of every staker the node distrusts (§3.8). Of each staker's,
the largest are taken first, while what covers the staker covers them
together.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from logging import getLogger
from time import time
from typing import Callable, Iterable, Mapping

from libranet.bundle.errors import BundleError, BundleSignatureError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import Balance, BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.checking import ChainView, TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.validating import balances_left

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


@dataclass(frozen=True)
class StakedBlock:
    """The stakes a validation block on transaction block ``validated_id`` takes.

    ``cover`` is what covers them, and holds that block. ``stakes`` are
    sorted by hash, and at least one (§6.1).
    """

    validated_id: ContentId
    cover: StakeCover
    stakes: tuple[SignedStake, ...]


class HeadStakes:  # pylint: disable=too-many-instance-attributes
    """Stakes on the best transaction block at the head's target, and takes others' stakes on it.

    The chain is the one ``history`` keeps, its blocks read from what
    ``reader`` holds. Whom the node distrusts, ``distrusts`` says (§3.8).
    The node stakes ``stake_percent`` of what covers it, its stake created at
    the time ``clock`` gives, in seconds since the epoch.
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        *,
        distrusts: Callable[[ContentId], bool],
        stake_percent: float,
        clock: Callable[[], float] = time,
    ) -> None:
        self._history = history
        self._reader = reader
        self._distrusts = distrusts
        self._stake_percent = stake_percent
        self._clock = clock
        self._chain = HeldChain(history, reader)
        # The head these were found at: the node's stake on each transaction
        # block, or None where it stakes nothing, and the stakes whose
        # signatures fail, each logged once.
        self._head: ContentId | None = None
        self._own: dict[ContentId, SignedStake | None] = {}
        self._passed_over: set[str] = set()

    def staked(
        self,
        transactions: Mapping[ContentId, TransactionBlockCheck],
        identity: NodeIdentity,
        also: Iterable[ContentId] = (),
    ) -> StakedBlock | None:
        """The stakes a validation block takes on the best of ``transactions``, or ``None``.

        ``transactions`` are the transaction blocks held at the head's
        transaction target, as the chain followed checks them, and the best
        is the best scored that is correct, and that no distrusted identity
        built (:meth:`_validated`). The stakes are those on it held at the
        head's validation target (:meth:`_offered`), and ``identity``'s, the
        node's (:meth:`_own_stake`), each staker's covered together
        (:meth:`StakeCover.taken`). What covers ``also``, whose stakes a
        runner-up may hold, is read with them.

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

        if head.validation != self._head:
            self._head = head.validation
            self._own.clear()
            self._passed_over.clear()

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

    def _offered(self, validated_id: ContentId, target: DropTarget) -> list[SignedStake]:
        """The stakes on ``validated_id`` in the validation blocks held at ``target``, once each.

        Each is signed by its staker, whom the node does not distrust (§3.8).
        One whose staker's key is not held is passed over, and the key asked
        for. One whose signature fails is passed over, and logged once at a
        head. A block that is no validation block holds none.
        """
        offered: dict[str, SignedStake] = {}

        for held in self._reader.read(target, ValidationBlock):
            try:
                drop = BlockDrop.parsed(held.data, ValidationBlock)

            except (BundleError, ValueError) as error:
                # Not logged as a warning: the chain followed checks each block at the target.
                _LOGGER.debug("Karma block %s holds no stakes: %s", held.block_id, error)
                continue

            for signed in drop.block.stakes:
                if (
                    signed.stake.transaction_block == validated_id
                    and signed.hash not in offered
                    and not self._distrusts(signed.staker)
                    and self._verified(signed, held.keys)
                ):
                    offered[signed.hash] = signed

        return list(offered.values())

    def _verified(self, signed: SignedStake, keys: Mapping[ContentId, bytes]) -> bool:
        """Whether ``signed``'s signature checks with its staker's key, of ``keys``."""
        key = keys.get(signed.staker)

        if key is None:
            # Not logged: the reader asks for it, and the stake is taken once it comes.
            return False

        try:
            signed.verified(key)

        except BundleSignatureError as error:
            if signed.hash not in self._passed_over:
                self._passed_over.add(signed.hash)
                _LOGGER.warning(
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
            kismet = Kismet(covering.count * Fraction(str(self._stake_percent)) // 100)

            if kismet == Kismet(0):
                _LOGGER.info(
                    "Not staking on Karma transaction block %s: %s%% of the %s Kismet that "
                    "cover the node is none",
                    validated_id,
                    self._stake_percent,
                    covering.value(),
                )
                self._own[validated_id] = None

            else:
                created = datetime.fromtimestamp(int(self._clock()), UTC)
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
