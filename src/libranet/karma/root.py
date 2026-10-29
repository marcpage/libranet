"""Making a chain's root, which anyone may place (KarmaLedger §7, Phase 6 Step 125).

A root is two blocks, each signed by its creator: transaction block 0,
placed at ``karma transaction:0``, holding only the root transaction, in
which the creator sends themselves 0, and stating them holding 0, having
won nothing; and validation block 0, placed at ``karma validation:0``,
holding only the creator's stake of 0 on it (§7.1 to §7.3). The root
transaction is mined to as many bits as a valid transaction holds (§4.3),
and each drop's nonce searched for until the drop shares as many with its
target as a search returns (§2).

Nothing here stores it, nor follows it: the Karma module does.
"""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.cas.drops import NONCE_SEARCHERS, Drop, NonceSearchers
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import NO_BALANCE
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import (
    MIN_TRANSACTION_BITS,
    SignedTransaction,
    Transaction,
)


@dataclass(frozen=True)
class ChainRoot:
    """A chain's root: the drops of ``transaction`` block 0 and ``validation`` block 0.

    ``generation`` is what the history of a chain on it keeps of it: its
    generation 0 (:meth:`~libranet.karma.history.ChainHistory.add`).
    """

    transaction: Drop
    validation: Drop
    generation: ChainGeneration

    @classmethod
    def made(
        cls,
        creator: NodeIdentity,
        created: datetime,
        *,
        drop_bits: int,
        transaction_bits: int = MIN_TRANSACTION_BITS,
        searchers: NonceSearchers = NONCE_SEARCHERS,
    ) -> ChainRoot:
        """The root ``creator`` makes, its transaction and its stake made at ``created`` (§7).

        Its transaction is mined until it shares at least
        ``transaction_bits`` with the target of its work, and each drop's
        nonce searched for until the drop shares at least ``drop_bits`` with
        its target, each by ``searchers``.

        Raises:
            ValueError: ``created`` is not a UTC time to the second, or
                ``drop_bits`` or ``transaction_bits`` is negative or more
                than a hash has.
            OSError: a searching process could not be started, or exited
                during the search.
        """
        targets = GenerationTargets.root()
        transaction_block = cls._transaction_block(creator, created, transaction_bits, searchers)
        transaction = targets.transaction.placed(
            BlockDrop(creator.signed(transaction_block.text()), transaction_block).content(),
            0,
            minimum_bits=drop_bits,
            searchers=searchers,
        )
        transaction_id = ContentId.for_data(transaction.data, HASH_ALGORITHM)
        stake = Stake(creator.node_id, transaction_id, Kismet(0), created)
        staked = SignedStake.of(creator.signed(stake.text()))
        validation_block = ValidationBlock(0, None, (staked,), ())
        validation = targets.validation.placed(
            BlockDrop(creator.signed(validation_block.text()), validation_block).content(),
            0,
            minimum_bits=drop_bits,
            searchers=searchers,
        )
        validation_id = ContentId.for_data(validation.data, HASH_ALGORITHM)
        return cls(
            transaction,
            validation,
            ChainGeneration.of(validation_id, validation_block, transaction_id, transaction_block),
        )

    @staticmethod
    def _transaction_block(
        creator: NodeIdentity, created: datetime, transaction_bits: int, searchers: NonceSearchers
    ) -> TransactionBlock:
        """Transaction block 0, holding only ``creator``'s root transaction, made at ``created``.

        The transaction is mined by ``searchers`` until it shares at least
        ``transaction_bits`` with the target of its work (§7.1, §7.2).

        Raises:
            ValueError: ``created`` is not a UTC time to the second, or
                ``transaction_bits`` is negative or more than a hash has.
            OSError: a searching process could not be started, or exited
                during the search.
        """
        own = creator.node_id
        sent = Transaction(own, {own: Kismet(0)}, Kismet(0), 0, created).mined(
            0, minimum_bits=transaction_bits, searchers=searchers
        )
        return TransactionBlock(
            0,
            None,
            ChainWeight(0),
            0,
            (SignedTransaction.of(creator.signed(sent.text())),),
            {own: NO_BALANCE},
        )
