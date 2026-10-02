"""Tests for the stats module, fed broadcasts directly."""

from __future__ import annotations
from json import dumps, loads
from logging import INFO
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import (
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.identity.node_identity import NodeIdentity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import AddressSource, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.module import StatsModule, stats_module_factory
from libranet.stats.schema import SeekKind

from tests.helpers import with_node_key

CONTENT = b"content worth counting"
CONTENT_SIZE = len(CONTENT)
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
OTHER_ID = ContentId.for_data(b"other content", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
PEER_ENDPOINT = "http://203.0.113.9:4300"
NOW = 1_000_000.0
SELF_ENDPOINT = "http://localhost:9099"


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return with_node_key(
        LibranetConfig(
            network=NetworkConfig(listen_port=9099),
            storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
            identity=IdentityConfig(key_dir=tmp_path / "keys"),
            stats=StatsConfig(derive_interval_seconds=30.0),
        )
    )


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def module(config: LibranetConfig, queues: ModuleQueues) -> Iterator[StatsModule]:
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval_seconds=0.01)
    module.on_start()

    try:
        yield module

    finally:
        module.on_stop()


def broadcast(event: EventType, payload: Mapping[str, Any], source: ModuleName) -> Message:
    return make_message(event, source, payload)


def requested(external: bool = True, content_id: ContentId = CONTENT_ID) -> Message:
    return broadcast(
        EventType.DATA_REQUESTED,
        {"algorithm": content_id.algorithm, "hash": content_id.hash, "external": external},
        ModuleName.WEBSERVER,
    )


def stored(content_id: ContentId = CONTENT_ID, size: int = CONTENT_SIZE) -> Message:
    return broadcast(
        EventType.DATA_STORED,
        {
            "algorithm": content_id.algorithm,
            "hash": content_id.hash,
            "node_id": str(PEER_ID),
            "size": size,
        },
        ModuleName.VALIDATOR,
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def node_list(config: LibranetConfig) -> dict[str, str]:
    nodes: dict[str, str] = loads(config.storage.node_list_path.read_bytes())["nodes"]
    return nodes


def candidates(config: LibranetConfig) -> dict[str, list[str]]:
    """The candidate list, as node id to endpoints."""
    nodes = loads(config.storage.candidate_list_path.read_bytes())["nodes"]
    return {node["node_id"]: node["endpoints"] for node in nodes}


def seek_list(config: LibranetConfig) -> dict[str, list[str]]:
    seek: dict[str, list[str]] = loads(config.storage.seek_list_path.read_bytes())
    return seek


def test_a_module_started_without_the_node_key_fails_rather_than_making_one(
    tmp_path: Path, queues: ModuleQueues
) -> None:
    # The supervisor makes the key as the node starts; a module only loads it.
    config = LibranetConfig(
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(key_dir=tmp_path / "keys"),
    )
    module = StatsModule(ModuleName.STATS, queues, config)

    with raises(FileNotFoundError):
        module.on_start()

    assert not config.private_key_path.exists()


@mark.usefixtures("module")
def test_starting_opens_the_database_and_writes_every_list(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    identity = NodeIdentity.load(config)

    assert config.storage.database_path.is_file()
    assert node_list(config) == {SELF_ENDPOINT: str(identity.node_id)}
    assert seek_list(config) == {"data": [], "search": []}
    assert candidates(config) == {}
    (message,) = published(queues)
    assert message["event"] == EventType.NODE_LIST_UPDATED
    assert message["path"] == str(config.storage.candidate_list_path)


def test_a_node_whose_ports_differ_publishes_both_for_itself(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    network = NetworkConfig(listen_port=9099, external_port=4300)
    module = StatsModule(
        ModuleName.STATS,
        queues,
        config.model_copy(update={"network": network}),
        poll_interval_seconds=0.01,
    )
    module.on_start()

    try:
        node_id = str(NodeIdentity.load(config).node_id)

        assert node_list(config) == {
            "http://localhost:4300": node_id,
            "http://localhost:9099": node_id,
        }

    finally:
        module.on_stop()


def test_requests_are_counted_by_origin(module: StatsModule) -> None:
    module.handle(requested(external=True))
    module.handle(requested(external=False))
    module.handle(requested(external=True))

    stats = module.database.data_stats(CONTENT_ID)

    assert stats is not None
    assert (stats.external_requests, stats.internal_requests) == (2, 1)


def test_a_local_miss_becomes_an_outstanding_request(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.DATA_NOT_FOUND,
            {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert seek_list(config)["data"] == [str(CONTENT_ID)]


def test_a_miss_handled_once_its_content_is_held_is_not_sought(
    module: StatsModule, config: LibranetConfig
) -> None:
    # Its data.stored was broadcast first: messages from two modules have no order.
    CasStore.source_of_truth(config.storage).write(CONTENT_ID, CONTENT)
    module.handle(stored())

    module.handle(
        broadcast(
            EventType.DATA_NOT_FOUND,
            {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert seek_list(config)["data"] == []


def test_a_search_becomes_an_outstanding_request(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.SEARCH_REQUESTED,
            {"prefix": "0123ABCD", "cache_path": "ignored"},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert seek_list(config)["search"] == ["0123abcd"]


def test_a_search_request_enriches_its_cached_response(
    module: StatsModule, config: LibranetConfig
) -> None:
    prefix = "8" + "0" * 63
    nearer = ContentId("sha256", "8" + "0" * 62 + "1")
    module.database.record_request(nearer, external=True)
    cache_path = config.storage.search_cache_dir / prefix[:4] / f"{prefix}.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(dumps({"results": []}).encode("utf-8"))

    module.handle(
        broadcast(
            EventType.SEARCH_REQUESTED,
            {"prefix": prefix, "cache_path": str(cache_path)},
            ModuleName.WEBSERVER,
        )
    )

    assert loads(cache_path.read_bytes())["results"] == [str(nearer)]


def test_stored_content_is_counted_credited_and_no_longer_sought(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.DATA_NOT_FOUND,
            {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash},
            ModuleName.WEBSERVER,
        )
    )

    module.handle(stored())
    module.derive()

    data = module.database.data_stats(CONTENT_ID)
    peer = module.database.node_stats(PEER_ID)

    assert data is not None and peer is not None
    assert data.pushes == 1
    assert data.last_acquired is not None
    assert data.size_bytes == len(CONTENT)
    assert peer.bytes_received == len(CONTENT)
    assert seek_list(config)["data"] == []


def test_deleted_content_is_counted_with_the_time_it_was_held(module: StatsModule) -> None:
    module.handle(stored())
    stats = module.database.data_stats(CONTENT_ID)
    assert stats is not None and stats.last_acquired is not None

    module.handle(
        broadcast(
            EventType.DATA_DELETED,
            {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash, "size": len(CONTENT)},
            ModuleName.EVICTION,
        )
    )

    deleted = module.database.data_stats(CONTENT_ID)
    assert deleted is not None
    assert deleted.deletes == 1
    assert deleted.stored_seconds >= 0
    assert deleted.last_acquired == stats.last_acquired
    assert deleted.size_bytes is None


def test_rejected_content_still_counts_as_a_push(module: StatsModule) -> None:
    module.handle(
        broadcast(
            EventType.DATA_REJECTED,
            {
                "algorithm": CONTENT_ID.algorithm,
                "hash": CONTENT_ID.hash,
                "node_id": str(PEER_ID),
            },
            ModuleName.VALIDATOR,
        )
    )

    stats = module.database.data_stats(CONTENT_ID)

    assert stats is not None
    assert (stats.pushes, stats.last_acquired, stats.size_bytes) == (1, None, None)


# -- What to let go of first (Phase 2 Step 28) --------------------------------


def unmatched(node_id: ContentId, variant: int) -> ContentId:
    """A content id sharing no leading bit with ``node_id``, told apart by ``variant``."""
    value = int(node_id.hash, 16) ^ (1 << 255) ^ variant
    return ContentId("sha256", f"{value:064x}")


def hold(module: StatsModule, config: LibranetConfig, content_id: ContentId, size: int) -> None:
    """Store ``size`` bytes as ``content_id``, and announce it."""
    CasStore.source_of_truth(config.storage).write(content_id, b"x" * size)
    module.handle(stored(content_id, size))


def candidates_requested(byte_count: int, *exclude: ContentId) -> Message:
    return broadcast(
        EventType.EVICTION_CANDIDATES_REQUESTED,
        {"bytes": byte_count, "exclude": [str(content_id) for content_id in exclude]},
        ModuleName.EVICTION,
    )


def offered(queues: ModuleQueues) -> list[tuple[ContentId, int]]:
    """The content the module last listed for eviction, and each size."""
    answers = [
        message
        for message in published(queues)
        if message["event"] == EventType.EVICTION_CANDIDATES
    ]
    assert answers, "no eviction.candidates was published"
    return [
        (ContentId.create(entry["algorithm"], entry["hash"]), entry["size"])
        for entry in answers[-1]["objects"]
    ]


@fixture
def node_id(config: LibranetConfig) -> ContentId:
    return NodeIdentity.load(config).node_id


@fixture
def still(config: LibranetConfig, queues: ModuleQueues) -> Iterator[StatsModule]:
    """A stats module whose clock stands still.

    Content held is ranked against the longest any of it has gone unused, so
    even the microseconds between stores would otherwise decide the order.
    """
    module = StatsModule(
        ModuleName.STATS, queues, config, clock=lambda: NOW, poll_interval_seconds=0.01
    )
    module.on_start()

    try:
        yield module

    finally:
        module.on_stop()


def test_content_held_is_offered_best_first_until_the_bytes_asked_are_covered(
    still: StatsModule, config: LibranetConfig, queues: ModuleQueues, node_id: ContentId
) -> None:
    # Alike but for size, so smallest first.
    large, small, middle = (unmatched(node_id, variant) for variant in (1, 2, 3))
    hold(still, config, large, 30)
    hold(still, config, small, 10)
    hold(still, config, middle, 20)

    still.handle(candidates_requested(25))

    assert offered(queues) == [(small, 10), (middle, 20)]


def test_content_being_handed_off_already_is_not_offered(
    still: StatsModule, config: LibranetConfig, queues: ModuleQueues, node_id: ContentId
) -> None:
    first, second = unmatched(node_id, 1), unmatched(node_id, 2)
    hold(still, config, first, 10)
    hold(still, config, second, 20)

    still.handle(candidates_requested(1_000, first))

    assert offered(queues) == [(second, 20)]


def test_no_more_than_the_most_candidates_are_offered(
    config: LibranetConfig, queues: ModuleQueues, node_id: ContentId
) -> None:
    module = StatsModule(
        ModuleName.STATS,
        queues,
        config,
        clock=lambda: NOW,
        poll_interval_seconds=0.01,
        max_candidates=2,
    )
    module.on_start()

    try:
        content = [unmatched(node_id, variant) for variant in (1, 2, 3)]

        for size, content_id in enumerate(content, start=1):
            hold(module, config, content_id, size)

        module.handle(candidates_requested(1_000))

        assert offered(queues) == [(content[0], 1), (content[1], 2)]

    finally:
        module.on_stop()


def test_content_gone_from_the_store_is_recorded_as_deleted_not_offered(
    still: StatsModule,
    config: LibranetConfig,
    queues: ModuleQueues,
    node_id: ContentId,
    caplog: LogCaptureFixture,
) -> None:
    gone, kept = unmatched(node_id, 1), unmatched(node_id, 2)
    hold(still, config, gone, 10)
    hold(still, config, kept, 20)
    CasStore.source_of_truth(config.storage).path_for(gone).unlink()

    with caplog.at_level(INFO):
        still.handle(candidates_requested(1_000))

    assert offered(queues) == [(kept, 20)]
    assert f"{gone} is gone from the store" in caplog.text
    stats = still.database.data_stats(gone)
    assert stats is not None
    assert (stats.size_bytes, stats.deletes) == (None, 1)


def test_this_nodes_own_key_is_never_offered(
    still: StatsModule, config: LibranetConfig, queues: ModuleQueues, node_id: ContentId
) -> None:
    hold(still, config, node_id, 113)

    still.handle(candidates_requested(1_000))

    assert offered(queues) == []


def test_with_nothing_held_nothing_is_offered(still: StatsModule, queues: ModuleQueues) -> None:
    still.handle(requested())

    still.handle(candidates_requested(1_000))

    assert offered(queues) == []


def accessed(bundle: ContentId) -> Message:
    return broadcast(EventType.APP_ACCESSED, {"bundle": str(bundle)}, ModuleName.WEBSERVER)


def reclaim_requested() -> Message:
    return broadcast(EventType.RESOLVED_RECLAIM_REQUESTED, {}, ModuleName.EVICTION)


def test_only_applications_used_lately_have_their_resolved_files_kept(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    now = [NOW]
    module = StatsModule(
        ModuleName.STATS, queues, config, clock=lambda: now[0], poll_interval_seconds=0.01
    )
    module.on_start()

    try:
        published(queues)
        module.handle(accessed(OTHER_ID))
        module.handle(accessed(CONTENT_ID))
        now[0] += config.storage.resolved_idle_seconds
        module.handle(accessed(PEER_ID))
        # Used exactly as long ago as allowed, so still kept.
        module.handle(reclaim_requested())
        now[0] += 1
        module.handle(reclaim_requested())

        answers = published(queues)

    finally:
        module.on_stop()

    assert [message["event"] for message in answers] == [EventType.RESOLVED_RECLAIM] * 2
    assert answers[0]["keep"] == sorted(str(bundle) for bundle in (CONTENT_ID, OTHER_ID, PEER_ID))
    assert answers[1]["keep"] == [str(PEER_ID)]


def test_with_no_application_used_no_resolved_files_are_kept(
    module: StatsModule, queues: ModuleQueues
) -> None:
    published(queues)

    module.handle(reclaim_requested())

    (answer,) = published(queues)
    assert (answer["event"], answer["keep"]) == (EventType.RESOLVED_RECLAIM, [])


def test_a_received_node_list_gives_candidates_not_yet_published(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {"nodes": {PEER_ENDPOINT: str(PEER_ID)}},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert candidates(config) == {str(PEER_ID): [PEER_ENDPOINT]}
    assert PEER_ENDPOINT not in node_list(config)
    (address,) = module.database.node_addresses(PEER_ID)
    assert address.source == AddressSource.RELAYED


def test_a_received_node_list_says_how_each_address_was_learned(module: StatsModule) -> None:
    named = "http://peer.example.org:4300"
    relayed = "http://198.51.100.7:8080"
    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {
                "nodes": {PEER_ENDPOINT: str(PEER_ID), named: str(PEER_ID), relayed: str(PEER_ID)},
                "sources": {PEER_ENDPOINT: "observed", named: "reverse_dns"},
            },
            ModuleName.CONNECTIONS,
        )
    )

    sources = {
        address.endpoint: address.source for address in module.database.node_addresses(PEER_ID)
    }
    assert sources == {
        PEER_ENDPOINT: AddressSource.OBSERVED,
        named: AddressSource.REVERSE_DNS,
        relayed: AddressSource.RELAYED,
    }


def test_unusable_node_list_entries_are_dropped_without_losing_the_rest(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {"nodes": {"http://198.51.100.7:8080": "nonsense", PEER_ENDPOINT: str(PEER_ID)}},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert candidates(config) == {str(PEER_ID): [PEER_ENDPOINT]}


def test_a_peers_seek_list_is_kept_apart_from_this_nodes(
    module: StatsModule, config: LibranetConfig
) -> None:
    module.handle(
        broadcast(
            EventType.SEEK_RECEIVED,
            {"node_id": str(PEER_ID), "data": [str(OTHER_ID)], "search": ["ffff"]},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert module.database.seek_values(SeekKind.DATA, PEER_ID) == [str(OTHER_ID)]
    assert module.database.seek_values(SeekKind.SEARCH, PEER_ID) == ["ffff"]
    assert seek_list(config) == {"data": [], "search": []}


def test_a_seek_list_without_either_key_is_accepted(module: StatsModule) -> None:
    module.handle(
        broadcast(EventType.SEEK_RECEIVED, {"node_id": str(PEER_ID)}, ModuleName.WEBSERVER)
    )

    assert module.database.seek_values(SeekKind.DATA, PEER_ID) == []


def test_connections_are_recorded_against_the_peer(module: StatsModule) -> None:
    module.handle(
        broadcast(
            EventType.CONNECTION_OPENED,
            {"node_id": str(PEER_ID), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )
    module.handle(
        broadcast(
            EventType.CONNECTION_CLOSED,
            {"node_id": str(PEER_ID), "remote": True},
            ModuleName.CONNECTIONS,
        )
    )

    stats = module.database.node_stats(PEER_ID)

    assert stats is not None
    assert (stats.successful_connections, stats.remote_disconnects) == (1, 1)
    assert module.database.last_good_endpoints() == [(PEER_ENDPOINT, str(PEER_ID))]


def test_failed_attempts_sends_and_lookups_are_counted_against_the_peer(
    module: StatsModule,
) -> None:
    content = {"algorithm": CONTENT_ID.algorithm, "hash": CONTENT_ID.hash, "node_id": str(PEER_ID)}
    module.handle(
        broadcast(
            EventType.CONNECTION_FAILED,
            {"node_id": str(PEER_ID), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )
    module.handle(broadcast(EventType.DATA_SENT, {**content, "size": 12}, ModuleName.CONNECTIONS))

    for found in (True, False, False):
        module.handle(
            broadcast(
                EventType.FETCH_ATTEMPTED, {**content, "found": found}, ModuleName.CONNECTIONS
            )
        )

    stats = module.database.node_stats(PEER_ID)

    assert stats is not None
    assert (stats.connection_attempts, stats.successful_connections) == (1, 0)
    assert stats.bytes_sent == 12
    assert (stats.data_found, stats.data_not_found) == (1, 2)
    # An address that could not be reached is not one to publish.
    assert module.database.node_addresses(PEER_ID) == []


def test_a_failed_attempt_is_counted_against_the_address_tried(module: StatsModule) -> None:
    module.handle(
        broadcast(
            EventType.NODES_RECEIVED, {"nodes": {PEER_ENDPOINT: str(PEER_ID)}}, ModuleName.WEBSERVER
        )
    )
    module.handle(
        broadcast(
            EventType.CONNECTION_FAILED,
            {"node_id": str(PEER_ID), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )

    (address,) = module.database.node_addresses(PEER_ID)
    assert (address.attempts, address.consecutive_failures) == (1, 1)


def test_an_address_that_answered_as_another_node_is_recorded_for_both(
    module: StatsModule, config: LibranetConfig
) -> None:
    stale_id = ContentId.for_data(b"a node that moved away", "sha256")
    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {"nodes": {PEER_ENDPOINT: str(stale_id)}},
            ModuleName.WEBSERVER,
        )
    )
    # The connection manager expected `stale_id` there, and the peer answered,
    # already connected at another address.
    module.handle(
        broadcast(
            EventType.CONNECTION_FAILED,
            {"node_id": str(stale_id), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )
    module.handle(
        broadcast(
            EventType.ADDRESS_VERIFIED,
            {"node_id": str(PEER_ID), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )
    module.derive()

    (stale,) = module.database.node_addresses(stale_id)
    (answered,) = module.database.node_addresses(PEER_ID)
    assert (stale.consecutive_failures, stale.successes) == (1, 0)
    assert (answered.source, answered.successes) == (AddressSource.DIALED, 1)
    # The duplicate connection was closed at once, so it counts as none.
    assert module.database.node_stats(PEER_ID) is None
    assert node_list(config)[PEER_ENDPOINT] == str(PEER_ID)


def unreached() -> Message:
    """The peer, dialed at every address it was to be tried at, reached at none."""
    return broadcast(EventType.NODE_UNREACHED, {"node_id": str(PEER_ID)}, ModuleName.CONNECTIONS)


def give_up_on_the_peer(module: StatsModule, config: LibranetConfig) -> None:
    """Fail to reach the peer, at an address that has worked, as often as it takes."""
    module.database.record_address_worked(PEER_ID, PEER_ENDPOINT)

    for _ in range(config.stats.max_node_failures):
        module.handle(unreached())


def test_a_node_is_given_up_on_at_once_after_enough_failed_attempts(
    module: StatsModule,
    config: LibranetConfig,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    module.database.record_address_worked(PEER_ID, PEER_ENDPOINT)
    module.derive()
    published(queues)

    for _ in range(config.stats.max_node_failures - 1):
        module.handle(unreached())

    assert candidates(config) == {str(PEER_ID): [PEER_ENDPOINT]}
    assert published(queues) == []

    module.handle(unreached())

    assert candidates(config) == {}
    (message,) = published(queues)
    assert message["event"] == EventType.NODE_LIST_UPDATED
    assert f"Giving up on {PEER_ID}" in caplog.text


@mark.parametrize("source", [AddressSource.ADVERTISED, AddressSource.OBSERVED])
def test_a_node_list_naming_its_sender_starts_the_senders_count_again(
    module: StatsModule, config: LibranetConfig, source: AddressSource
) -> None:
    give_up_on_the_peer(module, config)

    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {"nodes": {PEER_ENDPOINT: str(PEER_ID)}, "sources": {PEER_ENDPOINT: source.value}},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    assert candidates(config) == {str(PEER_ID): [PEER_ENDPOINT]}


def test_a_node_merely_relayed_or_named_in_reverse_dns_stays_given_up_on(
    module: StatsModule, config: LibranetConfig
) -> None:
    named = "http://peer.example.org:4300"
    give_up_on_the_peer(module, config)

    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {
                "nodes": {PEER_ENDPOINT: str(PEER_ID), named: str(PEER_ID)},
                "sources": {named: "reverse_dns"},
            },
            ModuleName.CONNECTIONS,
        )
    )
    module.derive()

    assert candidates(config) == {}


def test_reaching_a_node_starts_its_count_again(
    module: StatsModule, config: LibranetConfig
) -> None:
    give_up_on_the_peer(module, config)

    module.handle(
        broadcast(
            EventType.CONNECTION_OPENED,
            {"node_id": str(PEER_ID), "endpoint": PEER_ENDPOINT},
            ModuleName.CONNECTIONS,
        )
    )
    module.derive()

    assert candidates(config) == {str(PEER_ID): [PEER_ENDPOINT]}


def test_a_candidate_list_that_changed_is_announced_once(
    module: StatsModule, queues: ModuleQueues
) -> None:
    published(queues)

    module.derive()
    assert published(queues) == []

    module.handle(
        broadcast(
            EventType.NODES_RECEIVED,
            {"nodes": {PEER_ENDPOINT: str(PEER_ID)}},
            ModuleName.WEBSERVER,
        )
    )
    module.derive()

    (message,) = published(queues)
    assert message["event"] == EventType.NODE_LIST_UPDATED
    assert message["source"] == ModuleName.STATS


def test_the_lists_are_rederived_once_the_interval_passes(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    now = [10_000.0]
    module = StatsModule(
        ModuleName.STATS, queues, config, clock=lambda: now[0], poll_interval_seconds=0.01
    )
    module.on_start()

    try:
        module.database.record_address_worked(PEER_ID, PEER_ENDPOINT)
        module.on_idle()

        assert PEER_ENDPOINT not in node_list(config)

        now[0] += 30
        module.on_idle()

        assert PEER_ENDPOINT in node_list(config)

    finally:
        module.on_stop()


def test_run_survives_malformed_broadcasts(
    config: LibranetConfig, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval_seconds=0.01)

    for payload in ({"hash": CONTENT_ID.hash, "external": True}, {"algorithm": "sha256"}):
        queues.inbox.put(broadcast(EventType.DATA_REQUESTED, payload, ModuleName.WEBSERVER))

    queues.inbox.put(requested())
    queues.inbox.put(make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR))

    module.run()

    assert caplog.text.count("failed handling data.requested") == 2

    stats = StatsModule(ModuleName.STATS, queues, config, poll_interval_seconds=0.01)
    stats.on_start()

    try:
        # The one good message was still recorded, and the run stopped cleanly.
        recorded = stats.database.data_stats(CONTENT_ID)

    finally:
        stats.on_stop()

    assert recorded is not None
    assert recorded.external_requests == 1


def test_stopping_a_module_that_never_started_is_harmless(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    # The supervisor stops a module from a `finally`, so `on_start` failing
    # must not turn into a second failure on the way out.
    StatsModule(ModuleName.STATS, queues, config, poll_interval_seconds=0.01).on_stop()

    assert not config.storage.database_path.exists()


def test_the_database_is_closed_when_the_module_stops(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval_seconds=0.01)
    module.on_start()
    module.on_stop()

    with raises(RuntimeError, match="not running"):
        module.database  # pylint: disable=pointless-statement


def test_the_module_subscribes_to_what_it_records() -> None:
    assert EventType.DATA_REQUESTED in StatsModule.subscriptions
    assert EventType.DATA_DELETED in StatsModule.subscriptions
    assert EventType.ADDRESS_VERIFIED in StatsModule.subscriptions
    assert EventType.NODE_UNREACHED in StatsModule.subscriptions
    assert EventType.EVICTION_CANDIDATES_REQUESTED in StatsModule.subscriptions
    assert EventType.APP_ACCESSED in StatsModule.subscriptions
    assert EventType.RESOLVED_RECLAIM_REQUESTED in StatsModule.subscriptions
    assert EventType.PUT_COMPLETED not in StatsModule.subscriptions
    assert EventType.NODE_LIST_UPDATED not in StatsModule.subscriptions


def test_factory_builds_a_stats_module_for_the_configured_node(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = stats_module_factory(ModuleName.STATS, config, queues)

    assert isinstance(module, StatsModule)
    assert module.name == ModuleName.STATS


def test_offering_no_candidates_at_all_is_refused(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    with raises(ValueError, match="max_candidates"):
        StatsModule(ModuleName.STATS, queues, config, max_candidates=0)
