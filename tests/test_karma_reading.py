"""Tests for reading what is placed at a target (KarmaLedger §2)."""

from __future__ import annotations
from datetime import UTC, datetime
from hashlib import sha256
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
from libranet.karma.amounts import Kismet
from libranet.karma.blocks import BlockDrop, BlockFinding, ValidationBlock
from libranet.karma.generations import GenerationTargets
from libranet.karma.reading import HeldBlock, TargetReader
from libranet.karma.stakes import SignedStake, Stake
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.protocol.search import LocalSearch, SearchCache
from tests.helpers import new_identity

CREATED = datetime(2026, 10, 21, 14, 3, 22, tzinfo=UTC)
CAROL = ContentId("sha256", "c0" * 32)
WAIT_SECONDS = 30.0

# The fewest bits a search returns, fewer than a node's, so that a drop is
# placed in moments.
MINIMUM_BITS = 8

FIRST = GenerationTargets.after(ContentId("sha256", "f0" * 32))


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


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def placed(target: DropTarget, content: bytes) -> bytes:
    """``content`` as a drop at ``target``, sharing at least :data:`MINIMUM_BITS` with it."""
    nonce = 0

    while True:
        data = content + b"\x00" + str(nonce).encode("ascii")

        if matching_bits(target.hex, sha256(data).hexdigest()) >= MINIMUM_BITS:
            return data

        nonce += 1


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


@fixture(scope="module")
def alice() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def bob() -> NodeIdentity:
    return new_identity()


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
def cache(storage: StorageConfig) -> SearchCache:
    """The cached answers to searches, as the web server and the stats module write them."""
    return SearchCache.of(storage)


@fixture
def reader(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, clock: Clock
) -> TargetReader:
    return TargetReader(
        truth,
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        SearchCache.of(storage),
        ShunnedContent.of(storage),
        recorder,
        wait_seconds=WAIT_SECONDS,
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
    assert recorder.messages == [(EventType.SEARCH_REQUESTED, {"prefix": FIRST.validation.hex})]
    assert reader.waiting == frozenset()


def test_a_block_held_compressed_is_read_decompressed_and_measured_as_held(
    reader: TargetReader, truth: CasStore, staking: bytes
) -> None:
    stored = compress(staking, 1)
    truth.write(id_of(staking), stored)

    [block] = reader.read(FIRST.validation, ValidationBlock)

    assert block.data == staking
    assert block.compressed_bytes == len(stored)


@mark.parametrize("stored", [b"not it", compress(b"other content")])
def test_what_is_held_that_is_not_what_it_is_held_as_is_passed_over_and_logged(
    reader: TargetReader, truth: CasStore, caplog: LogCaptureFixture, stored: bytes
) -> None:
    forged = matching(FIRST.validation, 12)
    truth.write(forged, stored)

    with caplog.at_level(WARNING):
        assert reader.read(FIRST.validation, ValidationBlock) == []

    assert f"Passing over {forged} at Karma target {FIRST.validation.hex}" in caplog.text


def test_shunned_content_is_neither_read_nor_asked_for(
    storage: StorageConfig,
    reader: TargetReader,
    truth: CasStore,
    cache: SearchCache,
    recorder: Recorder,
    staking: bytes,
) -> None:
    block_id = hold(truth, staking)
    heard = matching(FIRST.validation, 20)
    cache.save_results(FIRST.validation.hex, [heard])
    write_atomically(storage.shunned_list_path, ShunnedContent.body([block_id, heard]))

    assert reader.read(FIRST.validation, ValidationBlock) == []

    reader.fetch([heard])

    assert recorder.asked_for() == []

    with raises(ShunnedContentError):
        reader.held(block_id)


def test_a_block_heard_of_and_not_held_is_asked_for_and_read_once_stored(
    reader: TargetReader,
    truth: CasStore,
    cache: SearchCache,
    recorder: Recorder,
    alice: NodeIdentity,
    bob: NodeIdentity,
    staking: bytes,
) -> None:
    block_id = id_of(staking)
    cache.save_results(FIRST.validation.hex, [block_id])
    hold_keys(truth, alice, bob)

    assert reader.read(FIRST.validation, ValidationBlock) == []
    assert recorder.asked_for() == [block_id]
    assert reader.waiting == {block_id}

    truth.write(block_id, staking)

    assert reader.stored(block_id)
    assert reader.waiting == frozenset()
    assert [block.block_id for block in reader.read(FIRST.validation, ValidationBlock)] == [
        block_id
    ]


def test_what_is_waited_for_is_asked_for_once(
    reader: TargetReader, cache: SearchCache, recorder: Recorder, staking: bytes
) -> None:
    block_id = id_of(staking)
    cache.save_results(FIRST.validation.hex, [block_id])

    reader.read(FIRST.validation, ValidationBlock)
    reader.read(FIRST.validation, ValidationBlock)
    reader.fetch([block_id])

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
    reader: TargetReader, cache: SearchCache, recorder: Recorder
) -> None:
    cache.save_results(FIRST.validation.hex, [matching(FIRST.validation, MINIMUM_BITS - 1)])

    assert reader.read(FIRST.validation, ValidationBlock) == []
    assert recorder.asked_for() == []


def test_a_reader_waits_as_long_as_its_node_is_configured_to(
    storage: StorageConfig, truth: CasStore, recorder: Recorder, clock: Clock
) -> None:
    config = LibranetConfig(storage=storage, karma=KarmaConfig(fetch_wait_seconds=5))
    reader = TargetReader.of(config, LayeredSource(truth), recorder, clock=clock)

    reader.fetch([CAROL])
    clock.now += 5

    assert reader.given_up() == [CAROL]


@mark.parametrize("wait_seconds, max_bytes", [(0, MIB), (WAIT_SECONDS, 0)])
def test_a_reader_must_wait_and_read_something(
    storage: StorageConfig, truth: CasStore, wait_seconds: float, max_bytes: int
) -> None:
    with raises(ValueError, match="must be positive"):
        TargetReader(
            truth,
            LocalSearch(truth),
            SearchCache.of(storage),
            ShunnedContent.of(storage),
            Recorder(),
            wait_seconds=wait_seconds,
            max_bytes=max_bytes,
        )
