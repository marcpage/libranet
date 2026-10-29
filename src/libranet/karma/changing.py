"""Changing to a heavier chain (KarmaLedger §3.5.3, Phase 6 Step 129).

A head the node does not follow, a validation block, is weighed, and the
chain through it followed if it is heavier than the chain followed. Such a
head is found at a target: another validation block at the target the head
was chosen from, as one placed after the head's generation was followed is
(Phase 6 Step 126), or another root while the root is the head. Or a peer
tells of it (:meth:`HeadChanges.told`, Phase 6 Step 130), the latest each
peer told kept until it is come to. The heads are weighed one at a time, in
the six steps of §3.5.3 (:class:`~libranet.karma.claims.HeadClaim`):

1. **The claim is read**: the head, and the transaction block it validates,
   whose ``weight``, with 2^b for each of the two drops, is what the chain
   through them claims to weigh. Unless that is more than the chain
   followed weighs, nothing more is read. One only as heavy is not
   followed, so that the node keeps the head it chose as the best (§6.5).
2. **It walks back** to the common ancestor, checking each ``weight`` and
   ``generation`` as it goes.
3. **The chain followed after the ancestor is collected**: each transaction
   of its transaction blocks, payouts aside, with the generation it was
   witnessed in (§4.5).
4. **The new chain is checked forward** from the ancestor.
5. **It is followed** if every block in it is correct, and it is heavier
   still than the chain followed: the history after the ancestor is
   replaced by it.
6. **What is left over is carried**: each transaction collected whose key
   the new chain does not hold (:mod:`libranet.karma.carried`).

What a step lacks, a block or a key, is asked for, and the head is weighed
on once it comes, or given up on with it (:meth:`HeadChanges.given_up`). A
block found unsigned, or stating its ``generation`` or ``weight`` falsely,
is no block of the ledger, and is shunned (§3.7); one found false is said,
for its builder to be distrusted (Phase 6 Step 127)::

    data.shunned       {"algorithm", "hash"}
    karma.false_block  {"block", "builder", "staker", "statement"}

Then, and for a block that is not correct, the head followed is kept. Not
even the part of the chain before that block is followed (§9): a later head
past it is weighed again, and found the same. What is found of each block,
and each head weighed, is kept while the head followed is the same.
"""

from __future__ import annotations
from logging import getLogger
from typing import Collection, Sequence

from libranet.bundle.errors import BundleError, BundleSignatureError, MissingContentError
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds, BlockFinding, ValidationBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.claims import ClaimedChain, FalseStatement, HeadClaim
from libranet.karma.following import say_found
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.validating import ValidationBlockCheck
from libranet.karma.weight import ChainWeight
from libranet.messaging.events import EventType
from libranet.messaging.publishing import Publish
from libranet.protocol.karma_carried import CarriedTransaction

_LOGGER = getLogger(__name__)


class HeadChanges:  # pylint: disable=too-many-instance-attributes
    """Weighs the heads ``history`` does not follow, and follows a heavier one (§3.5.3).

    What it finds of a block is said with ``publish``, the blocks are read
    from what ``reader`` holds, each checked against ``bounds``, the
    specification's unless a test gives less, and what a chain left holds
    that the new one does not is kept in ``carried``.
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        publish: Publish,
        carried: CarriedTransactions,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._history = history
        self._reader = reader
        self._publish = publish
        self._carried = carried
        self._bounds = bounds
        self._chain = HeldChain(history, reader)
        # The head these were found at, and what was found of each block,
        # the heads found at a target and those told by each peer, and each
        # weighed, while the head is the same.
        self._head = self._head_id()
        self._found: dict[ContentId, BlockFinding] = {}
        self._queue: list[ContentId] = []
        self._told: dict[ContentId, ContentId] = {}
        self._weighed: set[ContentId] = set()
        self._claim: HeadClaim | None = None

    def told(self, node_id: ContentId, head: ContentId) -> None:
        """Weigh ``head``, as peer ``node_id`` told of it, in place of any it told before."""
        self._told[node_id] = head

    def given_up(self, content_ids: Collection[ContentId]) -> None:
        """Weigh no more the head being weighed, if it waits for any of ``content_ids``.

        Those have not come, and are given up on. It is weighed again once
        the head followed changes.
        """
        claim = self._claim

        if claim is not None and not claim.lacking.isdisjoint(content_ids):
            _LOGGER.info(
                "Not weighing Karma head %s further: what it needs has not come", claim.head
            )
            self._done(claim)

    def look(self) -> bool:
        """Weigh the heads not followed, one at a time, and follow the first that is heavier.

        Those at the target the head was chosen from are weighed first, then
        those peers told of, and the one being weighed waits for what it
        lacks.

        Returns:
            Whether a heavier chain is followed now.

        Raises:
            OSError: the history could not be written, as
                :meth:`~libranet.karma.history.ChainHistory.replace` says.
        """
        self._note_head()
        self._find()

        while (claim := self._next()) is not None:
            try:
                followed = self._weigh(claim)

            except MissingContentError as error:
                # Not logged: what it lacks is asked for, and it is weighed on
                # once that comes.
                claim.lacking = frozenset(error.content_ids)
                return False

            except (BundleError, ValueError) as error:
                _LOGGER.warning(
                    "Not following the Karma chain through %s, which cannot be checked: %s",
                    claim.head,
                    error,
                )
                followed = False

            self._done(claim)

            if followed:
                return True

        return False

    def _head_id(self) -> ContentId | None:
        """The head followed, or ``None`` while none is."""
        head = self._history.head
        return None if head is None else head.validation

    def _note_head(self, collected: Sequence[CarriedTransaction] = ()) -> None:
        """Forget what was found while the head was another, and carry ``collected`` on, if it is.

        What is carried already is carried on with it.
        """
        head = self._head_id()

        if head == self._head:
            return

        self._head = head
        self._found.clear()
        self._weighed.clear()
        self._carry(collected)

    def _find(self) -> None:
        """Weigh the validation blocks at the target the head was chosen from, but the head."""
        head = self._history.head

        if head is None:
            return

        named_for = (
            None
            if head.generation == 0
            else self._history.generation(head.generation - 1).validation
        )
        target = GenerationTargets.built_on(named_for).validation

        for held in self._reader.read(target, ValidationBlock):
            if held.block_id != head.validation and held.block_id not in self._queue:
                self._queue.append(held.block_id)

    def _next(self) -> HeadClaim | None:
        """The head being weighed, or else the next to weigh, or ``None`` if there is none."""
        head = self._head_id()

        while self._claim is None and (self._queue or self._told):
            taken = self._queue.pop(0) if self._queue else self._told.pop(next(iter(self._told)))

            if taken not in self._weighed and taken != head:
                self._claim = HeadClaim(taken)

        return self._claim

    def _done(self, claim: HeadClaim) -> None:
        """Weigh ``claim`` no more while the head is the same."""
        self._weighed.add(claim.head)
        self._claim = None

    def _heavier(self, weight: ChainWeight) -> bool:
        """Whether a chain weighing ``weight`` is heavier than the one followed."""
        head = self._history.head
        return head is None or weight > head.weight

    def _weigh(self, claim: HeadClaim) -> bool:
        """Weigh ``claim`` on, as far as what is held lets it, and follow it if it is heavier.

        Returns:
            Whether it is followed now.

        Raises:
            MissingContentError: names what it lacks, which is asked for.
            BundleError: a block of it is shunned, or cannot be checked here.
            ValueError: it names a block that is no block of its kind, or
                an id under a hash algorithm this node lacks.
            OSError: the history could not be written.
        """
        claimed = claim.claimed(self._chain)

        if isinstance(claimed, FalseStatement):
            return self._not_following(claim, self._said(claimed))

        if not self._heavier(claimed.through):
            _LOGGER.debug(
                "Not following the Karma chain through %s, claiming to weigh %s, no more than "
                "the chain followed",
                claim.head,
                claimed.through.value(),
            )
            return False

        chain = claim.walk(self._history, self._chain)

        if isinstance(chain, FalseStatement):
            return self._not_following(claim, self._said(chain))

        failed = claim.check(chain, self._reader, bounds=self._bounds)

        if failed is not None:
            return self._not_following(claim, self._why(*failed))

        self._follow(chain)
        return True

    def _why(
        self,
        block_id: ContentId,
        found: TransactionBlockCheck | ValidationBlockCheck | BlockFinding,
    ) -> str:
        """Why a chain holding block ``block_id``, ``found`` so, is not followed.

        What it states falsely, or that it is unsigned, is said once.
        """
        if not isinstance(found, BlockFinding):
            return f"{block_id} is not correct: {found.incorrect}"

        if block_id not in self._found:
            self._found[block_id] = found
            say_found(self._publish, block_id, found)

        return f"{block_id} is not taken: {found.statement}"

    def _said(self, false: FalseStatement) -> str:
        """Why a chain holding ``false``'s block is not followed, said once its signature checks.

        A block whose signature fails is unsigned, and shunned, and one
        signed that is ``invalid`` is shunned as well as said false (§3.7).

        Raises:
            MissingContentError: names the block, or its builder's key,
                which is not held, and is asked for.
            BundleError: either is shunned, or what is held of it is not it.
        """
        found = self._found.get(false.block_id)

        if found is not None:
            return self._why(false.block_id, found)

        # The walk read it as a block, so its drop holds a signed bundle.
        content = self._reader.held(false.block_id).rpartition(DROP_SEPARATOR)[0]
        bundle = SignedBundle.decoded(content)

        try:
            bundle.verified(self._reader.held(bundle.signer))
            found = BlockFinding(false.statement, bundle.signer)

        except BundleSignatureError as error:
            # Not logged: it is the finding, which is logged as it is said.
            found = BlockFinding(f"A block's signature must check: {error}")

        why = self._why(false.block_id, found)

        if found.builder is not None and false.invalid:
            _LOGGER.warning(
                "Shunning Karma block %s, no block of the ledger: %s",
                false.block_id,
                false.statement,
            )
            self._publish(EventType.DATA_SHUNNED, false.block_id.fields())

        return why

    def _not_following(self, claim: HeadClaim, why: str) -> bool:
        """Log that ``claim`` is not followed, for ``why``.

        Returns:
            ``False``, as it is not followed.
        """
        _LOGGER.info("Not following the Karma chain through %s: %s", claim.head, why)
        return False

    def _follow(self, chain: ClaimedChain) -> None:
        """Follow ``chain``, checked, after its ancestor, and carry what is left over.

        Raises:
            OSError: the history could not be written.
        """
        left = self._history.head
        collected = self._collected(chain.ancestor)
        self._history.replace(chain.generations)
        head = chain.generations[-1]
        _LOGGER.info(
            "Following the heavier Karma chain through generation %d, head %s, weighing %s, "
            "in place of the chain after generation %d through %s",
            head.generation,
            head.validation,
            head.weight.value(),
            chain.ancestor,
            None if left is None else left.validation,
        )
        self._note_head(collected)

    def _collected(self, ancestor: int) -> list[CarriedTransaction]:
        """Each transaction of the chain followed after generation ``ancestor``, payouts aside.

        Each is witnessed in the generation of its block, or of the chain's
        winning validation block listing it, if that is earlier (§4.5). A
        block not held, or that cannot be read, has none carried, and that is
        logged.
        """
        collected: list[CarriedTransaction] = []

        for generation in range(ancestor + 1, len(self._history)):
            block_id = self._history.generation(generation).transaction

            try:
                block = self._chain.transaction(block_id).block

            except (BundleError, ValueError) as error:
                _LOGGER.warning(
                    "Carrying over none of the transactions of Karma block %s, which cannot be "
                    "read: %s",
                    block_id,
                    error,
                )
                continue

            for signed in block.signed:
                witnessed = self._history.witnessed(signed.hash)
                earliest = generation if witnessed is None else min(witnessed, generation)
                collected.append(CarriedTransaction(signed, earliest))

        return collected

    def _carry(self, collected: Sequence[CarriedTransaction]) -> None:
        """Carry ``collected``, and what is carried, at the head followed, and log it.

        A file that cannot be read or written is logged, and carried again as
        the head next changes.
        """
        try:
            carried = self._carried.carry(collected, self._history)

        except OSError as error:
            _LOGGER.error("Cannot carry over Karma transactions: %s", error)
            return

        if collected:
            _LOGGER.info(
                "Carrying %d Karma transactions over from the chain left, of %d collected",
                len(carried),
                len(collected),
            )
