"""Checking a block's transactions against the chain it is built on (KarmaLedger §4.4).

A transaction a block holds is valid there, pending, or invalid. It is
invalid when its key is already in the chain, and pending, which is no
error, when its sender's balance does not cover it, or the chain has not
reached its generation. What it states by itself is found as its block is
read (:meth:`~libranet.karma.blocks.BlockDrop.decoded`).

The chain is read through a :class:`ChainView`, so that a check is made
against what a node holds, or against a chain held in memory. What the view
does not hold, it names, and a check waits for it rather than judging. A
key older than the history it holds is not known to it, so a duplicate
older than that is passed (§3.8).
"""

from __future__ import annotations
from enum import Enum
from typing import Collection, Mapping, Protocol

from libranet.cas.content_id import ContentId
from libranet.karma.blocks import Balance, BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.ledger import BalanceSheet
from libranet.karma.transactions import SignedTransaction, TransactionKey


class ChainView(Protocol):
    """What a check reads of a chain: the chain through one validation block, as a node holds it.

    Its generations are numbered from the root's, 0. A block it does not
    hold, it raises :class:`~libranet.bundle.errors.MissingContentError`
    for, naming what it lacks, so that it is fetched and the check made
    again. The keys and witnesses it holds may not reach back to the root
    (KarmaLedger §9): what is older is not known to it.
    """

    def validation(self, block_id: ContentId) -> BlockDrop[ValidationBlock]:
        """Validation block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held.
        """
        ...

    def transaction(self, block_id: ContentId) -> BlockDrop[TransactionBlock]:
        """Transaction block ``block_id``, as its builder signed it.

        Raises:
            MissingContentError: it is not held.
        """
        ...

    def stated(self, generation: int) -> Mapping[ContentId, Balance]:
        """The balances the chain's transaction block ``generation`` states.

        Raises:
            MissingContentError: names that block, which is not held.
        """
        ...

    def balances(
        self, identities: Collection[ContentId], generation: int
    ) -> Mapping[ContentId, Balance]:
        """Each of ``identities``' balance, as the chain's transaction block ``generation`` left it.

        That is the latest stated of it through that block, or
        :data:`~libranet.karma.ledger.NO_BALANCE` for one stated nowhere
        (§5.3).

        Raises:
            MissingContentError: names the blocks within that block's reach
                that are not held.
        """
        ...

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        """Whether a transaction in the chain's blocks ``first`` to ``last`` has ``key``.

        Those are its transaction blocks, ``first`` and ``last`` among them,
        as far back as the history it holds (§4.4).
        """
        ...

    def witnessed(self, transaction_hash: str) -> int | None:
        """The generation of the chain's winning validation block listing ``transaction_hash``.

        It is ``None`` when none it holds lists it (§4.5, §6.4).
        """
        ...


class TransactionVerdict(Enum):
    """A transaction's verdict in a transaction block (§4.4).

    A pending transaction is no error: it is carried forward, and tried in
    each later block, until its sender's balance covers it and the chain
    reaches its generation. An invalid one is never included.
    """

    VALID = "valid"
    PENDING = "pending"
    INVALID = "invalid"

    @classmethod
    def of(
        cls, signed: SignedTransaction, sheet: BalanceSheet, chain: ChainView
    ) -> TransactionVerdict:
        """``signed``'s verdict in ``sheet``'s block, which is built on ``chain``.

        It is invalid when its key is a transaction's in one of ``chain``'s
        blocks from its ``generation`` through the one before ``sheet``'s,
        of those ``chain`` holds. Otherwise, it is pending when its
        ``generation`` is later than its block's, or its sender is one
        ``sheet`` finds overspent. What it states by itself, its signature,
        its work, that it moves something, and that it shares no key with
        another in its block, is found as its block is read
        (:meth:`~libranet.karma.blocks.BlockDrop.decoded`).
        """
        transaction = signed.transaction
        generation = sheet.block.generation

        if transaction.generation < generation and chain.has_key(
            transaction.key, transaction.generation, generation - 1
        ):
            return cls.INVALID

        if transaction.generation > generation or transaction.sender in sheet.overspent:
            return cls.PENDING

        return cls.VALID
