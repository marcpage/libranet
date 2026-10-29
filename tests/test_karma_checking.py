"""Tests for checking a transaction block against its target and its chain (KarmaLedger §3.8)."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Callable, Collection, Mapping, TypeVar

from pytest import fixture, mark, raises

from libranet.bundle.errors import MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import (
    BlockBounds,
    BlockDrop,
    BlockFinding,
    MisplacedBlockError,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import (
    BlockScore,
    TransactionBlockCheck,
    TransactionVerdict,
)
from libranet.karma.generations import GenerationTargets, new_kismet
from libranet.karma.ledger import BalanceSheet, Reward, reach_of
from libranet.karma.weight import ChainWeight
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import NO_BALANCE, Balance
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import (
    Payout,
    SignedTransaction,
    Transaction,
    TransactionKey,
)
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
DAVE = ContentId("sha256", "d0" * 32)
ERIN = ContentId("sha256", "e0" * 32)
NOWHERE = ContentId("sha256", "5e" * 32)
RIVAL = ContentId("sha256", "4d" * 32)

_Block = TypeVar("_Block", TransactionBlock, ValidationBlock)

# Bounds every transaction meets, in which a block of FULL_BYTES is full.
BOUNDS = BlockBounds(most_bytes=1_000, room_bytes=500, transaction_bits=0)
FULL_BYTES = 800


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
SECOND = GenerationTargets.after(FIRST_VALIDATION_ID)
SECOND_ID = matching(SECOND.transaction, 7)

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


def data_of(builder: NodeIdentity, block: TransactionBlock) -> bytes:
    """``block``'s drop, as ``builder`` signs it, with a nonce."""
    return drop_of(builder, block).content() + b"\x007"


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


def checked(
    block: TransactionBlock,
    builder: NodeIdentity,
    chain: ChainInMemory,
    keys: Mapping[ContentId, bytes],
    *,
    compressed_bytes: int = FULL_BYTES,
    bounds: BlockBounds = BOUNDS,
    named_for: ContentId | None = FIRST_VALIDATION_ID,
) -> TransactionBlockCheck | BlockFinding:
    """``block``, signed by ``builder``, checked at the target named for ``named_for``.

    That is transaction block 2's target unless another is named, and the
    block is checked against ``chain``.
    """
    return TransactionBlockCheck.of(
        ROOT_ID if named_for is None else SECOND_ID,
        data_of(builder, block),
        named_for,
        chain,
        keys,
        compressed_bytes=compressed_bytes,
        bounds=bounds,
    )


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
def keys(alice: NodeIdentity, bob: NodeIdentity) -> dict[ContentId, bytes]:
    return {identity.node_id: identity.public_key for identity in (alice, bob)}


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


def test_a_correct_block_is_scored_by_its_bits_its_reach_and_its_age(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    check = checked(block, alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.is_correct
    assert check.incorrect == ""
    assert check.drop.block == block
    # Dave, who holds 1,000 Kismet, is stated in block 1, and not in block 2.
    assert check.score == BlockScore(7, 1, Fraction(2))


def test_the_payout_and_balances_a_correct_block_states_are_the_rewards_and_those_it_leaves(
    alice: NodeIdentity, bob: NodeIdentity, block: TransactionBlock
) -> None:
    held = new_kismet(0).count - 1_000 + new_kismet(1).count

    assert block.payout is not None
    assert block.payout.sources == {None: new_kismet(1), alice.node_id: Kismet(10)}
    assert block.payout.to == {alice.node_id: Kismet(new_kismet(1).count + 10)}
    assert block.balances == {
        alice.node_id: Balance(Kismet(held - 300), 1),
        bob.node_id: Balance(Kismet(300)),
    }


def test_a_block_stating_every_holder_has_reach_0(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    stating_dave = replace(
        block, balances={**block.balances, DAVE: Balance(Kismet(1_000))}, reach=0
    )

    check = checked(stating_dave, alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.score == BlockScore(7, 0, Fraction(2))


def test_a_transaction_is_as_old_as_the_generation_that_witnessed_it(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    sent: SignedTransaction,
) -> None:
    unwitnessed = paying(alice, {bob.node_id: 5}, created=moment(3))
    witnessing = replace(chain, witnesses={sent.hash: 0})

    check = checked(block_on(chain, sent, unwitnessed), alice, witnessing, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.score is not None
    assert check.score.age == Fraction(0 + 2, 2)


def test_a_block_holding_only_its_payout_is_as_young_as_can_be(
    alice: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    check = checked(block_on(chain), alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.score is not None
    assert check.score.age == Fraction(2)


@mark.parametrize(
    "better, worse",
    [
        (BlockScore(8, 3, Fraction(5)), BlockScore(7, 0, Fraction(0))),
        (BlockScore(7, 1, Fraction(5)), BlockScore(7, 2, Fraction(0))),
        (BlockScore(7, 1, Fraction(1)), BlockScore(7, 1, Fraction(3, 2))),
    ],
)
def test_scores_rank_by_bits_then_reach_then_age(better: BlockScore, worse: BlockScore) -> None:
    assert better > worse
    assert worse < better
    assert max(worse, better) is better


def test_a_drop_that_is_no_signed_block_is_unsigned(
    chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    found = TransactionBlockCheck.of(
        SECOND_ID, b"no block", FIRST_VALIDATION_ID, chain, keys, compressed_bytes=FULL_BYTES
    )

    assert isinstance(found, BlockFinding)
    assert found.builder is None


def test_what_a_block_states_falsely_by_itself_is_found_naming_its_builder(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    demanding = BlockBounds(most_bytes=1_000, room_bytes=500, transaction_bits=256)

    found = checked(block, alice, chain, keys, bounds=demanding)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert "under 256" in found.statement


@mark.parametrize(
    "changed, statement",
    [
        (lambda block: replace(block, weight=ChainWeight(119)), '"weight" must be 120, got 119'),
        (lambda block: replace(block, reach=2), '"reach" must be 1, got 2'),
    ],
)
def test_a_block_stating_falsely_what_it_is_built_on_distrusts_its_builder(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    changed: Callable[[TransactionBlock], TransactionBlock],
    statement: str,
) -> None:
    found = checked(changed(block), alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert statement in found.statement


def test_a_block_of_another_generation_than_its_targets_is_false(
    alice: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes], sent: SignedTransaction
) -> None:
    found = checked(block_on(chain, sent, generation=3), alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert '"generation" must be 2' in found.statement


def test_a_block_paying_other_than_the_reward_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    sent: SignedTransaction,
) -> None:
    reward = reward_of(chain)
    paid = sum(reward.to.values(), Kismet(0))
    elsewhere = Payout.of(reward.sources, {bob.node_id: paid}, 2)

    found = checked(block_on(chain, sent, elsewhere), alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert "payout must pay the reward" in found.statement


@mark.parametrize(
    "stated",
    [
        lambda balances, alice: {**balances, alice: Balance(balances[alice].kismet + Kismet(1), 1)},
        lambda balances, alice: {**balances, alice: Balance(balances[alice].kismet)},
        lambda balances, alice: {**balances, DAVE: Balance(Kismet(999))},
    ],
)
def test_a_block_stating_a_balance_wrongly_is_false(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    stated: Callable[[Mapping[ContentId, Balance], ContentId], dict[ContentId, Balance]],
) -> None:
    misstated = replace(block, balances=stated(block.balances, alice.node_id))

    found = checked(misstated, alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert "must state the balance of" in found.statement
    assert ", got {" in found.statement


@mark.parametrize("sender", [True, False])
def test_a_block_leaving_a_sender_or_recipient_unstated_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    sender: bool,
) -> None:
    left_out = alice.node_id if sender else bob.node_id
    unstated = replace(
        block,
        balances={
            identity: balance
            for identity, balance in block.balances.items()
            if identity != left_out
        },
    )

    found = checked(unstated, alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.statement.endswith(f"must state the balance of {left_out}, which it names")


def test_a_block_whose_sender_overspends_is_false(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    overspending = paying(alice, {bob.node_id: 2 * new_kismet(0).count}, created=moment(2))

    found = checked(block_on(chain, overspending), alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert found.statement.startswith(f"{alice.node_id} sends more")


def test_a_block_holding_a_key_already_in_the_chain_is_false(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    # Alice paid Dave in block 1 at the same moment, so this is that payment.
    again = paying(alice, {bob.node_id: 5}, created=moment(1))

    found = checked(block_on(chain, again), alice, chain, keys)

    assert isinstance(found, BlockFinding)
    assert found.statement == f"Transaction {again.hash} has the key of one already in the chain"


def test_a_duplicate_older_than_the_history_held_is_neither_false_nor_incorrect(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    again = paying(alice, {bob.node_id: 5}, created=moment(1))

    check = checked(block_on(chain, again), alice, replace(chain, oldest=2), keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.is_correct


@mark.parametrize("compressed_bytes", [0, 500, 1_001])
def test_a_block_not_full_is_incorrect(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    compressed_bytes: int,
) -> None:
    check = checked(block, alice, chain, keys, compressed_bytes=compressed_bytes)

    assert isinstance(check, TransactionBlockCheck)
    assert not check.is_correct
    assert check.score is None
    assert not check.full
    assert "must be full" in check.incorrect


def test_a_block_breaking_the_half_free_rule_is_incorrect(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    paid = paying(alice, {bob.node_id: 300}, fee=5, created=moment(2))

    check = checked(block_on(chain, paid), alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.full
    assert "must be free" in check.incorrect


def test_a_block_including_a_transaction_before_its_generation_is_incorrect(
    alice: NodeIdentity, bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes]
) -> None:
    early = paying(alice, {bob.node_id: 300}, generation=3, created=moment(2))

    check = checked(block_on(chain, early), alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.incorrect == f"Transaction {early.hash} is included before the generation it names"


def test_a_block_stating_0_for_one_it_does_not_name_is_incorrect(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    idle = replace(block, balances={**block.balances, ERIN: NO_BALANCE})

    check = checked(idle, alice, chain, keys)

    assert isinstance(check, TransactionBlockCheck)
    assert check.incorrect == (
        f"A transaction block states a balance of 0 for {ERIN}, which it does not name"
    )


def test_a_block_waits_for_the_validation_block_its_target_is_named_for(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    lacking = replace(chain, validations={})

    with raises(MissingContentError) as raised:
        checked(block, alice, lacking, keys)

    assert raised.value.content_ids == (FIRST_VALIDATION_ID,)


def test_a_block_waits_for_the_transaction_block_before_it(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    lacking = replace(chain, transactions={ROOT_ID: chain.transactions[ROOT_ID]})

    with raises(MissingContentError) as raised:
        checked(block, alice, lacking, keys)

    assert raised.value.content_ids == (FIRST_ID,)


def test_a_block_waits_for_the_runner_up_whose_stakes_its_payout_takes(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> None:
    validation = chain.validations[FIRST_VALIDATION_ID]
    naming = BlockDrop(validation.bundle, replace(validation.block, runner_up=RIVAL))
    lacking = replace(chain, validations={**chain.validations, FIRST_VALIDATION_ID: naming})

    with raises(MissingContentError) as raised:
        checked(block, alice, lacking, keys)

    assert raised.value.content_ids == (RIVAL,)


def test_the_roots_block_is_correct_by_its_shape_though_not_full(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    root: TransactionBlock,
) -> None:
    check = checked(root, alice, chain, keys, compressed_bytes=1, named_for=None)

    assert isinstance(check, TransactionBlockCheck)
    assert check.score == BlockScore(3, 0, Fraction(0))


@mark.parametrize(
    "changed",
    [
        lambda root: replace(root, weight=ChainWeight(1)),
        lambda root: replace(root, reach=1),
        lambda root: replace(
            root, balances={identity: Balance(Kismet(0), 0) for identity in root.balances}
        ),
    ],
)
def test_a_block_at_the_roots_target_not_shaped_as_the_root_is_false(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    root: TransactionBlock,
    changed: Callable[[TransactionBlock], TransactionBlock],
) -> None:
    found = checked(changed(root), alice, chain, keys, compressed_bytes=1, named_for=None)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert "the root's target must be generation 0" in found.statement


def test_a_root_built_by_another_than_its_creator_is_false(
    bob: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes], root: TransactionBlock
) -> None:
    found = checked(root, bob, chain, keys, compressed_bytes=1, named_for=None)

    assert isinstance(found, BlockFinding)
    assert found.builder == bob.node_id
    assert "the root's target must be generation 0" in found.statement


@mark.parametrize("named_for", [FIRST_VALIDATION_ID, None])
def test_a_block_built_on_another_than_its_targets_is_of_another_target(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    named_for: ContentId | None,
) -> None:
    # Placed at transaction block 2's target, or the root's, as anyone may
    # place a copy, since a drop's nonce is not signed.
    elsewhere = replace(block, previous=NOWHERE)

    with raises(MisplacedBlockError, match=f"It is built on {NOWHERE}, where a block"):
        checked(elsewhere, alice, chain, keys, named_for=named_for)


def test_the_root_at_a_later_target_is_of_another_target(
    alice: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes], root: TransactionBlock
) -> None:
    with raises(MisplacedBlockError, match="It is built on None"):
        checked(root, alice, chain, keys)


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


@mark.parametrize("score, incorrect", [(None, ""), (BlockScore(7, 1, Fraction(2)), "Not full")])
def test_a_checked_block_is_either_scored_or_incorrect(
    block: TransactionBlock, alice: NodeIdentity, score: BlockScore | None, incorrect: str
) -> None:
    with raises(ValueError, match="scored when it is correct"):
        TransactionBlockCheck(drop_of(alice, block), score, incorrect)
