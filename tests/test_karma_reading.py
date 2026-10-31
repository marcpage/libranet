"""Tests for reading what is placed at a target, and the chain it is built on (KarmaLedger §2)."""

from __future__ import annotations
from datetime import UTC, datetime
from fractions import Fraction
from logging import DEBUG, WARNING
from typing import Any, Mapping
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import MissingContentError, ShunnedContentError
from libranet.bundle.storing import STORAGE_COMPRESSION_LEVEL
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.layered import LayeredSource
from libranet.cas.prefix import matching_bits
from libranet.cas.shunned import ShunnedContent
from libranet.cas.store import CasStore
from libranet.config.models import MIB, KarmaConfig, LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.karma.blocks import (
    BlockBounds,
    BlockDrop,
    BlockFinding,
    TransactionBlock,
    ValidationBlock,
)
from libranet.karma.checking import BlockScore, ChainView, TransactionBlockCheck
from libranet.karma.generations import GenerationTargets
from libranet.karma.history import ChainGeneration, ChainHistory
from libranet.karma.reading import HeldBlock, HeldChain, TargetReader
from libranet.karma.validating import ValidationBlockCheck, ValidationScore
from libranet.karma.weight import ChainWeight
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import NO_BALANCE, Balance
from libranet.protocol.karma_stakes import SignedStake, Stake
from libranet.protocol.karma_transactions import (
    Payout,
    SignedTransaction,
    Transaction,
    TransactionKey,
)
from libranet.protocol.search import LocalSearch
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
CAROL = ContentId("sha256", "c0" * 32)
DAVE = ContentId("sha256", "d0" * 32)
PREVIOUS = ContentId("sha256", "5e" * 32)
WITNESS = "ab" * 32
WAIT_SECONDS = 30.0
ANSWER_SECONDS = 300.0

# The fewest bits a search returns, fewer than a node's, so that a drop is
# placed in moments.
MINIMUM_BITS = 8

# Bounds every transaction meets, whatever its work.
NO_WORK = BlockBounds(transaction_bits=0)

ROOT = GenerationTargets.root()
FIRST = GenerationTargets.after(ContentId("sha256", "f0" * 32))

# Every target these tests read. Nothing they hold is found at one by
# chance: each drop is found at its own alone, and no key at any.
TARGETS = (ROOT.transaction, ROOT.validation, FIRST.transaction, FIRST.validation)


class Recorder:
    """Stands in for the Karma module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def asked_for(self) -> list[ContentId]:
        """The content asked for, in the order it was."""
        return [
            ContentId.from_fields(payload)
            for event, payload in self.messages
            if event is EventType.DATA_NOT_FOUND
        ]

    def requested(self) -> list[ContentId]:
        """The content said to be requested, in the order it was, each said not to be external."""
        said = [payload for event, payload in self.messages if event is EventType.DATA_REQUESTED]
        assert not any(payload["external"] for payload in said)
        return [ContentId.from_fields(payload) for payload in said]


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def found_at(target: DropTarget, content_id: ContentId) -> bool:
    """Whether a search of ``target`` finds ``content_id``: they share :data:`MINIMUM_BITS`."""
    return matching_bits(target.hex, content_id.hash) >= MINIMUM_BITS


def placed(target: DropTarget, content: bytes) -> bytes:
    """``content`` as a drop at ``target``, sharing at least :data:`MINIMUM_BITS` with it.

    It shares fewer with every other of :data:`TARGETS`, so that a search of
    one does not find it there by chance.
    """
    nonce = 0

    while True:
        data = content + b"\x00" + str(nonce).encode("ascii")
        drop_id = id_of(data)

        if found_at(target, drop_id) and not any(
            found_at(other, drop_id) for other in TARGETS if other != target
        ):
            return data

        nonce += 1


def off_target_identity() -> NodeIdentity:
    """A new identity, whose key's id a search of none of :data:`TARGETS` finds.

    A key held whose id shares the few bits these tests search for with a
    target's hash, as one now and then does by chance, is read there as a
    block.
    """
    while True:
        identity = new_identity()

        if not any(found_at(target, identity.node_id) for target in TARGETS):
            return identity


def id_of(data: bytes) -> ContentId:
    """The id of ``data``."""
    return ContentId.for_data(data, "sha256")


def hold(truth: CasStore, data: bytes) -> ContentId:
    """Store ``data`` as it is, and give its id."""
    content_id = id_of(data)
    truth.write(content_id, data)
    return content_id


def hold_keys(truth: CasStore, *identities: NodeIdentity) -> None:
    """Store the public key of each of ``identities``."""
    for identity in identities:
        truth.write(identity.node_id, identity.public_key)


def matching(target: DropTarget, bits: int) -> ContentId:
    """An id sharing exactly ``bits`` leading bits with ``target``'s hash."""
    differing = int.from_bytes(target.digest, "big") ^ (1 << (255 - bits))
    return ContentId("sha256", differing.to_bytes(32, "big").hex())


def staked(staker: NodeIdentity, on: ContentId, kismet: int) -> SignedStake:
    """A stake ``staker`` signs, of ``kismet``, on the transaction block ``on``."""
    stake = Stake(staker.node_id, on, Kismet(kismet), CREATED)
    return SignedStake.of(staker.signed(stake.text()))


def root_of(creator: NodeIdentity) -> TransactionBlock:
    """The root's transaction block, holding ``creator``'s transaction sending themselves 0."""
    transaction = Transaction(creator.node_id, {creator.node_id: Kismet(0)}, Kismet(0), 0, CREATED)
    created = SignedTransaction.of(creator.signed(transaction.text()))
    return TransactionBlock(0, None, ChainWeight(0), 0, (created,), {creator.node_id: NO_BALANCE})


def stating(generation: int, reach: int, balances: Mapping[ContentId, Balance]) -> TransactionBlock:
    """Transaction block ``generation``, of ``reach``, stating ``balances``, and paying each."""
    payout = Payout.of(
        {None: Kismet(len(balances))}, {identity: Kismet(1) for identity in balances}, generation
    )
    return TransactionBlock(generation, PREVIOUS, ChainWeight(0), reach, (payout,), balances)


@fixture(scope="module")
def alice() -> NodeIdentity:
    return off_target_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return off_target_identity()


@fixture(scope="module")
def staking(alice: NodeIdentity, bob: NodeIdentity) -> bytes:
    """Validation block 1, which Alice built, holding Bob's stake, as a drop at its target."""
    block = ValidationBlock(1, None, (staked(bob, CAROL, 5),), ())
    return placed(FIRST.validation, alice.signed(block.text()).encoded())


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def reader(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, clock: Clock
) -> TargetReader:
    return TargetReader(
        truth,
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        ShunnedContent.of(storage),
        recorder,
        wait_seconds=WAIT_SECONDS,
        answer_seconds=ANSWER_SECONDS,
        max_bytes=MIB,
        clock=clock,
    )


def test_the_blocks_at_a_target_are_read_with_the_keys_of_all_who_signed_them(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = hold(truth, staking)
    hold_keys(truth, alice, bob)

    blocks = reader.read(FIRST.validation, ValidationBlock)

    keys = {alice.node_id: alice.public_key, bob.node_id: bob.public_key}
    compressed_bytes = len(compress(staking, STORAGE_COMPRESSION_LEVEL))
    assert blocks == [HeldBlock(block_id, staking, compressed_bytes, keys)]
    assert isinstance(BlockDrop.decoded(staking, ValidationBlock, blocks[0].keys), BlockDrop)
    assert recorder.messages[0] == (EventType.SEARCH_REQUESTED, {"prefix": FIRST.validation.hex})
    assert recorder.asked_for() == []
    assert reader.waiting == frozenset()


def test_a_block_held_compressed_is_read_decompressed_and_measured_as_held(
    reader: TargetReader, truth: CasStore, recorder: Recorder, staking: bytes
) -> None:
    stored = compress(staking, 1)
    truth.write(id_of(staking), stored)

    [block] = reader.read(FIRST.validation, ValidationBlock)

    assert block.data == staking
    assert block.compressed_bytes == len(stored)
    assert id_of(staking) in recorder.requested()


def test_what_is_read_is_said_to_be_requested_at_most_once_a_minute(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    clock: Clock,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    read = sorted([hold(truth, staking), alice.node_id, bob.node_id])
    hold_keys(truth, alice, bob)

    reader.read(FIRST.validation, ValidationBlock)
    reader.read(FIRST.validation, ValidationBlock)

    assert sorted(recorder.requested()) == read

    clock.now += 59.0
    reader.read(FIRST.validation, ValidationBlock)

    assert sorted(recorder.requested()) == read

    clock.now += 1.0
    reader.read(FIRST.validation, ValidationBlock)

    assert sorted(recorder.requested()) == sorted(read * 2)


def test_what_is_known_or_not_held_is_not_said_to_be_requested(
    reader: TargetReader, truth: CasStore, recorder: Recorder, staking: bytes
) -> None:
    block_id = hold(truth, staking)

    assert reader.read(FIRST.validation, ValidationBlock, known={block_id}) == []

    with raises(MissingContentError):
        reader.block(matching(FIRST.validation, 20), ValidationBlock)

    assert recorder.requested() == []


@mark.parametrize("stored", [b"not it", compress(b"other content")])
def test_what_is_held_that_is_not_what_it_is_held_as_is_passed_over_and_logged(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    caplog: LogCaptureFixture,
    stored: bytes,
) -> None:
    forged = matching(FIRST.validation, 12)
    truth.write(forged, stored)

    with caplog.at_level(WARNING):
        assert reader.read(FIRST.validation, ValidationBlock) == []

    assert f"Passing over {forged} at Karma target {FIRST.validation.hex}" in caplog.text
    assert recorder.requested() == []


def test_shunned_content_is_neither_read_nor_asked_for(
    storage: StorageConfig,
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    staking: bytes,
) -> None:
    block_id = hold(truth, staking)
    named = matching(FIRST.validation, 20)
    write_atomically(storage.shunned_list_path, ShunnedContent.body([block_id, named]))

    assert reader.read(FIRST.validation, ValidationBlock) == []
    assert not reader.answered(FIRST.validation.hex, [named])

    reader.fetch([named])

    assert recorder.asked_for() == []

    with raises(ShunnedContentError):
        reader.held(block_id)


def test_one_block_is_read_by_its_id_as_it_is_read_at_its_target(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = hold(truth, staking)
    hold_keys(truth, alice)

    held = reader.block(block_id, ValidationBlock)

    compressed_bytes = len(compress(staking, STORAGE_COMPRESSION_LEVEL))
    assert held == HeldBlock(block_id, staking, compressed_bytes, {alice.node_id: alice.public_key})
    assert recorder.asked_for() == [bob.node_id]


def test_one_block_not_held_is_asked_for_and_one_shunned_is_not(
    storage: StorageConfig, reader: TargetReader, recorder: Recorder, staking: bytes
) -> None:
    with raises(MissingContentError):
        reader.block(id_of(staking), ValidationBlock)

    assert recorder.asked_for() == [id_of(staking)]

    shunned = matching(FIRST.validation, 20)
    write_atomically(storage.shunned_list_path, ShunnedContent.body([shunned]))

    with raises(ShunnedContentError):
        reader.block(shunned, ValidationBlock)


def test_a_block_a_peer_names_is_asked_for_at_once_and_read_once_stored(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = id_of(staking)
    hold_keys(truth, alice, bob)

    assert reader.read(FIRST.validation, ValidationBlock) == []
    assert reader.answered(FIRST.validation.hex, [block_id])
    assert recorder.asked_for() == [block_id]
    assert reader.waiting == {block_id}

    truth.write(block_id, staking)

    assert reader.stored(block_id)
    assert reader.waiting == frozenset()
    assert [block.block_id for block in reader.read(FIRST.validation, ValidationBlock)] == [
        block_id
    ]


def test_a_block_found_at_a_target_but_lacked_is_awaited_until_a_read_holds_it(
    reader: TargetReader,
    truth: CasStore,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = id_of(staking)
    hold_keys(truth, alice, bob)
    reader.read(FIRST.validation, ValidationBlock)

    assert not reader.awaits(FIRST.validation)

    reader.answered(FIRST.validation.hex, [block_id])
    reader.read(FIRST.validation, ValidationBlock)

    assert reader.awaits(FIRST.validation)
    assert not reader.awaits(ROOT.validation)

    truth.write(block_id, staking)
    reader.read(FIRST.validation, ValidationBlock)

    assert not reader.awaits(FIRST.validation)


def test_a_block_found_at_a_target_but_lacked_is_awaited_as_long_as_one_is_waited_for(
    reader: TargetReader, clock: Clock, staking: bytes
) -> None:
    reader.read(FIRST.validation, ValidationBlock)
    reader.answered(FIRST.validation.hex, [id_of(staking)])
    reader.read(FIRST.validation, ValidationBlock)
    clock.now += WAIT_SECONDS - 1
    reader.read(FIRST.validation, ValidationBlock)

    assert reader.awaits(FIRST.validation)

    clock.now += 1
    reader.given_up()
    # Asked for again, but awaited no longer.
    reader.read(FIRST.validation, ValidationBlock)

    assert not reader.awaits(FIRST.validation)


def test_a_read_not_searching_reads_what_is_held_and_asks_no_peer(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    hold_keys(truth, alice, bob)
    truth.write(id_of(staking), staking)

    (held,) = reader.read(FIRST.validation, ValidationBlock, searching=False)

    assert held.block_id == id_of(staking)
    assert [event for event, _ in recorder.messages if event is EventType.SEARCH_REQUESTED] == []

    reader.read(FIRST.validation, ValidationBlock)

    assert [event for event, _ in recorder.messages if event is EventType.SEARCH_REQUESTED] == [
        EventType.SEARCH_REQUESTED
    ]


def test_a_block_known_is_neither_read_nor_asked_for_again(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = id_of(staking)
    hold_keys(truth, alice, bob)
    reader.read(FIRST.validation, ValidationBlock)
    reader.answered(FIRST.validation.hex, [block_id])
    # No longer waited for, so that a read would ask for it again.
    reader.stored(block_id)

    assert reader.read(FIRST.validation, ValidationBlock, known={block_id}) == []
    assert recorder.asked_for() == [block_id]
    assert not reader.awaits(FIRST.validation)

    reader.read(FIRST.validation, ValidationBlock)

    assert recorder.asked_for() == [block_id, block_id]

    truth.write(block_id, staking)

    assert reader.read(FIRST.validation, ValidationBlock, known={block_id}) == []


def test_a_read_knowing_a_block_another_lacks_leaves_its_wait_as_it_was(
    reader: TargetReader, clock: Clock, staking: bytes
) -> None:
    block_id = id_of(staking)
    reader.read(FIRST.validation, ValidationBlock)
    reader.answered(FIRST.validation.hex, [block_id])
    reader.read(FIRST.validation, ValidationBlock)
    clock.now += WAIT_SECONDS - 1
    reader.read(FIRST.validation, ValidationBlock, known={block_id})

    assert reader.awaits(FIRST.validation)
    assert not reader.awaits(FIRST.validation, known={block_id})

    clock.now += 1
    reader.given_up()
    reader.read(FIRST.validation, ValidationBlock)

    assert not reader.awaits(FIRST.validation)


def test_what_was_asked_for_is_the_ledgers_own_for_a_while_though_stored_since(
    reader: TargetReader, clock: Clock
) -> None:
    content_id = ContentId("sha256", "ab" * 32)

    assert not reader.asked_for(content_id)

    reader.fetch([content_id])
    reader.stored(content_id)

    assert reader.asked_for(content_id)

    clock.now += ANSWER_SECONDS - 1
    reader.given_up()

    assert reader.asked_for(content_id)

    clock.now += 1
    reader.given_up()

    assert not reader.asked_for(content_id)


def test_what_a_search_of_a_target_being_read_would_find_is_the_ledgers_own(
    reader: TargetReader,
) -> None:
    at_target = ContentId("sha256", FIRST.validation.hex)

    assert not reader.asked_for(at_target)

    reader.read(FIRST.validation, ValidationBlock)

    assert reader.asked_for(at_target)
    assert not reader.asked_for(ContentId("sha256", ROOT.validation.hex))


def test_what_is_waited_for_is_asked_for_once(
    reader: TargetReader, recorder: Recorder, staking: bytes
) -> None:
    block_id = id_of(staking)

    reader.read(FIRST.validation, ValidationBlock)

    assert reader.answered(FIRST.validation.hex, [block_id])
    # Named again, it is nothing new.
    assert not reader.answered(FIRST.validation.hex, [block_id])

    reader.read(FIRST.validation, ValidationBlock)
    reader.fetch([block_id])

    assert recorder.asked_for() == [block_id]


def test_what_peers_name_at_a_target_is_kept_while_it_is_read(
    reader: TargetReader, recorder: Recorder, clock: Clock, staking: bytes
) -> None:
    block_id = id_of(staking)
    reader.read(FIRST.validation, ValidationBlock)
    reader.answered(FIRST.validation.hex, [block_id])
    clock.now += WAIT_SECONDS
    reader.given_up()

    reader.read(FIRST.validation, ValidationBlock)

    assert recorder.asked_for() == [block_id, block_id]


def test_an_answer_for_a_target_not_being_read_is_passed_over(
    reader: TargetReader, recorder: Recorder, clock: Clock, staking: bytes
) -> None:
    block_id = id_of(staking)
    other = matching(FIRST.validation, 20)

    assert not reader.answered(FIRST.validation.hex, [block_id])

    reader.read(FIRST.validation, ValidationBlock)
    reader.answered(FIRST.validation.hex, [block_id])
    clock.now += ANSWER_SECONDS

    assert not reader.answered(FIRST.validation.hex, [other])

    reader.given_up()
    reader.read(FIRST.validation, ValidationBlock)

    # What was named before is forgotten, as the target was not read.
    assert recorder.asked_for() == [block_id]


def test_what_is_not_stored_in_time_is_given_up_on_and_asked_for_again(
    reader: TargetReader, recorder: Recorder, clock: Clock, caplog: LogCaptureFixture
) -> None:
    reader.fetch([CAROL])
    clock.now += WAIT_SECONDS - 1

    assert reader.given_up() == []

    clock.now += 1

    with caplog.at_level(DEBUG, logger="libranet.karma.reading"):
        assert reader.given_up() == [CAROL]

    assert f"Gave up waiting for {CAROL}, not stored within 30 seconds" in caplog.text
    assert reader.waiting == frozenset()
    assert not reader.stored(CAROL)

    reader.fetch([CAROL])

    assert recorder.asked_for() == [CAROL, CAROL]


def test_what_is_held_is_not_asked_for(
    reader: TargetReader, truth: CasStore, recorder: Recorder, alice: NodeIdentity
) -> None:
    hold_keys(truth, alice)

    reader.fetch([alice.node_id])

    assert recorder.asked_for() == []
    assert not reader.stored(alice.node_id)


def test_a_signers_key_not_held_is_asked_for_and_its_block_read_without_it(
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    hold(truth, staking)
    hold_keys(truth, alice)

    [block] = reader.read(FIRST.validation, ValidationBlock)

    assert block.keys == {alice.node_id: alice.public_key}
    assert recorder.asked_for() == [bob.node_id]
    assert reader.waiting == {bob.node_id}

    with raises(MissingContentError) as raised:
        BlockDrop.decoded(block.data, ValidationBlock, block.keys)

    assert raised.value.content_ids == (bob.node_id,)


def test_a_drop_holding_no_block_is_read_with_its_builders_key_or_none(
    reader: TargetReader, truth: CasStore, recorder: Recorder, alice: NodeIdentity
) -> None:
    signed = hold(truth, placed(FIRST.validation, alice.signed("Not a block").encoded()))
    unsigned = hold(truth, placed(FIRST.validation, b"Not signed"))
    hold_keys(truth, alice)

    blocks = reader.read(FIRST.validation, ValidationBlock)

    assert {block.block_id: block.keys for block in blocks} == {
        signed: {alice.node_id: alice.public_key},
        unsigned: {},
    }
    assert recorder.asked_for() == []

    for block in blocks:
        found = BlockDrop.decoded(block.data, ValidationBlock, block.keys)

        assert isinstance(found, BlockFinding)
        assert found.builder == (alice.node_id if block.block_id == signed else None)


def test_what_shares_too_few_bits_with_the_target_is_neither_read_nor_asked_for(
    reader: TargetReader, recorder: Recorder
) -> None:
    reader.read(FIRST.validation, ValidationBlock)

    assert not reader.answered(FIRST.validation.hex, [matching(FIRST.validation, MINIMUM_BITS - 1)])
    assert reader.read(FIRST.validation, ValidationBlock) == []
    assert recorder.asked_for() == []


def test_a_reader_waits_as_long_as_its_node_is_configured_to(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, clock: Clock
) -> None:
    config = LibranetConfig(storage=storage, karma=KarmaConfig(fetch_wait_seconds=5))
    reader = TargetReader.of(config, LayeredSource(truth), recorder, clock=clock)

    reader.fetch([CAROL])
    reader.read(FIRST.validation, ValidationBlock)
    clock.now += 5

    assert reader.given_up() == [CAROL]

    clock.now += storage.search_cache_ttl_seconds - 5

    assert not reader.answered(FIRST.validation.hex, [matching(FIRST.validation, 30)])


@mark.parametrize(
    "wait_seconds, answer_seconds, max_bytes",
    [(0, ANSWER_SECONDS, MIB), (WAIT_SECONDS, 0, MIB), (WAIT_SECONDS, ANSWER_SECONDS, 0)],
)
def test_a_reader_must_wait_keep_answers_and_read_something(
    storage: StorageConfig,
    truth: CasStore,
    wait_seconds: float,
    answer_seconds: float,
    max_bytes: int,
) -> None:
    with raises(ValueError, match="must be positive"):
        TargetReader(
            truth,
            LocalSearch(truth),
            ShunnedContent.of(storage),
            Recorder(),
            wait_seconds=wait_seconds,
            answer_seconds=answer_seconds,
            max_bytes=max_bytes,
        )


@fixture
def history(storage: StorageConfig, truth: CasStore, alice: NodeIdentity) -> ChainHistory:
    """Fourteen generations, from Alice's root, each transaction block held and stating Alice's.

    Block 1 states Dave holding 7 Kismet, and block 2 Carol holding nothing,
    having won in generation 1. Blocks 2 to 12 have a reach of 0, and block
    13 one of 12. Alice's key is in block 1, and a hash is witnessed in
    generation 2.
    """
    blocks = [
        root_of(alice),
        stating(1, 1, {alice.node_id: Balance(Kismet(2)), DAVE: Balance(Kismet(7))}),
        stating(2, 0, {alice.node_id: Balance(Kismet(9)), CAROL: Balance(Kismet(0), 1)}),
        *(
            stating(generation, 0, {alice.node_id: Balance(Kismet(generation))})
            for generation in range(3, 13)
        ),
        stating(13, 12, {alice.node_id: Balance(Kismet(13))}),
    ]
    history = ChainHistory.open(storage.karma_dir)

    for generation, block in enumerate(blocks):
        history.add(
            ChainGeneration(
                generation,
                ContentId("sha256", f"{generation + 1:02x}" * 32),
                hold(truth, alice.signed(block.text()).encoded() + b"\x007"),
                ChainWeight(generation),
                (TransactionKey(alice.node_id, CREATED),) if generation == 1 else (),
                (WITNESS,) if generation == 2 else (),
            )
        )

    return history


@fixture
def chain(history: ChainHistory, reader: TargetReader) -> HeldChain:
    return HeldChain(history, reader)


def transaction_id(history: ChainHistory, generation: int) -> ContentId:
    """The id of transaction block ``generation`` of ``history``."""
    return history.generation(generation).transaction


def test_the_chain_reads_each_block_as_its_builder_signed_it(
    chain: HeldChain, truth: CasStore, alice: NodeIdentity, bob: NodeIdentity
) -> None:
    validation = ValidationBlock(1, None, (staked(bob, CAROL, 5),), ())
    validation_id = hold(truth, alice.signed(validation.text()).encoded() + b"\x007")

    assert chain.validation(validation_id).block == validation
    assert chain.validation(validation_id).builder == alice.node_id
    assert chain.stated(1) == {alice.node_id: Balance(Kismet(2)), DAVE: Balance(Kismet(7))}


def test_a_block_evicted_is_asked_for_again_and_read_once_stored(
    chain: HeldChain,
    reader: TargetReader,
    truth: CasStore,
    recorder: Recorder,
    history: ChainHistory,
) -> None:
    evicted = transaction_id(history, 1)
    data = truth.read(evicted)
    truth.delete(evicted)

    with raises(MissingContentError) as raised:
        chain.transaction(evicted)

    assert raised.value.content_ids == (evicted,)
    assert recorder.asked_for() == [evicted]

    truth.write(evicted, data)

    assert reader.stored(evicted)
    assert chain.transaction(evicted).block.generation == 1


def test_a_balance_is_read_back_ten_blocks_past_a_reach_of_0(
    chain: HeldChain,
    history: ChainHistory,
    truth: CasStore,
    recorder: Recorder,
    alice: NodeIdentity,
) -> None:
    truth.delete(transaction_id(history, 1))

    # Carol's last win, in block 2, is read; Dave, in block 1, is too far back.
    assert chain.balances({alice.node_id, CAROL, DAVE}, 12) == {
        alice.node_id: Balance(Kismet(12)),
        CAROL: Balance(Kismet(0), 1),
        DAVE: NO_BALANCE,
    }
    assert recorder.asked_for() == []


def test_a_balance_is_read_back_through_a_reach_past_ten_blocks(
    chain: HeldChain, alice: NodeIdentity
) -> None:
    unstated = ContentId("sha256", "e0" * 32)

    assert chain.balances({alice.node_id, CAROL, DAVE, unstated}, 13) == {
        alice.node_id: Balance(Kismet(13)),
        CAROL: Balance(Kismet(0), 1),
        DAVE: Balance(Kismet(7)),
        unstated: NO_BALANCE,
    }


def test_a_balance_near_the_root_is_read_back_to_it(chain: HeldChain) -> None:
    assert chain.balances({DAVE}, 3) == {DAVE: Balance(Kismet(7))}


def test_every_balance_stated_back_through_the_reach_is_read_the_latest_of_each(
    chain: HeldChain, alice: NodeIdentity
) -> None:
    assert chain.every_balance(13) == {
        alice.node_id: Balance(Kismet(13)),
        CAROL: Balance(Kismet(0), 1),
        DAVE: Balance(Kismet(7)),
    }


def test_every_balance_is_read_back_ten_blocks_past_a_reach_of_0(
    chain: HeldChain, alice: NodeIdentity
) -> None:
    # Dave, stated in block 1 alone, is too far back.
    assert chain.every_balance(12) == {
        alice.node_id: Balance(Kismet(12)),
        CAROL: Balance(Kismet(0), 1),
    }


def test_every_balance_waits_for_each_block_it_is_read_from(
    chain: HeldChain, history: ChainHistory, truth: CasStore, recorder: Recorder
) -> None:
    truth.delete(transaction_id(history, 1))

    with raises(MissingContentError) as raised:
        chain.every_balance(13)

    assert raised.value.content_ids == (transaction_id(history, 1),)
    assert recorder.asked_for() == [transaction_id(history, 1)]


def test_every_block_lacking_that_far_back_is_named_at_once_and_asked_for(
    chain: HeldChain, history: ChainHistory, truth: CasStore, recorder: Recorder
) -> None:
    lacking = [transaction_id(history, 3), transaction_id(history, 2)]

    for content_id in lacking:
        truth.delete(content_id)

    with raises(MissingContentError) as raised:
        chain.balances({CAROL}, 12)

    assert list(raised.value.content_ids) == lacking
    assert recorder.asked_for() == lacking


def test_the_latest_block_lacking_is_named_alone(
    chain: HeldChain, history: ChainHistory, truth: CasStore
) -> None:
    truth.delete(transaction_id(history, 12))
    truth.delete(transaction_id(history, 11))

    with raises(MissingContentError) as raised:
        chain.balances({CAROL}, 12)

    assert raised.value.content_ids == (transaction_id(history, 12),)


def test_keys_and_witnesses_are_read_from_the_history(
    chain: HeldChain, alice: NodeIdentity
) -> None:
    key = TransactionKey(alice.node_id, CREATED)

    assert chain.has_key(key, 0, 3)
    assert not chain.has_key(key, 2, 3)
    assert chain.witnessed(WITNESS) == 2
    assert chain.witnessed("cd" * 32) is None


def test_the_roots_blocks_read_at_their_targets_are_checked_against_the_chain_held(
    storage: StorageConfig, reader: TargetReader, truth: CasStore, alice: NodeIdentity
) -> None:
    root_id = hold(truth, placed(ROOT.transaction, alice.signed(root_of(alice).text()).encoded()))
    validation = ValidationBlock(0, None, (staked(alice, root_id, 0),), ())
    validation_id = hold(truth, placed(ROOT.validation, alice.signed(validation.text()).encoded()))
    hold_keys(truth, alice)
    chain: ChainView = HeldChain(ChainHistory.open(storage.karma_dir), reader)

    [held] = reader.read(ROOT.transaction, TransactionBlock)
    transaction = TransactionBlockCheck.of_held(held, None, chain, bounds=NO_WORK)
    [held] = reader.read(ROOT.validation, ValidationBlock)
    validated = ValidationBlockCheck.of_held(
        held, None, chain, transactions={root_id: transaction}, bounds=NO_WORK
    )

    root_bits = matching_bits(ROOT.transaction.hex, root_id.hash)
    assert isinstance(transaction, TransactionBlockCheck)
    assert transaction.score == BlockScore(root_bits, 0, Fraction(0))
    assert isinstance(validated, ValidationBlockCheck)
    assert validated.score == ValidationScore(
        matching_bits(ROOT.validation.hex, validation_id.hash), 0
    )
