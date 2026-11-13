"""Every holder's balance, written each time the head changes (KarmaLedger §5.3, Phase 6 Step 128).

An identity's Karma, to a node, is its balance as the chain the node follows
states it, with the head's reward paid (§2). Each time the head of the chain
the Karma module follows changes, every balance stated as the head's
transaction block leaves it
(:meth:`~libranet.karma.reading.HeldChain.every_balance`), with the payout
of the block after applied
(:meth:`~libranet.karma.checking.ChainBefore.paid`), is written to
``balances.json``, with the head it was read at
(:class:`~libranet.protocol.karma_balances.KarmaBalances`), for the web
server, and any other module, to read. While no chain is followed, it names
no head, and states no balance.

A block the head's balances are read from that is not held is asked for, and
they are written once it comes.
"""

from __future__ import annotations
from pathlib import Path

from libranet.cas.content_id import ContentId
from libranet.karma.checking import ChainBefore
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain
from libranet.protocol.karma_balances import Balance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead


class HeadBalances:
    """Writes every balance ``chain`` states at the head of ``history`` into ``directory``.

    ``chain`` is the chain ``history`` keeps.
    """

    def __init__(self, history: ChainHistory, chain: HeldChain, directory: Path) -> None:
        self._history = history
        self._chain = chain
        self._directory = directory
        # The head the balances were last written at, None before they are.
        self._written: KarmaHead | None = None

    def write(self) -> bool:
        """Write the balances stated at the head, unless they were written at that head.

        Returns:
            Whether they were written now.

        Raises:
            MissingContentError: names each block they are read from that is
                not held, each asked for; nothing is written.
            ShunnedContentError: the node shuns one of those blocks.
            BundleError: what is held of one is not it, or holds no signed
                bundle.
            ValueError: what one signs is no transaction block, or the
                payout takes more from an identity than it holds.
            OSError: they could not be written, which leaves those before.
        """
        head = self._history.head
        karma_head = KarmaHead() if head is None else head.karma_head

        if karma_head == self._written:
            return False

        stated: dict[ContentId, Balance] = {}

        if head is not None:
            stated = self._chain.every_balance(head.generation)
            stated.update(ChainBefore.read(self._chain, head.validation).paid().after)

        KarmaBalances(karma_head, stated).write(self._directory)
        self._written = karma_head
        return True
