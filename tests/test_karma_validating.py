"""Tests for checking a validation block, and choosing a generation's best (KarmaLedger §6.5)."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Callable, Collection, Mapping, TypeVar

from pytest import fixture, mark, raises

from libranet.bundle.errors import MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import (
    Balance,
    BlockBounds,
    BlockDrop,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.ledger import NO_BALANCE, BalanceSheet, Reward, reach_of
from libranet.karma.stakes import SignedStake, Stake
from libranet.karma.transactions import Payout, SignedTransaction, Transaction, TransactionKey
from libranet.karma.validating import ValidationBlockCheck, ValidationScore
from libranet.karma.weight import ChainWeight
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
DAVE = ContentId("sha256", "d0" * 32)
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
# it; then generation 2's blocks, each drop sharing a known number of bits
# with its target hash.
ROOT = GenerationTargets.root()
ROOT_ID = matching(ROOT.transaction, 3)
ROOT_VALIDATION_ID = matching(ROOT.validation, 5)
FIRST = GenerationTargets.after(ROOT_VALIDATION_ID)
FIRST_ID = matching(FIRST.transaction, 4)
FIRST_VALIDATION_ID = matching(FIRST.validation, 6)
SECOND = GenerationTargets.after(FIRST_VALIDATION_ID)
SECOND_ID = matching(SECOND.transaction, 7)
OTHER_ID = matching(SECOND.transaction, 5)
VALIDATION_ID = matching(SECOND.validation, 6)
RUNNER_UP_ID = matching(SECOND.validation, 4)

# The weight of the chain before transaction block 2: 2^b over each drop.
WEIGHT = ChainWeight(2**3 + 2**5 + 2**4 + 2**6)


@dataclass(frozen=True)
class ChainInMemory:
    """A chain held in memory: its blocks by id, and each generation's winning validation block.

    ``witnesses`` are the generation each hash was witnessed in.
    """

    transactions: Mapping[ContentId, BlockDrop[TransactionBlock]]
    validations: Mapping[ContentId, BlockDrop[ValidationBlock]]
    genealogy: tuple[ContentId, ...]
    witnesses: Mapping[str, int] = field(default_factory=dict)

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
            for generation in range(first, last + 1)
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


def staked(
    staker: NodeIdentity, on: ContentId, kismet: int, *, created: datetime = CREATED
) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), created)
    return SignedStake.of(staker.signed(stake.text()))


def validation_of(
    *stakes: SignedStake,
    generation: int = 2,
    runner_up: ContentId | None = None,
    witnessed: tuple[str, ...] = (),
) -> ValidationBlock:
    """Validation block ``generation``, holding ``stakes``, sorted by hash as a block holds them."""
    ordered = tuple(sorted(stakes, key=lambda stake: stake.hash))
    return ValidationBlock(generation, runner_up, ordered, witnessed)


def drop_of(builder: NodeIdentity, block: _Block) -> BlockDrop[_Block]:
    """``block``, as ``builder`` signs it."""
    return BlockDrop(builder.signed(block.text()), block)


def data_of(builder: NodeIdentity, block: TransactionBlock | ValidationBlock) -> bytes:
    """``block``'s drop, as ``builder`` signs it, with a nonce."""
    return builder.signed(block.text()).encoded() + b"\x007"


def by_hash(*transactions: SignedTransaction | Payout) -> tuple[SignedTransaction | Payout, ...]:
    """``transactions``, sorted by hash, as a block holds them."""
    return tuple(sorted(transactions, key=lambda transaction: transaction.hash))


def stating(block: TransactionBlock, before: Mapping[ContentId, Balance]) -> TransactionBlock:
    """``block``, stating the balances it leaves from ``before``."""
    return replace(block, balances=BalanceSheet.of(block, before).after)


def block_on(chain: ChainInMemory, *transactions: SignedTransaction) -> TransactionBlock:
    """Transaction block 2 on ``chain``, holding ``transactions``, as it truly is.

    It holds validation block 1's reward as its payout, and states the
    balances it leaves and its reach truly.
    """
    validation = chain.validation(FIRST_VALIDATION_ID)
    validated = chain.transaction(FIRST_ID).block
    reward = Reward(validation.block, validation.builder, validated, validated.balances)
    entries = by_hash(*transactions, reward.payout())
    block = TransactionBlock(2, FIRST_VALIDATION_ID, WEIGHT, 0, entries, {})
    block = stating(block, chain.balances(block.named(), 1))
    return replace(block, reach=reach_of([block.balances, chain.stated(1)]))


def transaction_check(
    block: TransactionBlock,
    builder: NodeIdentity,
    chain: ChainInMemory,
    keys: Mapping[ContentId, bytes],
    *,
    block_id: ContentId = SECOND_ID,
    compressed_bytes: int = FULL_BYTES,
) -> TransactionBlockCheck | BlockFinding:
    """``block``, signed by ``builder``, checked at transaction block 2's target, on ``chain``."""
    return TransactionBlockCheck.of(
        block_id,
        data_of(builder, block),
        FIRST_VALIDATION_ID,
        chain,
        keys,
        compressed_bytes=compressed_bytes,
        bounds=BOUNDS,
    )


def checked(
    validation: ValidationBlock,
    builder: NodeIdentity,
    chain: ChainInMemory,
    keys: Mapping[ContentId, bytes],
    transactions: Mapping[ContentId, TransactionBlockCheck | BlockFinding],
    *,
    block_id: ContentId = VALIDATION_ID,
    named_for: ContentId | None = FIRST_VALIDATION_ID,
    compressed_bytes: int = FULL_BYTES,
) -> ValidationBlockCheck | BlockFinding:
    """``validation``, signed by ``builder``, checked at the target named for ``named_for``.

    That is validation block 2's target unless another is named, and the
    block is checked against ``chain`` and the checks ``transactions``.
    """
    return ValidationBlockCheck.of(
        block_id,
        data_of(builder, validation),
        named_for,
        chain,
        keys,
        transactions=transactions,
        compressed_bytes=compressed_bytes,
        bounds=BOUNDS,
    )


def holding(
    chain: ChainInMemory, builder: NodeIdentity, runner_up: ValidationBlock
) -> ChainInMemory:
    """``chain``, holding ``runner_up``, signed by ``builder``, as validation block RUNNER_UP_ID."""
    return replace(
        chain, validations={**chain.validations, RUNNER_UP_ID: drop_of(builder, runner_up)}
    )


def ranked(
    score: ValidationScore | None, block_id: ContentId, drop: BlockDrop[ValidationBlock]
) -> ValidationBlockCheck:
    """``drop``, as validation block ``block_id``, checked and scored ``score``, or not correct."""
    return ValidationBlockCheck(block_id, drop, score, "" if score is not None else "Not full")


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def keys(alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity) -> dict[ContentId, bytes]:
    return {identity.node_id: identity.public_key for identity in (alice, bob, carol)}


@fixture(scope="module")
def root(alice: NodeIdentity) -> TransactionBlock:
    created = paying(alice, {alice.node_id: 0}, generation=0)
    return TransactionBlock(0, None, ChainWeight(0), 0, (created,), {alice.node_id: NO_BALANCE})


@fixture(scope="module")
def root_validation(alice: NodeIdentity) -> ValidationBlock:
    return validation_of(staked(alice, ROOT_ID, 0), generation=0)


@fixture(scope="module")
def chain(
    alice: NodeIdentity,
    bob: NodeIdentity,
    root: TransactionBlock,
    root_validation: ValidationBlock,
) -> ChainInMemory:
    """The root, which Alice built, and generation 1, in which Alice pays Dave 1,000 Kismet.

    Alice stakes 500 Kismet in validation block 1, which Bob built.
    """
    payout = Reward(root_validation, alice.node_id, root, {}).payout()
    sent = paying(alice, {DAVE: 1_000}, fee=10, generation=0, created=moment(1))
    first = stating(
        TransactionBlock(
            1, ROOT_VALIDATION_ID, ChainWeight(2**3 + 2**5), 0, by_hash(payout, sent), {}
        ),
        {},
    )
    first_validation = validation_of(staked(alice, FIRST_ID, 500), generation=1)
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
    """Transaction block 2, in which Alice, who won generation 1, pays Bob 300 Kismet."""
    return block_on(chain, sent)


@fixture(scope="module")
def transactions(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
) -> dict[ContentId, TransactionBlockCheck | BlockFinding]:
    """What is at transaction block 2's target: ``block``, which Alice built, checked correct."""
    return {SECOND_ID: transaction_check(block, alice, chain, keys)}


@fixture(scope="module")
def validation(alice: NodeIdentity, bob: NodeIdentity) -> ValidationBlock:
    """Validation block 2: Alice stakes 1,000 Kismet on transaction block 2, and Bob 100."""
    return validation_of(staked(alice, SECOND_ID, 1_000), staked(bob, SECOND_ID, 100))


def test_a_correct_block_is_scored_by_its_bits_and_its_effective_stake(
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
    validation: ValidationBlock,
) -> None:
    check = checked(validation, bob, chain, keys, transactions)

    assert isinstance(check, ValidationBlockCheck)
    assert check.is_correct
    assert check.incorrect == ""
    assert check.block_id == VALIDATION_ID
    assert check.drop.block == validation
    # Alice won generation 1, so weighs a tenth; Bob has won none, and weighs
    # the whole.
    assert check.score == ValidationScore(6, 1_000 * 1 + 100 * 10)


def test_a_drop_that_is_no_signed_block_is_unsigned(
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    found = ValidationBlockCheck.of(
        VALIDATION_ID,
        b"no block",
        FIRST_VALIDATION_ID,
        chain,
        keys,
        transactions=transactions,
        compressed_bytes=FULL_BYTES,
    )

    assert isinstance(found, BlockFinding)
    assert found.builder is None


def test_a_block_of_another_generation_than_its_target_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    later = validation_of(staked(alice, SECOND_ID, 1_000), generation=3)

    found = checked(later, bob, chain, keys, transactions)

    assert isinstance(found, BlockFinding)
    assert found.builder == bob.node_id
    assert '"generation" must be 2' in found.statement


@mark.parametrize(
    "stakes, counted",
    [
        (lambda alice, bob: (), "got 0 on 0"),
        (
            lambda alice, bob: (staked(alice, SECOND_ID, 1_000), staked(bob, OTHER_ID, 100)),
            "got 2 on 2",
        ),
    ],
)
def test_a_block_holding_no_stake_or_stakes_on_two_blocks_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
    stakes: Callable[[NodeIdentity, NodeIdentity], tuple[SignedStake, ...]],
    counted: str,
) -> None:
    found = checked(validation_of(*stakes(alice, bob)), bob, chain, keys, transactions)

    assert isinstance(found, BlockFinding)
    assert found.builder == bob.node_id
    assert found.statement.endswith(counted)


def test_a_block_staking_on_a_block_at_another_target_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    # Transaction block 1 is held, and is not at transaction block 2's target.
    elsewhere = validation_of(staked(alice, FIRST_ID, 1_000))

    found = checked(elsewhere, bob, chain, keys, transactions)

    assert isinstance(found, BlockFinding)
    assert found.statement.endswith(f"at its target, got {FIRST_ID}")


def test_a_block_waits_for_the_transaction_block_its_stakes_name(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    with raises(MissingContentError) as raised:
        checked(validation_of(staked(alice, NOWHERE, 1_000)), bob, chain, keys, transactions)

    assert raised.value.content_ids == (NOWHERE,)


def test_a_block_waits_for_the_validation_block_its_target_is_named_for(
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
    validation: ValidationBlock,
) -> None:
    with raises(MissingContentError) as raised:
        checked(validation, bob, replace(chain, validations={}), keys, transactions)

    assert raised.value.content_ids == (FIRST_VALIDATION_ID,)


@mark.parametrize("builder", [True, False])
def test_a_block_validating_one_found_unsigned_or_false_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    validation: ValidationBlock,
    builder: bool,
) -> None:
    finding = BlockFinding("It is false", alice.node_id if builder else None)

    found = checked(validation, bob, chain, keys, {SECOND_ID: finding})

    assert isinstance(found, BlockFinding)
    assert found.builder == bob.node_id
    assert found.statement.endswith(f"and {SECOND_ID} is not: It is false")


def test_a_block_validating_one_that_breaks_the_half_free_rule_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    validation: ValidationBlock,
) -> None:
    paid = block_on(chain, paying(alice, {bob.node_id: 300}, fee=5, created=moment(2)))
    incorrect = transaction_check(paid, alice, chain, keys)

    found = checked(validation, bob, chain, keys, {SECOND_ID: incorrect})

    assert isinstance(incorrect, TransactionBlockCheck)
    assert incorrect.full
    assert isinstance(found, BlockFinding)
    assert found.builder == bob.node_id
    assert "must validate a correct transaction block" in found.statement
    assert found.statement.endswith("must be free")


def test_a_block_validating_one_not_full_as_held_is_not_correct_and_not_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    block: TransactionBlock,
    validation: ValidationBlock,
) -> None:
    small = transaction_check(block, alice, chain, keys, compressed_bytes=500)

    check = checked(validation, bob, chain, keys, {SECOND_ID: small})

    assert isinstance(small, TransactionBlockCheck)
    assert not small.full
    assert isinstance(check, ValidationBlockCheck)
    assert not check.is_correct
    assert check.score is None
    assert f"validate a full transaction block, and {SECOND_ID} is not" in check.incorrect


def test_a_block_larger_than_a_block_may_be_is_not_correct(
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
    validation: ValidationBlock,
) -> None:
    check = checked(validation, bob, chain, keys, transactions, compressed_bytes=1_001)

    assert isinstance(check, ValidationBlockCheck)
    assert check.incorrect == "A validation block must be at most 1000 bytes compressed, got 1001"


def test_a_block_naming_a_rival_at_its_target_as_its_runner_up_is_correct(
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    # A runner-up need not be correct.
    at_target = {**transactions, OTHER_ID: BlockFinding("It is false", alice.node_id)}
    rival = holding(chain, carol, validation_of(staked(bob, OTHER_ID, 100)))
    naming = validation_of(staked(alice, SECOND_ID, 1_000), runner_up=RUNNER_UP_ID)

    check = checked(naming, alice, rival, keys, at_target)

    assert isinstance(check, ValidationBlockCheck)
    assert check.score == ValidationScore(6, 1_000)


@mark.parametrize(
    "runner_up, statement",
    [
        (lambda bob: None, "not itself"),
        (lambda bob: validation_of(staked(bob, SECOND_ID, 1)), f"validates {SECOND_ID} too"),
        (lambda bob: validation_of(staked(bob, OTHER_ID, 1), generation=3), "at its target"),
        (lambda bob: validation_of(staked(bob, FIRST_ID, 1)), "at its target"),
        (lambda bob: validation_of(), "at its target"),
    ],
)
def test_a_block_naming_itself_or_one_not_a_rival_as_its_runner_up_is_false(
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
    runner_up: Callable[[NodeIdentity], ValidationBlock | None],
    statement: str,
) -> None:
    rival = runner_up(bob)
    named = VALIDATION_ID if rival is None else RUNNER_UP_ID
    held = chain if rival is None else holding(chain, carol, rival)
    at_target = {**transactions, OTHER_ID: BlockFinding("It is false", alice.node_id)}
    naming = validation_of(staked(alice, SECOND_ID, 1_000), runner_up=named)

    found = checked(naming, alice, held, keys, at_target)

    assert isinstance(found, BlockFinding)
    assert found.builder == alice.node_id
    assert "runner_up must" in found.statement
    assert statement in found.statement


def test_a_block_waits_for_its_runner_up(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    transactions: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    naming = validation_of(staked(alice, SECOND_ID, 1_000), runner_up=RIVAL)

    with raises(MissingContentError) as raised:
        checked(naming, alice, chain, keys, transactions)

    assert raised.value.content_ids == (RIVAL,)


@fixture(scope="module")
def at_root(
    alice: NodeIdentity, chain: ChainInMemory, keys: dict[ContentId, bytes], root: TransactionBlock
) -> dict[ContentId, TransactionBlockCheck | BlockFinding]:
    """What is at the root's transaction target: the root's block, checked correct."""
    check = TransactionBlockCheck.of(
        ROOT_ID, data_of(alice, root), None, chain, keys, compressed_bytes=1, bounds=BOUNDS
    )
    return {ROOT_ID: check}


def test_the_roots_validation_block_is_correct_by_its_shape(
    alice: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    root_validation: ValidationBlock,
    at_root: dict[ContentId, TransactionBlockCheck | BlockFinding],
) -> None:
    check = checked(
        root_validation, alice, chain, keys, at_root, block_id=ROOT_VALIDATION_ID, named_for=None
    )

    assert isinstance(check, ValidationBlockCheck)
    assert check.score == ValidationScore(5, 0)


@mark.parametrize(
    "shaped, built_by_bob",
    [
        (lambda alice, bob, root: replace(root, generation=1), False),
        (lambda alice, bob, root: replace(root, runner_up=RIVAL), False),
        (lambda alice, bob, root: replace(root, witnessed=("ab" * 32,)), False),
        (lambda alice, bob, root: validation_of(staked(alice, ROOT_ID, 1), generation=0), False),
        (lambda alice, bob, root: validation_of(staked(bob, ROOT_ID, 0), generation=0), False),
        (lambda alice, bob, root: validation_of(staked(alice, FIRST_ID, 0), generation=0), False),
        (lambda alice, bob, root: validation_of(generation=0), False),
        (
            lambda alice, bob, root: validation_of(
                staked(alice, ROOT_ID, 0), staked(bob, ROOT_ID, 0), generation=0
            ),
            False,
        ),
        (lambda alice, bob, root: root, True),
    ],
)
def test_a_block_at_the_roots_target_not_the_roots_is_passed_over(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    root_validation: ValidationBlock,
    at_root: dict[ContentId, TransactionBlockCheck | BlockFinding],
    shaped: Callable[[NodeIdentity, NodeIdentity, ValidationBlock], ValidationBlock],
    built_by_bob: bool,
) -> None:
    block = shaped(alice, bob, root_validation)
    builder = bob if built_by_bob else alice

    check = checked(
        block, builder, chain, keys, at_root, block_id=ROOT_VALIDATION_ID, named_for=None
    )

    assert isinstance(check, ValidationBlockCheck)
    assert not check.is_correct
    assert "the root's target is passed over" in check.incorrect


def test_a_block_at_the_roots_target_validating_a_false_root_is_passed_over(
    alice: NodeIdentity,
    bob: NodeIdentity,
    chain: ChainInMemory,
    keys: dict[ContentId, bytes],
    root_validation: ValidationBlock,
) -> None:
    false_root = {ROOT_ID: BlockFinding("It is false", bob.node_id)}

    check = checked(
        root_validation,
        alice,
        chain,
        keys,
        false_root,
        block_id=ROOT_VALIDATION_ID,
        named_for=None,
    )

    assert isinstance(check, ValidationBlockCheck)
    assert not check.is_correct


@mark.parametrize(
    "better, worse",
    [
        ((ValidationScore(7, 0), "0f"), (ValidationScore(6, 10**9), "01")),
        ((ValidationScore(6, 2_000), "0f"), (ValidationScore(6, 1_999), "01")),
        ((ValidationScore(6, 2_000), "01"), (ValidationScore(6, 2_000), "0f")),
    ],
)
def test_the_best_has_the_most_bits_then_the_most_stake_then_the_id_sorting_first(
    bob: NodeIdentity,
    validation: ValidationBlock,
    better: tuple[ValidationScore, str],
    worse: tuple[ValidationScore, str],
) -> None:
    drop = drop_of(bob, validation)
    best, other = (
        ranked(score, ContentId("sha256", digits * 32), drop) for score, digits in (better, worse)
    )

    assert ValidationBlockCheck.best([other, best]) is best
    assert ValidationBlockCheck.best([best, other]) is best


def test_a_block_not_correct_is_never_the_best(
    bob: NodeIdentity, validation: ValidationBlock
) -> None:
    drop = drop_of(bob, validation)
    passed_over = ranked(None, ContentId("sha256", "01" * 32), drop)
    correct = ranked(ValidationScore(0, 0), ContentId("sha256", "0f" * 32), drop)

    assert ValidationBlockCheck.best([passed_over, correct]) is correct
    assert ValidationBlockCheck.best([passed_over]) is None
    assert ValidationBlockCheck.best([]) is None


@mark.parametrize("score, incorrect", [(None, ""), (ValidationScore(6, 0), "Not full")])
def test_a_checked_block_is_either_scored_or_not_correct(
    bob: NodeIdentity,
    validation: ValidationBlock,
    score: ValidationScore | None,
    incorrect: str,
) -> None:
    with raises(ValueError, match="scored when it is correct"):
        ValidationBlockCheck(VALIDATION_ID, drop_of(bob, validation), score, incorrect)
