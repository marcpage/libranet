"""Putting a transaction up as a block of its own (KarmaLedger §5.6, Phase 6 Step 131).

A signed transaction, the node's own or one a person signs through it
(Phase 6 Steps 132 and 137), is put up for the generation after the head's
as a transaction block of its own, at the head's transaction target: holding
the transaction and the payout, stating its ``weight``, its balances, and its
``reach`` truly (§3.8), and signed by the node. It may be short of full, and
break the half-free rule (§5.6), for builders to combine (Phase 6 Step 134).
Its drop's nonce is searched for until it shares
``storage.search_min_bits`` with the target (§2), and it is
stored and announced, as the root the module makes is.

A transaction given to put up is checked first. One that is invalid by
itself, with too little work, moving nothing, or a signature that does not
check, or whose key the chain holds already, is logged and passed over
(§4.4); so is one whose sender's key is not held, which is asked for. Any
other is carried (:mod:`libranet.karma.carried`), and tried at each head
from then on: put up if it is valid there, and carried on, so that it is put
up again at the next head should the chain not take it in; or carried on if
it is pending, its sender's balance not covering it, or the chain not having
reached its ``generation``. One is carried until the chain holds its key, or
until ``karma.carry_generations`` after it was witnessed (§4.5). Each is
tried once at a head, so a key is put up there once.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from logging import getLogger

from libranet.bundle.errors import BundleError, BundleSignatureError, MissingContentError
from libranet.bundle.storing import HASH_ALGORITHM
from libranet.cas.content_id import ContentId
from libranet.karma.blocks import BLOCK_BOUNDS, BlockBounds, BlockDrop, TransactionBlock
from libranet.karma.carried import CarriedTransactions
from libranet.karma.checking import ChainBefore, ChainView, TransactionVerdict
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainHistory
from libranet.karma.ledger import BalanceSheet
from libranet.karma.placing import BlockPlacing
from libranet.karma.reading import HeldChain, TargetReader
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_carried import CarriedTransaction
from libranet.protocol.karma_transactions import SignedTransaction, TransactionKey

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class OwnBlock:
    """Transaction ``signed`` as a block of its own, built on a head (§5.6).

    ``verdict`` is the transaction's there (§4.4). ``block`` holds it and the
    payout, and states its ``weight``, balances, and ``reach`` truly (§3.8),
    when it is valid. It is ``None`` otherwise: a block holding a transaction
    that is not valid is not put up.
    """

    signed: SignedTransaction
    verdict: TransactionVerdict
    block: TransactionBlock | None

    @classmethod
    def of(cls, signed: SignedTransaction, head: ContentId, chain: ChainView) -> OwnBlock:
        """``signed`` as a block of its own, built on validation block ``head`` of ``chain``.

        Raises:
            MissingContentError: names the blocks ``chain`` lacks.
            ValueError: ``signed`` moves 0 Kismet, or the payout takes more
                from an identity than it holds.
        """
        before = ChainBefore.read(chain, head)
        last = before.generation - 1
        payout = before.payout(chain.balances(before.stakers(), last))
        block = TransactionBlock(
            before.generation,
            head,
            before.weight,
            0,
            tuple(sorted((payout, signed), key=lambda entry: entry.hash)),
            {},
        )
        sheet = BalanceSheet.of(block, chain.balances(block.named(), last))
        verdict = TransactionVerdict.of(signed, sheet, chain)

        if verdict is not TransactionVerdict.VALID:
            return cls(signed, verdict, None)

        stated = replace(block, balances=sheet.after)
        return cls(signed, verdict, replace(stated, reach=before.reach(stated)))


class TransactionsPutUp:
    """Puts up the transactions the node is given, each as a block of its own at the head (§5.6).

    Each is checked against the chain ``history`` keeps, its blocks read from
    what ``reader`` holds, and carried in ``carried``. A block is signed,
    placed, and stored as ``placing`` says. Each transaction's work is
    checked against ``bounds``: the specification's, unless a test gives
    less.
    """

    def __init__(
        self,
        history: ChainHistory,
        reader: TargetReader,
        carried: CarriedTransactions,
        placing: BlockPlacing,
        *,
        bounds: BlockBounds = BLOCK_BOUNDS,
    ) -> None:
        self._history = history
        self._reader = reader
        self._carried = carried
        self._placing = placing
        self._bounds = bounds
        self._chain = HeldChain(history, reader)
        # The head the keys tried, put up or found pending, were tried at.
        self._head: ContentId | None = None
        self._tried: set[TransactionKey] = set()

    def take(self, signed: SignedTransaction) -> bool:
        """Carry ``signed`` to put up, unless it is invalid, and put it up now if it is valid.

        One that is invalid, or whose sender's key is not held, is logged and
        passed over, as is one of a key carried already. One that cannot be
        carried, as ``carried.json`` cannot be written, is logged, and passed
        over too.

        Returns:
            Whether it is done with: carried, or passed over for good. It is
            not when its sender's key is not held, and is asked for, or it
            cannot be carried, so that whoever gave it may give it again.
        """
        try:
            invalid = self._invalid(signed)

        except MissingContentError:
            _LOGGER.warning(
                "Passing over Karma transaction %s: the key of its sender, %s, is not held, and "
                "is asked for",
                signed.hash,
                signed.transaction.sender,
            )
            return False

        except BundleError as error:
            _LOGGER.warning(
                "Passing over Karma transaction %s: the key of its sender cannot be read: %s",
                signed.hash,
                error,
            )
            return True

        if invalid:
            _LOGGER.warning(
                "Passing over Karma transaction %s, which is invalid: %s", signed.hash, invalid
            )
            return True

        try:
            carried = self._carried.carry((CarriedTransaction(signed, put_up=True),), self._history)

        except OSError as error:
            _LOGGER.error(
                "Cannot carry Karma transaction %s, which is passed over: %s", signed.hash, error
            )
            return False

        if all(each.signed.hash != signed.hash for each in carried):
            _LOGGER.info(
                "Not putting up Karma transaction %s: one of its key is carried already, or it "
                "was witnessed more than karma.carry_generations ago",
                signed.hash,
            )
            return True

        _LOGGER.info(
            "Taking Karma transaction %s from %s to put up", signed.hash, signed.transaction.sender
        )
        self.put_up()
        return True

    def put_up(self) -> None:
        """Put up each transaction carried to put up that is valid at the head, and not tried there.

        One pending there is carried on. One whose check lacks a block of
        the chain waits for it, which is asked for, and is tried as this is
        next called; so is one that cannot be put up for an error of the
        system, which is logged. One that cannot be checked is logged.
        """
        head = self._history.head

        if head is None:
            return

        if head.validation != self._head:
            self._head = head.validation
            self._tried.clear()

        try:
            carried = self._carried.to_put_up(self._history)

        except OSError as error:
            _LOGGER.error("Cannot read the Karma transactions carried: %s", error)
            return

        except (BundleError, ValueError) as error:
            _LOGGER.warning("Cannot read the Karma transactions carried: %s", error)
            return

        for each in carried:
            key = each.signed.transaction.key

            if key in self._tried:
                continue

            try:
                self._put_up(each.signed, head.validation)

            except MissingContentError:
                # Not logged: what it lacks is asked for, and it is tried again
                # once that comes.
                continue

            except OSError as error:
                _LOGGER.error("Cannot put up Karma transaction %s: %s", each.signed.hash, error)
                continue

            except (BundleError, ValueError) as error:
                _LOGGER.warning(
                    "Cannot put up Karma transaction %s, which cannot be checked: %s",
                    each.signed.hash,
                    error,
                )

            self._tried.add(key)

    def _invalid(self, signed: SignedTransaction) -> str:
        """Why ``signed`` is invalid, by itself or for its key being in the chain, or ``""``.

        Its work is checked against the bounds (§4.3), and its signature with
        its sender's key, held (§4.4).

        Raises:
            MissingContentError: its sender's key is not held, and is asked
                for.
            BundleError: the node shuns the key, or what is held of it is not
                it.
        """
        transaction = signed.transaction
        head = self._history.head

        if signed.work_bits < self._bounds.transaction_bits:
            return (
                f"it shares {signed.work_bits} bits with the target of its work, under "
                f"{self._bounds.transaction_bits}"
            )

        if transaction.sends() == Kismet(0):
            return "it moves 0 Kismet"

        if head is not None and self._history.has_key(
            transaction.key, transaction.generation, head.generation
        ):
            return "one of its key is in the chain"

        key = self._reader.held(transaction.sender)

        try:
            signed.verified(key)

        except BundleSignatureError as error:
            # Not logged: it is why it is invalid, which the caller logs.
            return f"its signature must check: {error}"

        return ""

    def _put_up(self, signed: SignedTransaction, head: ContentId) -> None:
        """Put ``signed`` up as a block of its own on ``head``, if it is valid there, and log it.

        Raises:
            MissingContentError: names the blocks of the chain not held,
                which are asked for.
            BundleError: one cannot be read, or the block is larger than an
                object may be, even compressed.
            ValueError: it moves 0 Kismet, the payout takes more than one
                holds, or the node's key cannot be loaded.
            OSError: the node's key could not be read, a searching process
                could not be started, or exited during the search, or the
                drop could not be written.
        """
        own = OwnBlock.of(signed, head, self._chain)

        if own.block is None:
            _LOGGER.info(
                "Not putting up Karma transaction %s at head %s, where it is %s",
                signed.hash,
                head,
                own.verdict.value,
            )
            return

        drop = self._placing.placed(
            BlockDrop(self._placing.identity().signed(own.block.text()), own.block),
            GenerationTargets.after(head).transaction,
            scored=False,
        )

        if drop is None:
            return

        _LOGGER.info(
            "Put up Karma transaction %s as block %s, sharing %d bits with the transaction "
            "target of %s",
            signed.hash,
            ContentId.for_data(drop.data, HASH_ALGORITHM),
            drop.matching_bits,
            head,
        )
