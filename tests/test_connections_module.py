"""Tests of the connection manager module, against live fixture peers.

Each fixture peer is this node's own web server over a temp CAS, with an
identity of its own. The module connects and talks to peers on background
threads, so tests wait for what it publishes. A fake clock stands in for
retry delays and seek refreshes, whose timers only :meth:`on_idle` checks;
signatures still use real time.
"""

from __future__ import annotations
from json import dumps
from logging import INFO, getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import create_server
from threading import Thread
from time import monotonic, sleep, time
from typing import Any, Callable, Iterator, Mapping, Sequence

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import node_store, source_of_truth_store
from libranet.config.models import IdentityConfig, LibranetConfig, PeerConfig, StorageConfig
from libranet.connections.module import ConnectionsModule, connections_module_factory
from libranet.identity.authentication import request_authenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity, load_node_identity
from libranet.identity.signatures import MessageSigner
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import MessageQueue, ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule
from libranet.webserver.server import LibranetHTTPServer, RequestHandler, build_router

TIMEOUT = 5.0
RETRY_DELAY = 30.0
SEEK_REFRESH = 10.0

HELD = b"content the client holds"
HELD_ID = ContentId.for_data(HELD, "sha256")
OFFERED = b"content one peer holds"
OFFERED_ID = ContentId.for_data(OFFERED, "sha256")
NOWHERE_ID = ContentId.for_data(b"content nobody holds", "sha256")
OTHER_ID = ContentId.for_data(b"some other node's key", "sha256")


def wait_until(condition: Callable[[], bool], what: str) -> None:
    deadline = monotonic() + TIMEOUT

    while not condition():
        assert monotonic() < deadline, f"timed out waiting for {what}"
        sleep(0.01)


class Bus:
    """Everything published to one outbox, kept as it is read."""

    def __init__(self, outbox: MessageQueue) -> None:
        self._outbox = outbox
        self._seen: list[Message] = []

    def events(self, event: EventType, **fields: Any) -> list[Message]:
        while True:
            try:
                self._seen.append(self._outbox.get(block=False))

            except Empty:
                break

        return [
            message
            for message in self._seen
            if message["event"] == event
            and all(message.get(name) == value for name, value in fields.items())
        ]

    def wait_for(self, event: EventType, count: int = 1, **fields: Any) -> list[Message]:
        wait_until(lambda: len(self.events(event, **fields)) >= count, f"{count} {event}")
        return self.events(event, **fields)


class FixturePeer:
    """This node's web server on a free local port, with an identity of its own."""

    def __init__(self, root: Path, identity: NodeIdentity | None = None) -> None:
        self.identity = identity or NodeIdentity.from_private_key(generate_private_key(), "sha256")
        self.storage = StorageConfig(data_dir=root / "data", cache_dir=root / "cache")
        self.store = source_of_truth_store(self.storage)
        self.identity.publish_public_key(self.store)
        queues = ModuleQueues(inbox=Queue(), outbox=Queue())
        self.bus = Bus(queues.outbox)
        self.server = LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_router(
                self.storage,
                7,
                StubModule(ModuleName.WEBSERVER, queues).publish,
                request_authenticator(LibranetConfig(storage=self.storage)),
                allow_unsigned_api_reads=True,
            ),
            getLogger("test.webserver"),
            MessageSigner(self.identity),
        )
        self._thread = Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    @property
    def node_id(self) -> ContentId:
        return self.identity.node_id

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()


def write_lists(
    storage: StorageConfig, nodes: Mapping[str, str] | None, sought: Sequence[ContentId] = ()
) -> None:
    """Derived node and seek lists, as the stats module would write them."""
    storage.derived_dir.mkdir(parents=True, exist_ok=True)

    if nodes is not None:
        storage.node_list_path.write_text(dumps({"nodes": nodes}))

    storage.seek_list_path.write_text(dumps({"data": [str(c) for c in sought], "search": []}))


@fixture
def peers(tmp_path: Path) -> Iterator[list[FixturePeer]]:
    peers = [FixturePeer(tmp_path / f"peer-{index}") for index in range(2)]
    yield peers

    for peer in peers:
        peer.stop()


def write_seeds(path: Path, seeds: Mapping[str, str | None]) -> Path:
    path.write_text(dumps({"nodes": seeds}))
    return path


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    root = tmp_path / "client"
    return LibranetConfig(
        storage=StorageConfig(data_dir=root / "data", cache_dir=root / "cache"),
        identity=IdentityConfig(key_dir=root / "keys"),
        peers=PeerConfig(
            connect_timeout_seconds=TIMEOUT,
            request_timeout_seconds=TIMEOUT,
            retry_delay_seconds=RETRY_DELAY,
            seek_refresh_seconds=SEEK_REFRESH,
            seed_file=write_seeds(tmp_path / "seeds.json", {}),
        ),
    )


def with_peers(config: LibranetConfig, **settings: Any) -> LibranetConfig:
    return config.model_copy(update={"peers": config.peers.model_copy(update=settings)})


@fixture
def identity(config: LibranetConfig) -> NodeIdentity:
    return load_node_identity(config)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def bus(queues: ModuleQueues) -> Bus:
    return Bus(queues.outbox)


@fixture
def now() -> list[float]:
    return [time()]


class Modules:
    """Builds connection managers over the test's queues, stopping each at the end."""

    def __init__(self, queues: ModuleQueues, now: list[float]) -> None:
        self._queues = queues
        self._now = now
        self.built: list[ConnectionsModule] = []

    def start(self, config: LibranetConfig) -> ConnectionsModule:
        module = ConnectionsModule(
            ModuleName.CONNECTIONS,
            self._queues,
            config,
            clock=lambda: self._now[0],
            poll_interval=0.01,
        )
        self.built.append(module)
        module.on_start()
        return module


@fixture
def modules(queues: ModuleQueues, now: list[float]) -> Iterator[Modules]:
    modules = Modules(queues, now)
    yield modules

    for module in modules.built:
        module.on_stop()


def node_list(identity: NodeIdentity, *peers: FixturePeer) -> dict[str, str]:
    """A derived node list: this node first, then ``peers`` in order."""
    nodes = {"http://localhost:8080": str(identity.node_id)}
    nodes.update({peer.endpoint: str(peer.node_id) for peer in peers})
    return nodes


def closed_port() -> int:
    with create_server(("127.0.0.1", 0)) as listener:
        return int(listener.getsockname()[1])


def fetch_request(content_id: ContentId) -> Message:
    return make_message(
        EventType.FETCH_REQUESTED,
        ModuleName.FETCHER,
        {"algorithm": content_id.algorithm, "hash": content_id.hash},
    )


# -- Keeping the peer mix --------------------------------------------------


def test_every_peer_the_node_list_names_is_connected_and_greeted(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))

    module = modules.start(config)

    opened = bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    assert {(message["node_id"], message["endpoint"]) for message in opened} == {
        (str(peer.node_id), peer.endpoint) for peer in peers
    }
    assert module.connected == {peer.node_id: peer.endpoint for peer in peers}

    for peer in peers:
        # Step 3 of first contact: the peer was told about this node.
        (told,) = peer.bus.wait_for(EventType.NODES_RECEIVED)
        assert told["nodes"]["http://127.0.0.1:8080"] == str(identity.node_id)


def test_the_mix_stops_at_the_configured_number(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))

    module = modules.start(with_peers(config, min_outgoing_connections=1))

    bus.wait_for(EventType.CONNECTION_OPENED, node_id=str(peers[0].node_id))
    sleep(0.2)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}


def test_a_new_node_list_brings_new_connections(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    module = modules.start(config)
    assert module.connected == {}

    write_lists(config.storage, node_list(identity, peers[0]))
    module.handle(
        make_message(
            EventType.NODE_LIST_UPDATED,
            ModuleName.STATS,
            {"path": str(config.storage.node_list_path)},
        )
    )

    bus.wait_for(EventType.CONNECTION_OPENED, node_id=str(peers[0].node_id))


def test_seeds_are_used_while_no_peer_is_known(
    modules: Modules, config: LibranetConfig, peers: list[FixturePeer], bus: Bus, tmp_path: Path
) -> None:
    seeds = write_seeds(tmp_path / "known-seeds.json", {peers[0].endpoint: None})

    module = modules.start(with_peers(config, seed_file=seeds))

    (opened,) = bus.wait_for(EventType.CONNECTION_OPENED)
    assert opened["node_id"] == str(peers[0].node_id)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}


def test_seeds_are_ignored_once_a_peer_is_known(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    tmp_path: Path,
) -> None:
    seeds = write_seeds(tmp_path / "known-seeds.json", {peers[1].endpoint: str(peers[1].node_id)})
    write_lists(config.storage, node_list(identity, peers[0]))

    module = modules.start(with_peers(config, seed_file=seeds))

    bus.wait_for(EventType.CONNECTION_OPENED)
    sleep(0.2)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}


def test_an_unusable_seed_list_leaves_nothing_to_dial(
    modules: Modules, config: LibranetConfig, tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    broken = tmp_path / "broken-seeds.json"
    broken.write_text("not json")

    module = modules.start(with_peers(config, seed_file=broken))

    assert "Seed list unavailable" in caplog.text
    assert module.connected == {}


def test_an_unreachable_peer_rests_before_it_is_dialed_again(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    now: list[float],
) -> None:
    endpoint = f"http://127.0.0.1:{closed_port()}"
    write_lists(config.storage, {**node_list(identity), endpoint: str(OTHER_ID)})

    module = modules.start(config)

    (failed,) = bus.wait_for(EventType.CONNECTION_FAILED)
    assert (failed["node_id"], failed["endpoint"]) == (str(OTHER_ID), endpoint)

    module.on_idle()
    sleep(0.2)
    assert len(bus.events(EventType.CONNECTION_FAILED)) == 1

    now[0] += RETRY_DELAY
    module.on_idle()

    bus.wait_for(EventType.CONNECTION_FAILED, count=2)


def test_an_unreachable_seed_of_unknown_id_is_not_reported(
    modules: Modules,
    config: LibranetConfig,
    bus: Bus,
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    endpoint = f"http://127.0.0.1:{closed_port()}"
    seeds = write_seeds(tmp_path / "dead-seeds.json", {endpoint: None})

    modules.start(with_peers(config, seed_file=seeds))

    wait_until(lambda: f"Could not connect to {endpoint}" in caplog.text, "the attempt to fail")
    # Stats count attempts per node id, and this one has none to count against.
    assert bus.events(EventType.CONNECTION_FAILED) == []


def test_an_endpoint_that_is_this_node_is_not_dialed_again(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    now: list[float],
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    itself = FixturePeer(tmp_path / "itself", identity)
    seeds = write_seeds(tmp_path / "self-seeds.json", {itself.endpoint: None})

    try:
        module = modules.start(with_peers(config, seed_file=seeds))
        wait_until(lambda: "it is this node" in caplog.text, "the node to recognize itself")

        now[0] += RETRY_DELAY
        module.on_idle()
        sleep(0.2)

    finally:
        itself.stop()

    assert caplog.text.count("it is this node") == 1
    assert module.connected == {}
    assert bus.events(EventType.CONNECTION_OPENED) == []
    assert bus.events(EventType.CONNECTION_FAILED) == []


def test_a_second_endpoint_for_a_connected_node_is_closed(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    twin = FixturePeer(tmp_path / "twin")
    double = FixturePeer(tmp_path / "double", twin.identity)
    # The node list still has an old node id for the second endpoint.
    write_lists(
        config.storage,
        {**node_list(identity, twin), double.endpoint: str(OTHER_ID)},
    )

    try:
        module = modules.start(config)
        wait_until(lambda: "is already connected" in caplog.text, "the duplicate to be closed")
        (opened,) = bus.wait_for(EventType.CONNECTION_OPENED)

    finally:
        twin.stop()
        double.stop()

    assert opened["node_id"] == str(twin.node_id)
    assert list(module.connected) == [twin.node_id]
    assert bus.events(EventType.CONNECTION_FAILED) == []


def test_a_connection_the_peer_closes_is_reported_and_rests(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    monkeypatch: MonkeyPatch,
) -> None:
    # The peer drops the connection as soon as it goes idle.
    monkeypatch.setattr(RequestHandler, "timeout", 0.3)
    write_lists(config.storage, node_list(identity, peers[0]))

    module = modules.start(config)

    (closed,) = bus.wait_for(EventType.CONNECTION_CLOSED)
    assert (closed["node_id"], closed["remote"]) == (str(peers[0].node_id), True)
    assert module.connected == {}

    module.on_idle()
    sleep(0.2)
    assert len(bus.events(EventType.CONNECTION_OPENED)) == 1

    now[0] += RETRY_DELAY
    module.on_idle()

    bus.wait_for(EventType.CONNECTION_OPENED, count=2)


def test_stopping_closes_every_connection(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    module.on_stop()

    closed = bus.events(EventType.CONNECTION_CLOSED)
    assert {message["node_id"] for message in closed} == {str(peer.node_id) for peer in peers}
    assert not any(message["remote"] for message in closed)
    assert module.connected == {}


def test_a_connected_peers_seek_list_is_refreshed(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
) -> None:
    source_of_truth_store(config.storage).write(HELD_ID, HELD)
    # Asking for this is the last step of first contact.
    write_lists(config.storage, node_list(identity, peers[0]), [NOWHERE_ID])
    module = modules.start(config)
    bus.wait_for(EventType.FETCH_ATTEMPTED, hash=NOWHERE_ID.hash)
    assert bus.events(EventType.DATA_SENT) == []

    write_lists(peers[0].storage, {}, [HELD_ID])

    def refreshed() -> bool:
        now[0] += SEEK_REFRESH
        module.on_idle()
        return bool(bus.events(EventType.DATA_SENT))

    wait_until(refreshed, "the peer's seek list to be refreshed")

    (sent,) = bus.events(EventType.DATA_SENT)
    assert (sent["hash"], sent["node_id"]) == (HELD_ID.hash, str(peers[0].node_id))
    peers[0].bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


# -- Fetching --------------------------------------------------------------


def test_a_fetch_is_answered_by_the_peer_that_has_it(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    peers[1].store.write(OFFERED_ID, OFFERED)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    module.handle(fetch_request(OFFERED_ID))

    (succeeded,) = bus.wait_for(EventType.FETCH_SUCCEEDED)
    assert succeeded["node_id"] == str(peers[1].node_id)
    assert (succeeded["algorithm"], succeeded["hash"]) == ("sha256", OFFERED_ID.hash)
    assert node_store(config.storage, peers[1].node_id).read(OFFERED_ID) == OFFERED
    bus.wait_for(EventType.PUT_COMPLETED, hash=OFFERED_ID.hash)
    assert bus.events(EventType.FETCH_FAILED) == []


def test_a_fetch_no_connected_peer_can_answer_fails(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    module.handle(fetch_request(NOWHERE_ID))

    (failed,) = bus.wait_for(EventType.FETCH_FAILED)
    assert failed["hash"] == NOWHERE_ID.hash
    attempts = bus.wait_for(EventType.FETCH_ATTEMPTED, count=2, hash=NOWHERE_ID.hash)
    assert {message["node_id"] for message in attempts} == {str(peer.node_id) for peer in peers}
    assert not any(message["found"] for message in attempts)


def test_a_fetch_without_connections_fails_at_once(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    module = modules.start(config)

    module.handle(fetch_request(OFFERED_ID))

    (failed,) = bus.wait_for(EventType.FETCH_FAILED)
    assert failed["hash"] == OFFERED_ID.hash


def test_a_malformed_fetch_request_raises(modules: Modules, config: LibranetConfig) -> None:
    module = modules.start(config)

    with raises(KeyError):
        module.handle(make_message(EventType.FETCH_REQUESTED, ModuleName.FETCHER, {}))


def test_an_event_it_does_not_handle_is_not_taken_for_a_fetch(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    module = modules.start(config)
    # Shaped like a fetch request, but meant for someone else.
    notice = make_message(
        EventType.EVICTION_NOTICE,
        ModuleName.EVICTION,
        {"algorithm": OFFERED_ID.algorithm, "hash": OFFERED_ID.hash},
    )

    with raises(KeyError):
        module.handle(notice)

    sleep(0.2)
    assert bus.events(EventType.FETCH_FAILED) == []


# -- Lifecycle -------------------------------------------------------------


def test_stopping_a_module_that_never_started_is_harmless(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = ConnectionsModule(ModuleName.CONNECTIONS, queues, config)

    module.on_stop()

    with raises(RuntimeError, match="not running"):
        module.exchange


def test_the_module_subscribes_to_node_lists_and_fetches() -> None:
    assert ConnectionsModule.subscriptions == {
        EventType.NODE_LIST_UPDATED,
        EventType.FETCH_REQUESTED,
    }


def test_factory_builds_a_connections_module(config: LibranetConfig, queues: ModuleQueues) -> None:
    module = connections_module_factory(ModuleName.CONNECTIONS, config, queues)

    assert isinstance(module, ConnectionsModule)
    assert module.name == ModuleName.CONNECTIONS
