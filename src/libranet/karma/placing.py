"""Placing the blocks the node builds, and storing them (KarmaLedger §2, Phase 6 Steps 131-136).

A block the node puts up or builds, a transaction's block of its own, a
combined transaction block, or a validation block, is signed as the node,
its drop's nonce searched for for ``karma.block_seconds``, and after that
until it shares ``storage.search_min_bits`` with its target (§2), and it is
stored and announced, as the root the module makes is.
"""

from __future__ import annotations
from dataclasses import KW_ONLY, dataclass
from typing import Any, Callable

from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, Drop, DropTarget, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop


@dataclass(frozen=True)
class BlockPlacing:
    """How the node signs, places, and stores each block it builds.

    A block is signed as ``identity`` gives the node, its drop's nonce
    searched for by ``searchers`` for ``block_seconds``, and after that until
    it shares ``drop_bits`` with its target, and handed to ``store``, with
    its builder's id, to be stored and announced.
    """

    identity: Callable[[], NodeIdentity]
    store: Callable[[bytes, ContentId], None]
    _: KW_ONLY
    block_seconds: float
    drop_bits: int
    searchers: NonceSearchers = NONCE_SEARCHERS

    def placed(self, drop: BlockDrop[Any], target: DropTarget) -> Drop:
        """``drop`` placed at ``target``, and stored.

        Raises:
            BundleError: it is larger than an object may be, even compressed.
            ValueError: its nonce cannot be searched for as asked.
            OSError: a searching process could not be started, or exited
                during the search, or the drop could not be written.
        """
        placed = target.placed(
            drop.content(),
            self.block_seconds,
            minimum_bits=self.drop_bits,
            searchers=self.searchers,
        )
        self.store(placed.data, drop.builder)
        return placed
