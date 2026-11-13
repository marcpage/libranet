"""Tests for distrusting whoever built a false Karma block, in this node's own list."""

from __future__ import annotations
from json import dumps
from logging import DEBUG, WARNING
from typing import Any, Callable, Mapping

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.trust import HeldTrustList, TrustLimits, TrustList, TrustScore
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.modules import ModuleName
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.false_builders import FalseBlock, FalseBuilders
from libranet.webserver.http_types import Request
from libranet.webserver.node_trust_handlers import OwnTrust
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_handlers import TrustEntry

from tests.helpers import new_identity

BLOCK = ContentId("sha256", "b1" * 32)
BUILDER = ContentId.for_data(b"The builder's public key", "sha256")
STAKER = ContentId.for_data(b"The staker's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
STATEMENT = "its weight is 7, not 8"
MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 20
# The fewest bits the search returns, fewer than a node's, to be quick to match.
MINIMUM_BITS = 16
RETRY_SECONDS = 5


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class NoLists:
    """Stands in for the trust list reader of the node's own build, which holds no list."""

    def read(self, owner: ContentId) -> HeldTrustList:
        return HeldTrustList(owner)


@fixture(scope="module")
def node() -> NodeIdentity:
    return new_identity()


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def uploads(
    storage: StorageConfig, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> OwnUploads:
    return OwnUploads.of(storage, truth, node.node_id, recorder)


@fixture
def builds(recorder: Recorder) -> TrustBuilds:
    """A build of the node's own scores, which reads nothing unless a test runs it."""
    return TrustBuilds(
        NoLists(),
        recorder,
        TrustLimits(4, 1000),
        wait_seconds=0,
        ask_again_seconds=RETRY_SECONDS,
        in_background=lambda job: None,
    )


@fixture
def trust(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
    builds: TrustBuilds,
) -> OwnTrust:
    """The node's trust endpoints over what is held, as ``/config``'s port makes them."""
    search = SearchHandler(
        LocalSearch(truth, min_bits=MINIMUM_BITS), SearchCache.of(storage), recorder
    )
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)
    return OwnTrust.of(
        node,
        search,
        drops,
        recorder,
        content=uploads,
        retry_after_seconds=RETRY_SECONDS,
        builds=builds,
    )


@fixture
def builders(trust: OwnTrust, clock: Clock) -> FalseBuilders:
    """What distrusts the builders of false blocks by ``trust``, at once, on the test's thread."""
    return FalseBuilders(trust, retry_seconds=RETRY_SECONDS, clock=clock, in_background=at_once)


def at_once(job: Callable[[], None]) -> None:
    """Run ``job`` now, on this thread, rather than on a thread of its own."""
    job()


def false_block(builder: ContentId = BUILDER, staker: ContentId | None = None) -> Message:
    """What the Karma module says of ``BLOCK``, found false, built by ``builder``."""
    return make_message(
        EventType.KARMA_FALSE_BLOCK,
        ModuleName.KARMA,
        {
            "block": str(BLOCK),
            "builder": str(builder),
            "staker": None if staker is None else str(staker),
            "statement": STATEMENT,
        },
    )


def entries(trust: OwnTrust, node: NodeIdentity) -> Mapping[ContentId, bool]:
    """Whom the newest version of the node's list, as held, trusts and distrusts."""
    return trust.changes.held(node.node_id, node).entries


def versions_placed(recorder: Recorder, node: NodeIdentity) -> int:
    """How many versions of its list this node uploaded: every upload but its key."""
    return sum(
        placed["hash"] != node.node_id.hash for placed in recorder.payloads(EventType.PUT_COMPLETED)
    )


def held_version(truth: CasStore, node: NodeIdentity, trust_list: TrustList) -> ContentId:
    """``trust_list``, signed by ``node``, stored in ``truth`` as a drop at its list's."""
    drop = TrustList.drop_target(node.node_id).placed(
        node.signed(dumps(trust_list.value())).encoded(), 0, minimum_bits=MINIMUM_BITS
    )
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def found_not_held(
    storage: StorageConfig, truth: CasStore, node: NodeIdentity, trust_list: TrustList
) -> bytes:
    """``trust_list``, as a version of the node's list a search found, and not held.

    Returns:
        The drop, for a test to store once the version is to be held.
    """
    content_id = held_version(truth, node, trust_list)
    data = truth.read(content_id)
    truth.path_for(content_id).unlink()
    SearchCache.of(storage).save_results(TrustList.drop_target(node.node_id).hex, [content_id])
    return data


def test_a_false_block_has_the_node_distrust_its_builder(
    builders: FalseBuilders,
    trust: OwnTrust,
    node: NodeIdentity,
    recorder: Recorder,
    caplog: LogCaptureFixture,
) -> None:
    with caplog.at_level(WARNING):
        builders.found(false_block())

    assert entries(trust, node) == {BUILDER: False}
    assert versions_placed(recorder, node) == 1
    assert f"Distrusting {BUILDER}" in caplog.text
    assert str(BLOCK) in caplog.text
    assert STATEMENT in caplog.text


def test_the_version_placed_is_handed_to_the_node_s_own_build(
    builders: FalseBuilders, node: NodeIdentity, builds: TrustBuilds
) -> None:
    builds.session_started(node.node_id)

    builders.found(false_block())

    scores = builds.scores(node.node_id)
    assert scores is not None
    assert scores.scores == {BUILDER: TrustScore(-1, 1)}


def test_the_staker_of_a_stake_over_what_covers_it_is_distrusted_too(
    builders: FalseBuilders, trust: OwnTrust, node: NodeIdentity, recorder: Recorder
) -> None:
    builders.found(false_block(staker=STAKER))

    assert entries(trust, node) == {BUILDER: False, STAKER: False}
    assert versions_placed(recorder, node) == 2


def test_a_builder_staking_in_its_own_block_is_distrusted_once(
    builders: FalseBuilders, trust: OwnTrust, node: NodeIdentity, recorder: Recorder
) -> None:
    builders.found(false_block(staker=BUILDER))

    assert entries(trust, node) == {BUILDER: False}
    assert versions_placed(recorder, node) == 1


def test_one_distrusted_already_has_nothing_placed(
    builders: FalseBuilders,
    trust: OwnTrust,
    node: NodeIdentity,
    recorder: Recorder,
    caplog: LogCaptureFixture,
) -> None:
    builders.found(false_block())
    caplog.clear()

    with caplog.at_level(DEBUG):
        # As the Karma module says it again once it restarts.
        builders.found(false_block())

    assert entries(trust, node) == {BUILDER: False}
    assert versions_placed(recorder, node) == 1
    assert f"{BUILDER}, which signed Karma block {BLOCK}, is distrusted already" in caplog.text


def test_one_the_operator_trusted_is_distrusted_over_it(
    builders: FalseBuilders, trust: OwnTrust, node: NodeIdentity
) -> None:
    trust.placed(Request("PUT", "/config/api/trust"), TrustEntry(BUILDER, True))
    trust.placed(Request("PUT", "/config/api/trust"), TrustEntry(DAVE, True))

    builders.found(false_block())

    assert entries(trust, node) == {BUILDER: False, DAVE: True}


def test_the_node_itself_is_never_distrusted(
    builders: FalseBuilders,
    trust: OwnTrust,
    node: NodeIdentity,
    recorder: Recorder,
    caplog: LogCaptureFixture,
) -> None:
    with caplog.at_level(WARNING):
        builders.found(false_block(node.node_id, staker=STAKER))

    assert entries(trust, node) == {STAKER: False}
    assert versions_placed(recorder, node) == 1
    assert f"Not distrusting this node, which signed Karma block {BLOCK}" in caplog.text


def test_a_distrust_waits_while_a_version_found_is_not_held(
    storage: StorageConfig,
    builders: FalseBuilders,
    trust: OwnTrust,
    truth: CasStore,
    node: NodeIdentity,
    recorder: Recorder,
    clock: Clock,
) -> None:
    drop = found_not_held(storage, truth, node, TrustList({DAVE: True}, 1))

    builders.found(false_block())

    assert versions_placed(recorder, node) == 0
    (asked,) = recorder.payloads(EventType.DATA_NOT_FOUND)
    clock.now += RETRY_SECONDS - 1
    builders.check()
    # Not tried again before it is time.
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [asked]

    clock.now += 1
    builders.check()
    # Asked for again, as it is still not held.
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [asked, asked]
    assert versions_placed(recorder, node) == 0

    truth.write(ContentId.from_fields(asked), drop)
    clock.now += RETRY_SECONDS
    builders.check()

    assert entries(trust, node) == {DAVE: True, BUILDER: False}
    assert versions_placed(recorder, node) == 1
    # Done with, so not tried again.
    clock.now += RETRY_SECONDS
    builders.check()
    assert versions_placed(recorder, node) == 1


def test_those_after_one_waiting_wait_behind_it_and_are_then_distrusted_in_order(
    storage: StorageConfig,
    builders: FalseBuilders,
    trust: OwnTrust,
    truth: CasStore,
    node: NodeIdentity,
    recorder: Recorder,
    clock: Clock,
) -> None:
    drop = found_not_held(storage, truth, node, TrustList({DAVE: True}, 1))
    builders.found(false_block())
    (asked,) = recorder.payloads(EventType.DATA_NOT_FOUND)

    builders.found(false_block(STAKER))

    # Not tried at once, behind the one waiting.
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [asked]
    truth.write(ContentId.from_fields(asked), drop)
    clock.now += RETRY_SECONDS
    builders.check()

    held = trust.changes.held(node.node_id, node)
    assert held.entries == {DAVE: True, BUILDER: False, STAKER: False}
    assert held.newest is not None
    assert held.newest.trust_list.version == 3
    assert versions_placed(recorder, node) == 2


def test_a_change_that_is_refused_is_logged_and_not_tried_again(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
    clock: Clock,
    caplog: LogCaptureFixture,
) -> None:
    search = SearchHandler(
        LocalSearch(truth, min_bits=MINIMUM_BITS), SearchCache.of(storage), recorder
    )
    # A node allowing fewer bits than a version of a trust list must match.
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MINIMUM_BITS - 1)
    trust = OwnTrust.of(
        node, search, drops, recorder, content=uploads, retry_after_seconds=RETRY_SECONDS
    )
    builders = FalseBuilders(trust, retry_seconds=RETRY_SECONDS, clock=clock, in_background=at_once)

    builders.found(false_block())
    clock.now += RETRY_SECONDS
    builders.check()

    assert versions_placed(recorder, node) == 0
    assert caplog.text.count(f"Cannot distrust {BUILDER}, which signed Karma block {BLOCK}") == 1


@mark.parametrize(
    ("member", "value", "error"),
    [
        ("builder", None, KeyError),
        ("block", "not an id", ValueError),
        ("staker", "sha256/zz", ValueError),
    ],
)
def test_a_message_that_is_not_one_raises(
    builders: FalseBuilders,
    node: NodeIdentity,
    recorder: Recorder,
    member: str,
    value: str | None,
    error: type[Exception],
) -> None:
    message = dict(false_block())

    if value is None:
        del message[member]

    else:
        message[member] = value

    with raises(error):
        builders.found(message)

    assert versions_placed(recorder, node) == 0


def test_a_block_names_its_builder_and_any_staker_to_distrust() -> None:
    block = FalseBlock.from_message(false_block(staker=STAKER))

    assert block == FalseBlock(BLOCK, BUILDER, STAKER, STATEMENT)
    assert block.distrusted() == (BUILDER, STAKER)
    assert FalseBlock.from_message(false_block()).distrusted() == (BUILDER,)


def test_one_job_at_a_time_distrusts_them_all_in_the_order_they_came(
    trust: OwnTrust, node: NodeIdentity, clock: Clock
) -> None:
    jobs: list[Callable[[], None]] = []
    builders = FalseBuilders(
        trust, retry_seconds=RETRY_SECONDS, clock=clock, in_background=jobs.append
    )
    builders.found(false_block())

    # While the first job has yet to distrust its builder.
    builders.found(false_block(STAKER))
    builders.check()

    (job,) = jobs
    job()
    held = trust.changes.held(node.node_id, node)
    assert held.entries == {BUILDER: False, STAKER: False}
    assert held.newest is not None
    assert held.newest.trust_list.version == 2
    builders.check()
    assert len(jobs) == 1


def test_a_job_that_fails_is_logged_and_tried_again_later(
    builders: FalseBuilders,
    trust: OwnTrust,
    node: NodeIdentity,
    clock: Clock,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    placing = OwnTrust.placed

    def failing(_self: OwnTrust, _request: Request, _entry: TrustEntry) -> Any:
        monkeypatch.setattr(OwnTrust, "placed", placing)
        raise OSError("the disk is full")

    monkeypatch.setattr(OwnTrust, "placed", failing)

    builders.found(false_block())

    assert "Distrusting the builders of false Karma blocks failed" in caplog.text
    assert "the disk is full" in caplog.text
    assert entries(trust, node) == {}
    clock.now += RETRY_SECONDS
    builders.check()
    assert entries(trust, node) == {BUILDER: False}
