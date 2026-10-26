"""Tests for staking, and building validation blocks of the stakes (KarmaLedger §6)."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from logging import INFO, WARNING

from pytest import LogCaptureFixture, fixture

from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import KIB, MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import BlockBounds, BlockDrop, ValidationBlock
from libranet.karma.carried import CarriedTransaction, CarriedTransactions
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.staking import HeadStakes, StakeCover, ValidationBuilder
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import SignedTransaction, Transaction
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import (
    CREATED,
    MINIMUM_BITS,
    Chain,
    Recorder,
    hold,
    id_of,
    signed_at,
    staked,
    validating,
)

# Bounds in which every transaction meets the work, and a block of more than
# 100 bytes compressed, up to 64 KiB, is full.
ROOMY = BlockBounds(most_bytes=64 * KIB, room_bytes=64 * KIB - 100, transaction_bits=0)

# When the node signs a stake.
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=UTC)


class Placed:
    """Stands in for the Karma module's storing, keeping each drop in the node's store."""

    def __init__(self, truth: CasStore) -> None:
        self.truth = truth
        self.drops: list[bytes] = []

    def __call__(self, data: bytes, node_id: ContentId) -> None:
        self.truth.write(id_of(data), data)
        self.drops.append(data)

    def blocks(self) -> list[ValidationBlock]:
        """The validation blocks placed, in the order they were."""
        return [BlockDrop.parsed(data, ValidationBlock).block for data in self.drops]


@fixture(scope="module")
def alice() -> NodeIdentity:
    """The node, which holds the Karma of the root and of generation 1."""
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def carol() -> NodeIdentity:
    """A node holding nothing."""
    return new_identity()


@fixture(scope="module")
def dave() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(alice: NodeIdentity) -> Chain:
    """Alice's root, each drop sharing 9 bits with its target."""
    return Chain.root(alice, 9)


@fixture(scope="module")
def first(root: Chain, alice: NodeIdentity, bob: NodeIdentity) -> Chain:
    """A generation on Alice's root that Bob builds, and Alice validates."""
    return root.then(bob, alice, bits=9)


@fixture
def truth(
    storage: StorageConfig,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
) -> CasStore:
    """The node's store, holding the keys of Alice, Bob, Carol, and Dave."""
    store = CasStore.source_of_truth(storage)

    for identity in (alice, bob, carol, dave):
        store.write(identity.node_id, identity.public_key)

    return store


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def reader(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> TargetReader:
    return TargetReader(
        truth,
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        ShunnedContent.of(storage),
        recorder,
        wait_seconds=30.0,
        answer_seconds=300.0,
        max_bytes=MIB,
    )


@fixture
def history(storage: StorageConfig) -> ChainHistory:
    return ChainHistory.open(storage.karma_dir)


@fixture
def follower(history: ChainHistory, reader: TargetReader, recorder: Recorder) -> ChainFollower:
    return ChainFollower(history, reader, recorder, bounds=ROOMY)


@fixture
def chain(history: ChainHistory, reader: TargetReader) -> HeldChain:
    return HeldChain(history, reader)


@fixture
def distrusted() -> set[ContentId]:
    """Whom the node distrusts, which a test adds to."""
    return set()


@fixture
def stakes(history: ChainHistory, reader: TargetReader, distrusted: set[ContentId]) -> HeadStakes:
    """What stakes a tenth of what covers the node, at NOW."""
    return HeadStakes(
        history,
        reader,
        distrusts=distrusted.__contains__,
        stake_percent=10.0,
        clock=NOW.timestamp,
    )


def following(follower: ChainFollower, truth: CasStore, chain: Chain) -> None:
    """Have ``follower`` follow ``chain`` through its head, its blocks then held."""
    hold(truth, *chain.drops(*range(len(chain.generations))))
    follower.look()

    assert follower.look() == []


def sent(
    sender: NodeIdentity, to: dict[ContentId, int], seconds: int, *, fee: int = 0
) -> SignedTransaction:
    """``sender``'s transaction paying ``to``, ``seconds`` after CREATED."""
    transaction = Transaction(
        sender.node_id,
        {identity: Kismet(amount) for identity, amount in to.items()},
        Kismet(fee),
        1,
        CREATED + timedelta(seconds=seconds),
    )
    return SignedTransaction.of(sender.signed(transaction.text()))


def put_up(
    truth: CasStore,
    chain: Chain,
    builder: NodeIdentity,
    *signed: SignedTransaction,
    bits: int | None = None,
) -> bytes:
    """``signed``, put up by ``builder`` as a block on ``chain``, full, held.

    Its drop shares ``bits`` with its target.
    """
    data = signed_at(chain.targets.transaction, builder, chain.block_after(*signed), bits)
    hold(truth, data)
    return data


def stakers(taken: tuple[SignedStake, ...]) -> list[ContentId]:
    """Who staked each of ``taken``, sorted."""
    return sorted(signed.staker for signed in taken)


def test_the_node_stakes_its_share_of_what_covers_it_on_the_best_block_at_the_target(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    signed = [sent(alice, {bob.node_id: 100}, 1), sent(alice, {dave.node_id: 1}, 2, fee=7)]
    data = put_up(truth, first, dave, *signed)
    # What Alice holds as the block leaves her, less her fee there.
    covering = first.block_after(*signed).balances[alice.node_id].kismet.count - 7

    with caplog.at_level(INFO):
        taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and taken.validated_id == id_of(data)
    assert taken.cover.covering(alice.node_id) == Kismet(covering)
    (own,) = taken.stakes
    assert own.stake == Stake(alice.node_id, id_of(data), Kismet(covering // 10), NOW)
    own.verified(alice.public_key)
    assert f"Staking {covering // 10} of the {covering} Kismet that cover the node" in caplog.text


def test_of_blocks_scoring_alike_the_one_whose_id_sorts_first_is_staked_on(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    block = first.block_after(sent(alice, {bob.node_id: 1}, 1))
    target = first.targets.transaction
    alike = [signed_at(target, builder, block, 9) for builder in (bob, dave)]
    hold(truth, signed_at(target, alice, block, 8), *alike)

    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and taken.validated_id == min(map(id_of, alike))


def test_no_block_a_distrusted_identity_built_is_staked_on(
    stakes: HeadStakes,
    distrusted: set[ContentId],
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    block = first.block_after(sent(alice, {bob.node_id: 1}, 1))
    target = first.targets.transaction
    better, worse = signed_at(target, dave, block, 10), signed_at(target, bob, block, 8)
    hold(truth, better, worse)
    distrusted.add(dave.node_id)

    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and taken.validated_id == id_of(worse)

    distrusted.add(bob.node_id)

    assert stakes.staked(follower.checked()[0], alice) is None


def test_nothing_is_staked_while_no_block_at_the_target_is_correct(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    # A paid transaction alone breaks the half-free rule.
    put_up(truth, first, dave, sent(alice, {bob.node_id: 1}, 1, fee=1))

    assert stakes.staked(follower.checked()[0], alice) is None


def test_a_node_nothing_covers_stakes_nothing_and_says_so_once_at_a_head(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    put_up(truth, first, dave, sent(alice, {bob.node_id: 1}, 1))

    with caplog.at_level(INFO):
        assert stakes.staked(follower.checked()[0], carol) is None
        assert stakes.staked(follower.checked()[0], carol) is None

    assert caplog.text.count("Not staking on Karma transaction block") == 1


def test_nothing_is_staked_while_no_chain_is_followed(
    stakes: HeadStakes, alice: NodeIdentity
) -> None:
    assert stakes.staked({}, alice) is None


def test_others_stakes_on_the_block_are_taken_but_those_of_whom_the_node_distrusts(
    stakes: HeadStakes,
    distrusted: set[ContentId],
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    data = put_up(truth, first, carol, sent(alice, {bob.node_id: 100, dave.node_id: 100}, 1))
    target = first.targets.validation
    hold(truth, validating(target, bob, data), validating(target, dave, data))
    distrusted.add(dave.node_id)

    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None
    assert stakers(taken.stakes) == sorted([alice.node_id, bob.node_id])
    assert staked(bob, id_of(data), 5) in taken.stakes


def test_a_stake_of_the_nodes_own_held_at_the_target_is_its_stake(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    data = put_up(truth, first, dave, sent(alice, {bob.node_id: 1}, 1))
    hold(truth, validating(first.targets.validation, alice, data))

    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and taken.stakes == (staked(alice, id_of(data), 5),)


def test_a_stake_whose_signature_fails_is_passed_over_and_logged_once_at_a_head(
    stakes: HeadStakes,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    data = put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 1))
    stake = Stake(bob.node_id, id_of(data), Kismet(5), CREATED)
    # Signed as Bob, with Dave's key.
    forged = SignedStake.of(SignedBundle.signed(stake.text(), dave.private_key, bob.node_id))
    hold(truth, signed_at(first.targets.validation, bob, ValidationBlock(2, None, (forged,), ())))

    with caplog.at_level(WARNING):
        taken = stakes.staked(follower.checked()[0], alice)
        stakes.staked(follower.checked()[0], alice)

    assert taken is not None and stakers(taken.stakes) == [alice.node_id]
    assert caplog.text.count(f"Passing over Karma stake {forged.hash}") == 1


def test_a_stake_whose_stakers_key_is_not_held_is_taken_once_it_is(
    stakes: HeadStakes,
    recorder: Recorder,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    erin = new_identity()
    data = put_up(truth, first, dave, sent(alice, {erin.node_id: 100}, 1))
    hold(truth, validating(first.targets.validation, erin, data))

    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and stakers(taken.stakes) == [alice.node_id]
    assert erin.node_id in recorder.asked_for()

    truth.write(erin.node_id, erin.public_key)
    taken = stakes.staked(follower.checked()[0], alice)

    assert taken is not None and stakers(taken.stakes) == sorted([alice.node_id, erin.node_id])


def test_of_a_stakers_stakes_the_largest_are_taken_first_while_covered_together(
    chain: HeldChain,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    signed = sent(alice, {bob.node_id: 100}, 1)
    data = put_up(truth, first, dave, signed)
    cover = StakeCover.of(first.block_after(signed), [bob.node_id], chain)
    offered = [staked(bob, id_of(data), kismet) for kismet in (30, 60, 50, 101)]

    assert cover.covering(bob.node_id) == Kismet(100)
    assert cover.taken([*offered, offered[0]]) == tuple(
        sorted(offered[:2], key=lambda each: each.hash)
    )


@fixture
def carried(storage: StorageConfig) -> CarriedTransactions:
    return CarriedTransactions(storage.karma_dir, 3)


@fixture
def placed(truth: CasStore) -> Placed:
    return Placed(truth)


def building(
    history: ChainHistory,
    follower: ChainFollower,
    reader: TargetReader,
    carried: CarriedTransactions,
    node: NodeIdentity,
    placed: Placed,
    bounds: BlockBounds = ROOMY,
) -> ValidationBuilder:
    """What builds validation blocks as ``node``, staking a tenth, placing each drop in moments."""
    return ValidationBuilder(
        history,
        follower,
        reader,
        carried,
        lambda: node,
        store=placed,
        distrusts=lambda identity: False,
        stake_percent=10.0,
        block_seconds=0.0,
        drop_bits=MINIMUM_BITS,
        clock=NOW.timestamp,
        bounds=bounds,
    )


@fixture
def builder(
    history: ChainHistory,
    follower: ChainFollower,
    reader: TargetReader,
    carried: CarriedTransactions,
    alice: NodeIdentity,
    placed: Placed,
) -> ValidationBuilder:
    return building(history, follower, reader, carried, alice, placed)


def test_a_validation_block_of_the_stakes_is_built_and_the_chain_followed_through_it(
    builder: ValidationBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, first)
    data = put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 1))

    with caplog.at_level(INFO):
        builder.build()

    (drop,) = placed.drops
    built = BlockDrop.parsed(drop, ValidationBlock)
    assert built.builder == alice.node_id
    assert (built.block.generation, built.block.runner_up, built.block.witnessed) == (2, None, ())
    assert [signed.stake.transaction_block for signed in built.block.stakes] == [id_of(data)]
    assert f"Built Karma validation block {id_of(drop)} of 1 stakes and 0 witnesses" in caplog.text
    (followed,) = follower.look()
    assert (followed.validation, followed.transaction) == (id_of(drop), id_of(data))


def test_nothing_is_built_while_no_stake_is_taken(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 1))

    building(history, follower, reader, carried, carol, placed).build()

    assert placed.drops == []


def test_nothing_is_built_while_no_chain_is_followed(
    builder: ValidationBuilder, placed: Placed
) -> None:
    builder.build()

    assert placed.drops == []


def test_a_block_is_built_again_only_once_stakes_come_that_those_held_lack(
    builder: ValidationBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    data = put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 1))
    builder.build()
    builder.build()

    assert len(placed.drops) == 1

    hold(truth, validating(first.targets.validation, bob, data))
    builder.build()
    builder.build()

    assert [stakers(block.stakes) for block in placed.blocks()] == [
        [alice.node_id],
        sorted([alice.node_id, bob.node_id]),
    ]


def test_a_rival_whose_stakes_the_block_would_take_covered_is_named_its_runner_up(
    builder: ValidationBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 1), bits=10)
    other = put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 2), bits=8)
    rival = validating(first.targets.validation, bob, other)
    hold(truth, rival)

    builder.build()

    (drop,) = placed.drops
    assert BlockDrop.parsed(drop, ValidationBlock).block.runner_up == id_of(rival)
    # Step 122 finds it correct.
    assert id_of(drop) in [check.block_id for check in follower.checked()[1] if check.is_correct]


def test_no_runner_up_is_named_when_no_rival_is_covered(
    builder: ValidationBuilder,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    following(follower, truth, first)
    # Bob holds nothing as the block staked on leaves him.
    put_up(truth, first, dave, sent(alice, {dave.node_id: 100}, 1), bits=10)
    other = put_up(truth, first, dave, sent(alice, {bob.node_id: 100}, 2), bits=8)
    hold(truth, validating(first.targets.validation, bob, other))

    builder.build()

    (block,) = placed.blocks()
    assert block.runner_up is None


def test_the_block_lists_what_is_carried_and_seen_that_the_chain_has_not_witnessed(
    builder: ValidationBuilder,
    placed: Placed,
    carried: CarriedTransactions,
    history: ChainHistory,
    follower: ChainFollower,
    truth: CasStore,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    # pylint: disable=too-many-locals
    old = sent(alice, {bob.node_id: 1}, 1)
    witnessing = root.then(bob, alice, bits=9, witnessed=(old.hash,))
    following(follower, truth, witnessing)
    taken, seen, unseen = (sent(alice, {bob.node_id: 1}, seconds) for seconds in (2, 3, 4))
    put_up(truth, witnessing, dave, taken, bits=10)
    put_up(truth, witnessing, dave, seen, bits=8)
    carried.carry([CarriedTransaction(each) for each in (unseen, old, taken)], history)

    builder.build()

    (block,) = placed.blocks()
    assert block.witnessed == tuple(sorted([seen.hash, unseen.hash]))


def test_witnesses_are_listed_as_room_allows_those_carried_first(
    history: ChainHistory,
    reader: TargetReader,
    carried: CarriedTransactions,
    placed: Placed,
    follower: ChainFollower,
    truth: CasStore,
    first: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    dave: NodeIdentity,
) -> None:
    # pylint: disable=too-many-locals
    following(follower, truth, first)
    taken = sent(alice, {bob.node_id: 1}, 1)
    seen, unseen = sorted(
        (sent(alice, {bob.node_id: 1}, seconds) for seconds in (2, 3)),
        key=lambda signed: signed.hash,
    )
    data = put_up(truth, first, dave, taken, bits=10)
    put_up(truth, first, dave, seen, bits=8)
    carried.carry([CarriedTransaction(unseen)], history)
    covering = first.block_after(taken).balances[alice.node_id].kismet.count
    own = Stake(alice.node_id, id_of(data), Kismet(covering // 10), NOW)
    one = ValidationBlock(2, None, (SignedStake.of(alice.signed(own.text())),), (unseen.hash,))
    # Room for one witness, and not two.
    most = BlockDrop(alice.signed(one.text()), one).measured_bytes()
    bounds = BlockBounds(most_bytes=most, room_bytes=most, transaction_bits=0)

    building(history, follower, reader, carried, alice, placed, bounds).build()

    assert placed.blocks() == [one]
