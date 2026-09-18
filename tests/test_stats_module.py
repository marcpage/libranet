"""Tests for the stats module, fed broadcasts directly."""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Iterator, Mapping

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.config.models import (
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.module import StatsModule, stats_module_factory
from libranet.stats.schema import SeekKind

CONTENT = b"content worth counting"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
OTHER_ID = ContentId.for_data(b"other content", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
PEER_ENDPOINT = "http://203.0.113.9:4300"
SELF_ENDPOINT = "http://localhost:9099"


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return LibranetConfig(
        network=NetworkConfig(listen_port=9099),
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(key_dir=tmp_path / "keys"),
        stats=StatsConfig(derive_interval_seconds=30.0),
    )


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def module(config: LibranetConfig, queues: ModuleQueues) -> Iterator[StatsModule]:
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval=0.01)
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


def stored(content_id: ContentId = CONTENT_ID, size: int = len(CONTENT)) -> Message:
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


def seek_list(config: LibranetConfig) -> dict[str, list[str]]:
    seek: dict[str, list[str]] = loads(config.storage.seek_list_path.read_bytes())
    return seek


def test_starting_opens_the_database_and_writes_both_lists(
    module: StatsModule, config: LibranetConfig, queues: ModuleQueues
) -> None:
    identity = load_node_identity(config)

    assert config.storage.database_path.is_file()
    assert node_list(config) == {SELF_ENDPOINT: str(identity.node_id)}
    assert seek_list(config) == {"data": [], "search": []}
    assert [message["event"] for message in published(queues)] == [EventType.NODE_LIST_UPDATED]


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
    assert peer.bytes_received == len(CONTENT)
    assert seek_list(config)["data"] == []


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
    assert (stats.pushes, stats.last_acquired) == (1, None)


def test_a_received_node_list_joins_the_one_this_node_publishes(
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

    assert node_list(config)[PEER_ENDPOINT] == str(PEER_ID)


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

    assert PEER_ENDPOINT in node_list(config)
    assert "http://198.51.100.7:8080" not in node_list(config)


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
    assert module.database.known_endpoints() == [(PEER_ENDPOINT, str(PEER_ID))]


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
    assert module.database.known_endpoints() == []


def test_a_node_list_that_changed_is_announced_once(
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
    module = StatsModule(ModuleName.STATS, queues, config, clock=lambda: now[0], poll_interval=0.01)
    module.on_start()

    try:
        module.database.record_endpoint(PEER_ID, PEER_ENDPOINT)
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
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval=0.01)

    for payload in ({"hash": CONTENT_ID.hash, "external": True}, {"algorithm": "sha256"}):
        queues.inbox.put(broadcast(EventType.DATA_REQUESTED, payload, ModuleName.WEBSERVER))

    queues.inbox.put(requested())
    queues.inbox.put(make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR))

    module.run()

    assert caplog.text.count("failed handling data.requested") == 2

    stats = StatsModule(ModuleName.STATS, queues, config, poll_interval=0.01)
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
    StatsModule(ModuleName.STATS, queues, config, poll_interval=0.01).on_stop()

    assert not config.storage.database_path.exists()


def test_the_database_is_closed_when_the_module_stops(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = StatsModule(ModuleName.STATS, queues, config, poll_interval=0.01)
    module.on_start()
    module.on_stop()

    with raises(RuntimeError, match="not running"):
        module.database


def test_the_module_subscribes_to_what_it_records() -> None:
    assert EventType.DATA_REQUESTED in StatsModule.subscriptions
    assert EventType.PUT_COMPLETED not in StatsModule.subscriptions
    assert EventType.NODE_LIST_UPDATED not in StatsModule.subscriptions


def test_factory_builds_a_stats_module_for_the_configured_node(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = stats_module_factory(ModuleName.STATS, config, queues)

    assert isinstance(module, StatsModule)
    assert module.name == ModuleName.STATS
