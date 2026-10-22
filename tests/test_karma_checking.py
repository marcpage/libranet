"""Tests for checking a block's transactions against the chain it is built on (KarmaLedger §4.4)."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Collection, Mapping, TypeVar

from pytest import fixture

from libranet.bundle.errors import MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import Balance, BlockDrop, TransactionBlock, ValidationBlock
from libranet.karma.checking import TransactionVerdict
from libranet.karma.generations import GenerationTargets, new_kismet
from libranet.karma.ledger import NO_BALANCE, BalanceSheet, Reward, reach_of
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.transactions import Payout, SignedTransaction, Transaction, TransactionKey
from libranet.karma.weight import ChainWeight
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
DAVE = ContentId("sha256", "d0" * 32)

_Block = TypeVar("_Block", TransactionBlock, ValidationBlock)


def matching(target: DropTarget, bits: int) -> ContentId:
    """An id sharing exactly ``bits`` leading bits with ``target``'s hash."""
    differing = int.from_bytes(target.digest, "big") ^ (1 << (255 - bits))
    return ContentId("sha256", differing.to_bytes(32, "big").hex())


# The chain a block is checked against: the root, and generation 1 built on
# it, each drop sharing a known number of bits with its target hash.
ROOT = GenerationTargets.root()
ROOT_ID = matching(ROOT.transaction, 3)
ROOT_VALIDATION_ID = matching(ROOT.validation, 5)
FIRST = GenerationTargets.after(ROOT_VALIDATION_ID)
FIRST_ID = matching(FIRST.transaction, 4)
FIRST_VALIDATION_ID = matching(FIRST.validation, 6)

# The weight of the chain before transaction block 2: 2^b over each drop.
WEIGHT = ChainWeight(2**3 + 2**5 + 2**4 + 2**6)


@dataclass(frozen=True)
class ChainInMemory:
    """A chain held in memory: its blocks by id, and each generation's winning validation block.

    It holds the keys of generations ``oldest`` on, and ``witnesses``, the
    generation each hash was witnessed in.
    """

    transactions: Mapping[ContentId, BlockDrop[TransactionBlock]]
    validations: Mapping[ContentId, BlockDrop[ValidationBlock]]
    genealogy: tuple[ContentId, ...]
    witnesses: Mapping[str, int] = field(default_factory=dict)
    oldest: int = 0

    def validation(self, block_id: ContentId) -> BlockDrop[ValidationBlock]:
        if block_id not in self.validations:
            raise MissingContentError((block_id,))

        return self.validations[block_id]

    def transaction(self, block_id: ContentId) -> BlockDrop[TransactionBlock]:
        if block_id not in self.transactions:
            raise MissingContentError((block_id,))

        return self.transactions[block_id]

    def stated(self, generation: int) -> Mapping[ContentId, Balance]:
        return self._block(generation).balances

    def balances(
        self, identities: Collection[ContentId], generation: int
    ) -> Mapping[ContentId, Balance]:
        latest: dict[ContentId, Balance] = {}

        for back in range(generation, -1, -1):
            for identity, balance in self.stated(back).items():
                latest.setdefault(identity, balance)

        return {identity: latest.get(identity, NO_BALANCE) for identity in identities}

    def has_key(self, key: TransactionKey, first: int, last: int) -> bool:
        return any(
            signed.transaction.key == key
            for generation in range(max(first, self.oldest), last + 1)
            for signed in self._block(generation).signed
        )

    def witnessed(self, transaction_hash: str) -> int | None:
        return self.witnesses.get(transaction_hash)

    def _block(self, generation: int) -> TransactionBlock:
        validation = self.validation(self.genealogy[generation]).block
        return self.transaction(validation.stakes[0].stake.transaction_block).block


def moment(seconds: int) -> datetime:
    """``seconds`` after :data:`CREATED`."""
    return CREATED + timedelta(seconds=seconds)


def paying(
    sender: NodeIdentity,
    to: dict[ContentId, int],
    *,
    fee: int = 0,
    generation: int = 1,
    created: datetime = CREATED,
) -> SignedTransaction:
    """A transaction ``sender`` signs in ``generation``, paying each of ``to``, and ``fee``."""
    amounts = {identity: Kismet(amount) for identity, amount in to.items()}
    transaction = Transaction(sender.node_id, amounts, Kismet(fee), generation, created)
    return SignedTransaction.of(sender.signed(transaction.text()))


def staked(staker: NodeIdentity, on: ContentId, kismet: int) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), CREATED)
    return SignedStake.of(staker.signed(stake.text()))


def drop_of(builder: NodeIdentity, block: _Block) -> BlockDrop[_Block]:
    """``block``, as ``builder`` signs it."""
    return BlockDrop(builder.signed(block.text()), block)


def by_hash(*transactions: SignedTransaction | Payout) -> tuple[SignedTransaction | Payout, ...]:
    """``transactions``, sorted by hash, as a block holds them."""
    return tuple(sorted(transactions, key=lambda transaction: transaction.hash))


def stating(block: TransactionBlock, before: Mapping[ContentId, Balance]) -> TransactionBlock:
    """``block``, stating the balances it leaves from ``before``, and a reach of 0."""
    return replace(block, balances=BalanceSheet.of(block, before).after)


def reward_of(chain: ChainInMemory, generation: int = 2) -> Payout:
    """The payout of transaction block ``generation`` on ``chain``: validation block 1's reward."""
    validation = chain.validation(FIRST_VALIDATION_ID)
    validated = chain.transaction(FIRST_ID).block
    reward = Reward(validation.block, validation.builder, validated, validated.balances)
    payout = reward.payout()
    return Payout.of(payout.sources, payout.to, generation)


def block_on(
    chain: ChainInMemory, *transactions: SignedTransaction | Payout, generation: int = 2
) -> TransactionBlock:
    """Transaction block ``generation`` on ``chain``, holding ``transactions``.

    It holds validation block 1's reward as its payout, unless
    ``transactions`` hold another, and states the balances it leaves and its
    reach truly.
    """
    if not any(isinstance(transaction, Payout) for transaction in transactions):
        transactions = (*transactions, reward_of(chain, generation))

    block = TransactionBlock(generation, FIRST_VALIDATION_ID, WEIGHT, 0, by_hash(*transactions), {})
    block = stating(block, chain.balances(block.named(), 1))
    return replace(block, reach=reach_of([block.balances, chain.stated(1)]))


def sheet_of(block: TransactionBlock, chain: ChainInMemory) -> BalanceSheet:
    """The balances ``block``, built on ``chain``, is checked against."""
    return BalanceSheet.of(block, chain.balances(block.named(), 1))


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(alice: NodeIdentity) -> TransactionBlock:
    created = paying(alice, {alice.node_id: 0}, generation=0)
    return TransactionBlock(0, None, ChainWeight(0), 0, (created,), {alice.node_id: NO_BALANCE})


@fixture(scope="module")
def chain(alice: NodeIdentity, bob: NodeIdentity, root: TransactionBlock) -> ChainInMemory:
    """The root, which Alice built, and generation 1, in which Alice pays Dave 1,000 Kismet.

    Alice stakes 500 Kismet in validation block 1, which Bob built.
    """
    root_validation = ValidationBlock(0, None, (staked(alice, ROOT_ID, 0),), ())
    payout = Reward(root_validation, alice.node_id, root, {}).payout()
    sent = paying(alice, {DAVE: 1_000}, fee=10, generation=0, created=moment(1))
    first = stating(
        TransactionBlock(
            1, ROOT_VALIDATION_ID, ChainWeight(2**3 + 2**5), 0, by_hash(payout, sent), {}
        ),
        {},
    )
    first_validation = ValidationBlock(1, None, (staked(alice, FIRST_ID, 500),), ())
    return ChainInMemory(
        {ROOT_ID: drop_of(alice, root), FIRST_ID: drop_of(alice, first)},
        {
            ROOT_VALIDATION_ID: drop_of(alice, root_validation),
            FIRST_VALIDATION_ID: drop_of(bob, first_validation),
        },
        (ROOT_VALIDATION_ID, FIRST_VALIDATION_ID),
    )


@fixture(scope="module")
def sent(alice: NodeIdentity, bob: NodeIdentity) -> SignedTransaction:
    """Alice paying Bob 300 Kismet, free, in generation 1."""
    return paying(alice, {bob.node_id: 300}, created=moment(2))


@fixture(scope="module")
def block(chain: ChainInMemory, sent: SignedTransaction) -> TransactionBlock:
    return block_on(chain, sent)


def test_a_transaction_covered_and_of_a_generation_reached_is_valid(
    chain: ChainInMemory, sent: SignedTransaction, block: TransactionBlock
) -> None:
    assert TransactionVerdict.of(sent, sheet_of(block, chain), chain) is TransactionVerdict.VALID


def test_a_transaction_its_senders_balance_does_not_cover_is_pending(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory
) -> None:
    overspending = paying(alice, {bob.node_id: 2 * new_kismet(0).count}, created=moment(2))
    sheet = sheet_of(block_on(chain, overspending), chain)

    assert TransactionVerdict.of(overspending, sheet, chain) is TransactionVerdict.PENDING


def test_a_transaction_of_a_generation_not_reached_is_pending(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory
) -> None:
    early = paying(alice, {bob.node_id: 300}, generation=3, created=moment(2))
    sheet = sheet_of(block_on(chain, early), chain)

    assert TransactionVerdict.of(early, sheet, chain) is TransactionVerdict.PENDING


def test_a_transaction_whose_key_is_in_the_chain_is_invalid(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory
) -> None:
    # It overspends too, but a transaction is pending only if nothing else
    # makes it invalid.
    again = paying(alice, {bob.node_id: 2 * new_kismet(0).count}, created=moment(1))
    sheet = sheet_of(block_on(chain, again), chain)

    assert TransactionVerdict.of(again, sheet, chain) is TransactionVerdict.INVALID


def test_a_key_is_looked_for_from_the_generation_its_transaction_names(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory
) -> None:
    # The payment to Dave with this key is in block 1, before generation 2.
    later = paying(alice, {bob.node_id: 5}, generation=2, created=moment(1))
    sheet = sheet_of(block_on(chain, later), chain)

    assert TransactionVerdict.of(later, sheet, chain) is TransactionVerdict.VALID
