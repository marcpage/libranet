"""Tests for the chain a head claims, as it is weighed (KarmaLedger §3.5.3)."""

from __future__ import annotations
from dataclasses import replace

from pytest import fixture, raises

from libranet.bundle.errors import MissingContentError
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import BlockFinding, ValidationBlock
from libranet.karma.claims import ClaimedChain, ClaimedGeneration, FalseStatement, HeadClaim
from libranet.karma.following import ChainFollower
from libranet.karma.history import ChainHistory
from libranet.karma.reading import HeldChain, TargetReader
from libranet.karma.weight import ChainWeight
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
    signed_at,
    validating,
)


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def root(alice: NodeIdentity) -> Chain:
    """Alice's root."""
    return Chain.root(alice)


@fixture(scope="module")
def claimed(root: Chain, alice: NodeIdentity, bob: NodeIdentity) -> Chain:
    """Three generations on Alice's root that Bob builds, the second paying him."""
    first = root.then(bob, alice)
    second = first.then(bob, alice, first.block_after(paying(alice, bob, 1)))
    return second.then(bob, alice)


@fixture
def truth(storage: StorageConfig, alice: NodeIdentity, bob: NodeIdentity) -> CasStore:
    """The node's store, holding the keys of Alice and Bob."""
    store = CasStore.source_of_truth(storage)

    for identity in (alice, bob):
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
def history(
    storage: StorageConfig, truth: CasStore, reader: TargetReader, root: Chain
) -> ChainHistory:
    """The history of a node following Alice's root."""
    followed = ChainHistory.open(storage.karma_dir)
    hold(truth, *root.drops(0))
    ChainFollower(followed, reader, Recorder(), bounds=BOUNDS).look()
    return followed


@fixture
def chain(history: ChainHistory, reader: TargetReader) -> HeldChain:
    return HeldChain(history, reader)


def test_a_head_is_read_with_the_block_it_validates_as_they_claim(
    chain: HeldChain, truth: CasStore, claimed: Chain
) -> None:
    hold(truth, *claimed.drops(3))
    read = ClaimedGeneration.read(chain, claimed.head)

    assert read == ClaimedGeneration(
        claimed.head,
        3,
        id_of(claimed.generations[3].transaction),
        3,
        id_of(claimed.generations[2].validation),
        claimed.transaction(3).weight,
        claimed.weight(),
    )


def test_a_head_holding_no_stake_states_so_falsely_and_is_not_invalid(
    chain: HeldChain, truth: CasStore, root: Chain, bob: NodeIdentity
) -> None:
    stakeless = signed_at(root.targets.validation, bob, ValidationBlock(1, None, (), ()))
    hold(truth, stakeless)

    assert ClaimedGeneration.read(chain, id_of(stakeless)) == FalseStatement(
        id_of(stakeless), "A validation block must hold a stake", invalid=False
    )


def test_the_walk_comes_to_the_common_ancestor_and_reads_nothing_before_it(
    history: ChainHistory, chain: HeldChain, truth: CasStore, recorder: Recorder, claimed: Chain
) -> None:
    hold(truth, *claimed.drops(1, 2, 3))
    claim = HeadClaim(claimed.head)
    claim.claimed(chain)
    walked = claim.walk(history, chain)

    assert isinstance(walked, ClaimedChain) and walked.ancestor == 0
    assert [generation.generation for generation in claim.walked] == [3, 2, 1]
    assert recorder.asked_for() == []


def test_the_walk_waits_for_a_block_it_lacks_and_goes_on_from_it(
    history: ChainHistory, chain: HeldChain, truth: CasStore, claimed: Chain
) -> None:
    hold(truth, *claimed.drops(2, 3))
    claim = HeadClaim(claimed.head)
    claim.claimed(chain)

    with raises(MissingContentError) as lacking:
        claim.walk(history, chain)

    assert lacking.value.content_ids == (id_of(claimed.generations[1].validation),)
    assert len(claim.walked) == 2

    hold(truth, *claimed.drops(1))

    assert isinstance(claim.walk(history, chain), ClaimedChain)


def test_a_chain_on_another_root_is_walked_to_its_root(
    history: ChainHistory, chain: HeldChain, truth: CasStore, alice: NodeIdentity, bob: NodeIdentity
) -> None:
    other = Chain.root(bob).then(alice, bob)
    hold(truth, *other.drops(0, 1))
    claim = HeadClaim(other.head)
    claim.claimed(chain)
    walked = claim.walk(history, chain)

    assert isinstance(walked, ClaimedChain) and walked.ancestor == -1


def test_a_false_weight_is_found_where_it_is_first_stated(
    history: ChainHistory,
    chain: HeldChain,
    truth: CasStore,
    recorder: Recorder,
    claimed: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    two = Chain(claimed.generations[:3])
    lying = two.then(bob, alice, replace(two.block_after(), weight=ChainWeight(7)))
    hold(truth, *lying.drops(2, 3))
    claim = HeadClaim(lying.head)
    claim.claimed(chain)

    assert claim.walk(history, chain) == FalseStatement(
        id_of(lying.generations[3].transaction),
        f'A transaction block\'s "weight" must be {two.weight().value()}, got 7',
    )
    assert recorder.asked_for() == []


def test_each_generation_is_checked_against_the_validation_block_before() -> None:
    previous = ContentId("sha256", "aa" * 32)
    walked = ClaimedGeneration(
        ContentId("sha256", "cc" * 32),
        2,
        ContentId("sha256", "bb" * 32),
        2,
        previous,
        ChainWeight(5),
        ChainWeight(9),
    )
    named = f"one more than that of {previous}"

    assert walked.false_on(1, ChainWeight(5)) is None
    assert replace(walked, generation=3).false_on(1, ChainWeight(5)) == FalseStatement(
        walked.transaction, f'A transaction block\'s "generation" must be 2, {named}, got 3'
    )
    assert replace(walked, validation_generation=4).false_on(1, ChainWeight(5)) == (
        FalseStatement(
            walked.validation, f'A validation block\'s "generation" must be 2, {named}, got 4'
        )
    )
    assert walked.false_on(1, ChainWeight(6)) == FalseStatement(
        walked.transaction, 'A transaction block\'s "weight" must be 6, got 5'
    )


def test_a_root_is_built_on_none_at_generation_0_and_weight_0() -> None:
    walked = ClaimedGeneration(
        ContentId("sha256", "cc" * 32),
        0,
        ContentId("sha256", "bb" * 32),
        0,
        None,
        ChainWeight(0),
        ChainWeight(9),
    )

    assert walked.false_on(-1, ChainWeight(0)) is None
    assert replace(walked, generation=1).false_on(-1, ChainWeight(0)) == FalseStatement(
        walked.transaction, "A transaction block's \"generation\" must be 0, the root's, got 1"
    )


def test_a_claim_checked_forward_holds_each_generation_correct(
    history: ChainHistory, chain: HeldChain, reader: TargetReader, truth: CasStore, claimed: Chain
) -> None:
    hold(truth, *claimed.drops(1, 2, 3))
    claim = HeadClaim(claimed.head)
    claim.claimed(chain)
    walked = claim.walk(history, chain)
    assert isinstance(walked, ClaimedChain)

    assert claim.check(walked, reader, bounds=BOUNDS) is None
    assert [generation.validation for generation in walked.generations] == [
        id_of(generation.validation) for generation in claimed.generations[1:]
    ]
    assert walked.generations[-1].weight == claimed.weight()
    assert walked.generation(0) == history.generation(0)
    assert walked.generation(3) == walked.generations[-1]

    with raises(IndexError):
        walked.generation(4)


def test_a_claim_checked_forward_names_the_first_block_not_correct(
    history: ChainHistory,
    chain: HeldChain,
    reader: TargetReader,
    truth: CasStore,
    claimed: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    first = Chain(claimed.generations[:2])
    block = first.block_after()
    misstated = {
        identity: replace(balance, kismet=balance.kismet + Kismet(1))
        for identity, balance in block.balances.items()
    }
    false = first.then(bob, alice, replace(block, balances=misstated)).then(bob, alice)
    hold(truth, *false.drops(1, 2, 3))
    claim = HeadClaim(false.head)
    claim.claimed(chain)
    walked = claim.walk(history, chain)
    assert isinstance(walked, ClaimedChain)

    failed = claim.check(walked, reader, bounds=BOUNDS)

    assert failed is not None and failed[0] == id_of(false.generations[2].transaction)
    assert isinstance(failed[1], BlockFinding) and failed[1].builder == bob.node_id
    assert len(walked.generations) == 1


def test_a_runner_up_staking_on_a_block_built_on_the_same_is_at_its_target(
    history: ChainHistory,
    chain: HeldChain,
    reader: TargetReader,
    truth: CasStore,
    root: Chain,
    alice: NodeIdentity,
    bob: NodeIdentity,
) -> None:
    targets = root.targets
    rival = signed_at(targets.transaction, alice, root.block_after())
    runner_up = validating(targets.validation, alice, rival)
    transaction = signed_at(targets.transaction, bob, root.block_after())
    validation = validating(targets.validation, alice, transaction, runner_up=runner_up)
    hold(truth, rival, runner_up, transaction, validation)
    claim = HeadClaim(id_of(validation))
    claim.claimed(chain)
    walked = claim.walk(history, chain)
    assert isinstance(walked, ClaimedChain)

    assert claim.check(walked, reader, bounds=BOUNDS) is None


def test_the_claimed_chain_holds_the_keys_and_witnesses_of_its_own_generations(
    history: ChainHistory, chain: HeldChain, reader: TargetReader, truth: CasStore, claimed: Chain
) -> None:
    hold(truth, *claimed.drops(1, 2, 3))
    claim = HeadClaim(claimed.head)
    claim.claimed(chain)
    walked = claim.walk(history, chain)
    assert isinstance(walked, ClaimedChain)
    [paid] = claimed.transaction(2).signed

    assert not walked.has_key(paid.transaction.key, 0, 3)

    claim.check(walked, reader, bounds=BOUNDS)

    assert walked.has_key(paid.transaction.key, 0, 3)
    assert not walked.has_key(paid.transaction.key, 3, 3)
    assert walked.witnessed(paid.hash) is None
