"""Placing the blocks the node builds, and storing them (KarmaLedger §2, Phase 6 Steps 131-136).

A block the node puts up or builds, a transaction's block of its own, a
combined transaction block, or a validation block, is signed as the node,
its drop's nonce searched for until it shares ``storage.search_min_bits``
with its target (§2), and it is stored and announced, as the root the module
makes is. One whose bits score it (§5.5, §6.5), a full transaction block or a
validation block, is searched for ``karma.block_seconds`` first, for more of
them, and one that is only to be found is not. One whose drop is
content the node shuns is not stored: signing and the search for a nonce
are alike each time, so a block built again of what it was built of before
is the same drop, though the node has let it go.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from logging import getLogger
from typing import Any, Callable

from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, Drop, DropTarget, NonceSearchers
from libranet.cas.shunned import ShunnedContent
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class BlockPlacing:
    """How the node signs, places, and stores each block it builds.

    A block is signed as ``identity`` gives the node, its drop's nonce
    searched for by ``searchers``, for ``block_seconds`` if its bits score it,
    and after that until it shares ``drop_bits`` with its target, and handed
    to ``store``, with
    its builder's id, to be stored and announced, unless ``shunned`` names
    it.
    """

    identity: Callable[[], NodeIdentity]
    store: Callable[[bytes, ContentId], None]
    _: KW_ONLY
    shunned: ShunnedContent
    block_seconds: float
    drop_bits: int
    searchers: NonceSearchers = NONCE_SEARCHERS

    def placed(
        self, drop: BlockDrop[Any], target: DropTarget, *, scored: bool = True
    ) -> Drop | None:
        """``drop`` placed at ``target``, and stored, or ``None`` if the node shuns it.

        It is searched for ``block_seconds`` if ``scored``: more bits rank it
        above its rivals. Otherwise, only until a search will find it.

        Returns:
            The drop placed, or ``None`` if it is content the node shuns,
            which is not stored.

        Raises:
            BundleError: it is larger than an object may be, even compressed.
            ValueError: its nonce cannot be searched for as asked.
            OSError: a searching process could not be started, or exited
                during the search, or the drop could not be written.
        """
        placed = target.placed(
            drop.content(),
            self.block_seconds if scored else 0.0,
            minimum_bits=self.drop_bits,
            searchers=self.searchers,
        )

        placed_id = ContentId.for_data(placed.data, HASH_ALGORITHM)

        if self.shunned.shuns(placed_id):
            _LOGGER.info("Not storing Karma block %s, built again, which the node shuns", placed_id)
            return None

        self.store(placed.data, drop.builder)
        return placed
