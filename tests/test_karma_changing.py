"""Tests for changing to a heavier chain (KarmaLedger §3.5.3)."""

from __future__ import annotations
from dataclasses import replace
from logging import ERROR, INFO, WARNING

from pytest import LogCaptureFixture, fixture

from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import ValidationBlock
from libranet.karma.carried import CARRIED_FILE, CarriedTransactions
from libranet.karma.changing import HeadChanges
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.reading import TargetReader
from libranet.karma.weight import ChainWeight
from libranet.messaging.events import EventType
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity
from tests.karma_chains import (
    BOUNDS,
    MINIMUM_BITS,
    Chain,
    Recorder,
    hold,
    id_of,
    paying,
    placed,
    signed_at,
    staked,
    validating,
)

PEER = ContentId("sha256", "9e" * 32)


def shunned(recorder: Recorder, keys: set[ContentId]) -> list[ContentId]:
    """What ``recorder`` says was shunned, but ``keys``, the ids of public keys.

    The follower reads a key the node holds as an unsigned block, and shuns
    it, when the key's id shares the few bits these tests search for with a
    target's hash, as one now and then does by chance.
    """
    return [
        content_id
        for content_id in map(ContentId.from_fields, recorder.said(EventType.DATA_SHUNNED))
        if content_id not in keys
    ]


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
def root(alice: NodeIdentity) -> Chain:
    """Alice's root, each drop sharing 9 bits with its target."""
    return Chain.root(alice, 9)


@fixture(scope="module")
def followed(root: Chain, alice: NodeIdentity, bob: NodeIdentity) -> Chain:
    """Two generations on Alice's root that Bob builds, the second paying Bob twice."""
    first = root.then(bob, alice, bits=9)
    block = first.block_after(paying(alice, bob, 1), paying(alice, bob, 2))
    return first.then(bob, alice, block, bits=9)


@fixture(scope="module")
def heavier(root: Chain, alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity) -> Chain:
    """Three generations on Alice's root that Carol builds, the second paying Bob again."""
    first = root.then(carol, alice, bits=9)
    second = first.then(carol, alice, first.block_after(paying(alice, bob, 2)), bits=9)
    return second.then(carol, alice, bits=9)


@fixture(scope="module")
def keys(alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity) -> set[ContentId]:
    """The ids of the public keys of Alice, Bob, and Carol."""
    return {alice.node_id, bob.node_id, carol.node_id}


@fixture
def truth(
    storage: StorageConfig, alice: NodeIdentity, bob: NodeIdentity, carol: NodeIdentity
) -> CasStore:
    """The node's store, holding the keys of Alice, Bob, and Carol."""
    store = CasStore.source_of_truth(storage)

    for identity in (alice, bob, carol):
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
    return ChainFollower(history, reader, recorder, bounds=BOUNDS)


@fixture
def carried(storage: StorageConfig) -> CarriedTransactions:
    return CarriedTransactions(storage.karma_dir, 3)


@fixture
def changes(
    history: ChainHistory, reader: TargetReader, recorder: Recorder, carried: CarriedTransactions
) -> HeadChanges:
    return HeadChanges(history, reader, recorder, carried, bounds=BOUNDS)


def following(follower: ChainFollower, truth: CasStore, chain: Chain) -> None:
    """Have ``follower`` follow ``chain``, whose blocks the node then holds."""
    hold(truth, *chain.drops(*range(len(chain.generations))))

    assert len(follower.look()) == len(chain.generations)


def test_a_lighter_head_is_read_and_nothing_more(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    root: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root.then(carol, alice, bits=12).then(carol, alice, bits=12))
    lighter = root.then(carol, alice, bits=9).then(carol, alice, bits=9)
    hold(truth, *lighter.drops(2))
    genealogy = [history.generation(generation) for generation in range(3)]
    changes.told(PEER, lighter.head)

    assert not changes.look()
    assert [history.generation(generation) for generation in range(3)] == genealogy
    assert recorder.asked_for() == []


def test_a_false_weight_is_caught_where_it_is_first_stated_nothing_before_it_asked_for(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, root)
    two = root.then(carol, alice).then(carol, alice)
    lying = two.then(carol, alice, replace(two.block_after(), weight=ChainWeight(2**40)))
    hold(truth, *lying.drops(2, 3))
    changes.told(PEER, lying.head)

    with caplog.at_level(INFO):
        assert not changes.look()

    false = id_of(lying.generations[3].transaction)
    statement = f'A transaction block\'s "weight" must be {two.weight().value()}, got {2**40}'
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == [
        {"block": str(false), "builder": str(carol.node_id), "staker": None, "statement": statement}
    ]
    assert shunned(recorder, keys) == [false]
    assert recorder.asked_for() == []
    assert len(history) == 1
    assert f"Not following the Karma chain through {lying.head}: {false} is not taken" in (
        caplog.text
    )


def test_a_heavier_chain_is_followed_in_place_of_the_history_after_the_ancestor(
    follower: ChainFollower,
    changes: HeadChanges,
    storage: StorageConfig,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    followed: Chain,
    heavier: Chain,
) -> None:
    following(follower, truth, followed)
    hold(truth, *heavier.drops(1, 2, 3))
    changes.told(PEER, heavier.head)

    assert changes.look()

    validations = [id_of(generation.validation) for generation in heavier.generations]
    assert [history.generation(generation).validation for generation in range(4)] == validations
    assert history.head is not None and history.head.weight == heavier.weight()
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []

    reopened = ChainHistory.open(storage.karma_dir)
    assert [reopened.generation(generation) for generation in range(4)] == [
        history.generation(generation) for generation in range(4)
    ]


def test_the_transactions_of_the_chain_left_are_carried_those_met_struck(
    follower: ChainFollower,
    changes: HeadChanges,
    carried: CarriedTransactions,
    truth: CasStore,
    followed: Chain,
    heavier: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, followed)
    hold(truth, *heavier.drops(1, 2, 3))
    changes.told(PEER, heavier.head)
    changes.look()

    [kept] = carried.read()
    assert (kept.signed, kept.witnessed) == (paying(alice, bob, 1), 2)


def test_what_is_carried_is_carried_no_more_once_the_chain_goes_past_it(
    follower: ChainFollower,
    changes: HeadChanges,
    carried: CarriedTransactions,
    truth: CasStore,
    followed: Chain,
    heavier: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, followed)
    hold(truth, *heavier.drops(1, 2, 3))
    changes.told(PEER, heavier.head)
    changes.look()
    on = heavier.then(carol, alice, bits=9)
    hold(truth, *on.drops(4))
    follower.look()
    changes.look()

    assert len(carried.read()) == 1

    past = on.then(carol, alice, bits=9)
    hold(truth, *past.drops(5))
    follower.look()
    changes.look()

    assert carried.read() == []


def test_a_chain_waiting_for_a_block_is_followed_once_it_comes(
    follower: ChainFollower,
    changes: HeadChanges,
    reader: TargetReader,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    followed: Chain,
    heavier: Chain,
) -> None:
    following(follower, truth, followed)
    hold(truth, *heavier.drops(2, 3))
    changes.told(PEER, heavier.head)

    assert not changes.look()

    lacking = id_of(heavier.generations[1].validation)
    assert lacking in recorder.asked_for()

    hold(truth, *heavier.drops(1))
    reader.stored(lacking)

    assert changes.look()
    assert history.head is not None and history.head.validation == heavier.head


def test_a_chain_given_up_on_is_weighed_no_more_while_the_head_is_the_same(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    followed: Chain,
    heavier: Chain,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, followed)
    hold(truth, *heavier.drops(2, 3))
    changes.told(PEER, heavier.head)
    changes.look()

    with caplog.at_level(INFO):
        changes.given_up([id_of(heavier.generations[1].validation)])

    hold(truth, *heavier.drops(1))

    assert not changes.look()
    assert history.head is not None and history.head.validation == followed.head
    assert f"Not weighing Karma head {heavier.head} further" in caplog.text


def test_a_block_that_does_not_check_says_its_builder_false_and_the_head_is_kept(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    followed: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, followed)
    second = root.then(carol, alice, bits=9).then(carol, alice, bits=9)
    block = second.block_after()
    misstated = {
        identity: replace(balance, kismet=balance.kismet + Kismet(1))
        for identity, balance in block.balances.items()
    }
    false = second.then(carol, alice, replace(block, balances=misstated), bits=9)
    hold(truth, *false.drops(1, 2, 3))
    changes.told(PEER, false.head)

    assert not changes.look()

    [said] = recorder.said(EventType.KARMA_FALSE_BLOCK)
    assert (said["block"], said["builder"]) == (
        str(id_of(false.generations[3].transaction)),
        str(carol.node_id),
    )
    assert shunned(recorder, keys) == []
    assert history.head is not None and history.head.validation == followed.head


def test_a_better_validation_block_of_the_latest_generation_is_followed_as_heavier(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    first = root.then(bob, alice, bits=9)
    following(follower, truth, first)
    better = ValidationBlock(
        1, None, (staked(alice, id_of(first.generations[1].transaction), 6),), ()
    )
    better_drop = signed_at(root.targets.validation, alice, better, 12)
    hold(truth, better_drop)

    assert changes.look()
    assert history.generation(1).validation == id_of(better_drop)
    assert history.generation(1).transaction == id_of(first.generations[1].transaction)
    assert not changes.look()


def test_one_only_as_heavy_is_not_followed(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    first = root.then(bob, alice, bits=9)
    following(follower, truth, first)
    other = ValidationBlock(
        1, None, (staked(alice, id_of(first.generations[1].transaction), 6),), ()
    )
    hold(truth, signed_at(root.targets.validation, alice, other, 9))

    assert not changes.look()
    assert history.generation(1).validation == first.head


def test_a_heavier_root_is_followed_in_place_of_the_root_followed(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    root: Chain,
    bob: NodeIdentity,
) -> None:
    following(follower, truth, root)
    heavier = Chain.root(bob, 12)
    hold(truth, *heavier.drops(0))

    assert changes.look()
    assert len(history) == 1
    assert history.generation(0).validation == heavier.head


def test_a_head_told_again_while_the_head_is_the_same_is_not_weighed_again(
    follower: ChainFollower,
    changes: HeadChanges,
    truth: CasStore,
    recorder: Recorder,
    root: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root)
    two = root.then(carol, alice).then(carol, alice)
    lying = two.then(carol, alice, replace(two.block_after(), weight=ChainWeight(2**40)))
    hold(truth, *lying.drops(2, 3))
    changes.told(PEER, lying.head)
    changes.look()
    changes.told(PEER, lying.head)
    changes.look()

    assert len(recorder.said(EventType.KARMA_FALSE_BLOCK)) == 1


def test_a_block_whose_signature_fails_is_shunned_and_no_one_said_false(
    follower: ChainFollower,
    changes: HeadChanges,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root)
    two = root.then(carol, alice).then(carol, alice)
    lying = replace(two.block_after(), weight=ChainWeight(2**40))
    # Bob signs it, and names Carol as its signer.
    forged = replace(bob.signed(lying.text()), signer=carol.node_id)
    transaction = placed(two.targets.transaction, forged.encoded())
    stake = ValidationBlock(3, None, (staked(alice, id_of(transaction), 5),), ())
    validation = signed_at(two.targets.validation, alice, stake)
    hold(truth, *two.drops(2), transaction, validation)
    changes.told(PEER, id_of(validation))

    assert not changes.look()
    assert shunned(recorder, keys) == [id_of(transaction)]
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []


def test_a_false_generation_is_caught_as_the_walk_reads_it(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    alice: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root)
    two = root.then(carol, alice).then(carol, alice)
    lying = two.then(carol, alice, generation=5)
    hold(truth, *lying.drops(2, 3))
    changes.told(PEER, lying.head)

    assert not changes.look()

    [said] = recorder.said(EventType.KARMA_FALSE_BLOCK)
    assert (said["block"], said["builder"], said["statement"]) == (
        str(lying.head),
        str(alice.node_id),
        f'A validation block\'s "generation" must be 3, one more than that of {two.head}, '
        f"got 5",
    )
    assert shunned(recorder, keys) == [lying.head]
    assert len(history) == 1


def test_a_head_holding_no_stake_is_said_false_and_not_shunned(
    follower: ChainFollower,
    changes: HeadChanges,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root)
    stakeless = signed_at(root.targets.validation, carol, ValidationBlock(1, None, (), ()))
    hold(truth, stakeless)
    changes.told(PEER, id_of(stakeless))

    assert not changes.look()

    [said] = recorder.said(EventType.KARMA_FALSE_BLOCK)
    assert (said["block"], said["builder"], said["statement"]) == (
        str(id_of(stakeless)),
        str(carol.node_id),
        "A validation block must hold a stake",
    )
    assert shunned(recorder, keys) == []


def test_a_head_that_is_no_block_is_passed_over_and_logged(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    keys: set[ContentId],
    root: Chain,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, root)
    junk = b"Not a block"
    hold(truth, junk)
    changes.told(PEER, id_of(junk))

    with caplog.at_level(WARNING):
        assert not changes.look()

    assert (
        f"Not following the Karma chain through {id_of(junk)}, which cannot be checked"
        in caplog.text
    )
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []
    assert shunned(recorder, keys) == []
    assert len(history) == 1


def test_a_validation_block_naming_a_runner_up_at_its_target_states_nothing_false(
    follower: ChainFollower,
    changes: HeadChanges,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
    carol: NodeIdentity,
) -> None:
    following(follower, truth, root.then(bob, alice, bits=9))
    first = root.then(carol, alice, bits=9)
    targets = first.targets
    rival = signed_at(targets.transaction, bob, first.block_after(), 9)
    runner_up = validating(targets.validation, alice, rival, bits=9)
    transaction = signed_at(targets.transaction, carol, first.block_after(), 9)
    validation = validating(targets.validation, alice, transaction, runner_up=runner_up, bits=9)
    hold(truth, *first.drops(1), rival, runner_up, transaction, validation)
    changes.told(PEER, id_of(validation))

    assert changes.look()
    assert history.generation(2).validation == id_of(validation)
    assert recorder.said(EventType.KARMA_FALSE_BLOCK) == []


def test_a_block_of_the_chain_left_not_held_has_none_carried_and_is_logged(
    follower: ChainFollower,
    changes: HeadChanges,
    carried: CarriedTransactions,
    history: ChainHistory,
    truth: CasStore,
    followed: Chain,
    heavier: Chain,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, followed)
    evicted = id_of(followed.generations[2].transaction)
    truth.delete(evicted)
    hold(truth, *heavier.drops(1, 2, 3))
    changes.told(PEER, heavier.head)

    with caplog.at_level(WARNING):
        assert changes.look()

    assert f"Carrying over none of the transactions of Karma block {evicted}" in caplog.text
    assert carried.read() == []
    assert history.head is not None and history.head.validation == heavier.head


def test_what_cannot_be_carried_is_logged(
    follower: ChainFollower,
    changes: HeadChanges,
    storage: StorageConfig,
    history: ChainHistory,
    truth: CasStore,
    followed: Chain,
    heavier: Chain,
    caplog: LogCaptureFixture,
) -> None:
    following(follower, truth, followed)
    (storage.karma_dir / CARRIED_FILE).mkdir()
    hold(truth, *heavier.drops(1, 2, 3))
    changes.told(PEER, heavier.head)

    with caplog.at_level(ERROR):
        assert changes.look()

    assert "Cannot carry over Karma transactions" in caplog.text
    assert history.head is not None and history.head.validation == heavier.head
