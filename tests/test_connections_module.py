"""Tests of the connection manager module, against live fixture peers.

Each fixture peer is this node's own web server over a temp CAS, with an
identity of its own. The module connects and talks to peers on background
threads, so tests wait for what it publishes. A fake clock stands in for
retry delays and seek refreshes, whose timers only :meth:`on_idle` checks;
signatures still use real time.
"""

# pylint: disable=too-many-lines

from __future__ import annotations
from contextlib import suppress
from json import dumps
from logging import DEBUG, INFO, Logger, getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import SHUT_RDWR, create_server, socket
from threading import Event, Thread, current_thread
from time import monotonic, sleep, time
from typing import Any, Callable, Iterator, Mapping, Sequence

from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits, nearest
from libranet.cas.store import CasStore
from libranet.config.models import (
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    PeerConfig,
    StatsConfig,
    StorageConfig,
)
from libranet.connections.module import ConnectionsModule, connections_module_factory
from libranet.connections.peer_exchange import PIPELINE_DEPTH, Retrieval
from libranet.connections.peer_session import PeerSession
from libranet.connections.reverse_dns import ResolveNames
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.messaging.queues import MessageQueue, ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.module import StatsModule
from libranet.supervision.stubs import StubModule
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.router import Router
from libranet.webserver.server import LibranetHTTPServer, RequestHandler, build_router

TIMEOUT = 5.0
RETRY_DELAY = 30.0
SEEK_REFRESH = 10.0
# The Retry-After a fixture peer sends with a 503.
PEER_RETRY_AFTER = 7

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


class DroppableServer(LibranetHTTPServer):
    """This node's web server, able to drop every connection it has accepted."""

    def __init__(
        self, address: tuple[str, int], router: Router, logger: Logger, signer: MessageSigner
    ) -> None:
        super().__init__(address, router, logger, signer)
        self.accepted: list[socket] = []

    def process_request(self, request: socket | tuple[bytes, socket], client_address: Any) -> None:
        if isinstance(request, socket):
            self.accepted.append(request)

        super().process_request(request, client_address)

    def drop_connections(self) -> None:
        for accepted in self.accepted:
            with suppress(OSError):
                accepted.shutdown(SHUT_RDWR)


class FixturePeer:
    """This node's web server on a free local port, with an identity of its own."""

    def __init__(self, root: Path, identity: NodeIdentity | None = None) -> None:
        self.identity = identity or NodeIdentity.from_private_key(generate_private_key(), "sha256")
        self.storage = StorageConfig(data_dir=root / "data", cache_dir=root / "cache")
        self.store = CasStore.source_of_truth(self.storage)
        self.identity.publish_public_key(self.store)
        queues = ModuleQueues(inbox=Queue(), outbox=Queue())
        self.bus = Bus(queues.outbox)
        self.server = DroppableServer(
            ("127.0.0.1", 0),
            build_router(
                self.storage,
                PEER_RETRY_AFTER,
                StubModule(ModuleName.WEBSERVER, queues).publish,
                RequestAuthenticator.of(LibranetConfig(storage=self.storage)),
                allow_unsigned_api_reads=True,
                config_credential=ConfigCredential.of(LibranetConfig(storage=self.storage)),
                node=NodeDescription(self.identity.node_id, NetworkConfig()),
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
    """Derived node, seek, and candidate lists, as the stats module would write them.

    Each node's candidate endpoints are those ``nodes`` names it at, in order.
    """
    storage.derived_dir.mkdir(parents=True, exist_ok=True)

    if nodes is not None:
        storage.node_list_path.write_text(dumps({"nodes": nodes}))
        endpoints: dict[str, list[str]] = {}

        for endpoint, node_id in nodes.items():
            endpoints.setdefault(node_id, []).append(endpoint)

        storage.candidate_list_path.write_text(
            dumps(
                {
                    "nodes": [
                        {"node_id": node_id, "endpoints": listed}
                        for node_id, listed in endpoints.items()
                    ]
                }
            )
        )

    storage.seek_list_path.write_text(dumps({"data": [str(c) for c in sought], "search": []}))


@fixture
def peers(tmp_path: Path) -> Iterator[list[FixturePeer]]:
    peers = [FixturePeer(tmp_path / f"peer-{index}") for index in range(2)]
    yield peers

    for peer in peers:
        peer.stop()


@fixture
def four_peers(tmp_path: Path) -> Iterator[list[FixturePeer]]:
    peers = [FixturePeer(tmp_path / f"one-of-four-{index}") for index in range(4)]
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
            # Step 27's search tests count on two passes, whatever the default.
            search_passes=2,
            seed_file=write_seeds(tmp_path / "seeds.json", {}),
        ),
    )


def with_peers(config: LibranetConfig, **settings: Any) -> LibranetConfig:
    return config.model_copy(update={"peers": config.peers.model_copy(update=settings)})


def with_retry_after(config: LibranetConfig, seconds: int) -> LibranetConfig:
    """``config`` with the ``Retry-After`` this node sends, and leaves a peer that names none."""
    network = config.network.model_copy(update={"retry_after_seconds": seconds})
    return config.model_copy(update={"network": network})


@fixture
def identity(config: LibranetConfig) -> NodeIdentity:
    return NodeIdentity.load(config)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def bus(queues: ModuleQueues) -> Bus:
    return Bus(queues.outbox)


@fixture
def now() -> list[float]:
    return [time()]


def no_names(_address: str) -> Sequence[str]:
    """A reverse DNS lookup that finds nothing, so tests never touch real DNS."""
    return ()


class Modules:
    """Builds connection managers over the test's queues, stopping each at the end."""

    def __init__(self, queues: ModuleQueues, now: list[float]) -> None:
        self._queues = queues
        self._now = now
        self.built: list[ConnectionsModule] = []

    def start(
        self, config: LibranetConfig, resolve_names: ResolveNames = no_names
    ) -> ConnectionsModule:
        module = ConnectionsModule(
            ModuleName.CONNECTIONS,
            self._queues,
            config,
            clock=lambda: self._now[0],
            poll_interval_seconds=0.01,
            resolve_names=resolve_names,
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


def identity_where(wanted: Callable[[ContentId], bool]) -> NodeIdentity:
    """A new identity whose node id is ``wanted``."""
    while True:
        identity = NodeIdentity.from_private_key(generate_private_key(), "sha256")

        if wanted(identity.node_id):
            return identity


def closed_port() -> int:
    with create_server(("127.0.0.1", 0)) as listener:
        return int(listener.getsockname()[1])


def fetch_request(content_id: ContentId) -> Message:
    return make_message(
        EventType.FETCH_REQUESTED,
        ModuleName.FETCHER,
        {"algorithm": content_id.algorithm, "hash": content_id.hash},
    )


def eviction_notice(content_id: ContentId, copies: int = 1) -> Message:
    return make_message(
        EventType.EVICTION_NOTICE,
        ModuleName.EVICTION,
        {"algorithm": content_id.algorithm, "hash": content_id.hash, "copies": copies},
    )


def stored(content_id: ContentId, source: ContentId) -> Message:
    """Content newly stored from ``source``, as the validator announces it."""
    return make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {"algorithm": content_id.algorithm, "hash": content_id.hash, "node_id": str(source)},
    )


def node_list_updated(config: LibranetConfig) -> Message:
    return make_message(
        EventType.NODE_LIST_UPDATED,
        ModuleName.STATS,
        {"path": str(config.storage.candidate_list_path)},
    )


def best_and_other(
    content_id: ContentId, peers: Sequence[FixturePeer]
) -> tuple[FixturePeer, FixturePeer]:
    """The two fixture peers, the one whose id best matches ``content_id``'s hash first."""
    best, other = (
        next(peer for peer in peers if peer.node_id == node_id)
        for node_id in nearest(content_id.hash, [peer.node_id for peer in peers], 2)
    )
    return best, other


def by_match(content_id: ContentId, peers: Sequence[FixturePeer]) -> list[str]:
    """The node ids of ``peers``, the one that best matches ``content_id``'s hash first."""
    node_ids = [peer.node_id for peer in peers]
    return [str(node_id) for node_id in nearest(content_id.hash, node_ids, len(node_ids))]


def asked(bus: Bus, content_id: ContentId) -> list[str]:
    """The peers asked for ``content_id`` so far, in the order they answered."""
    return [
        message["node_id"]
        for message in bus.events(EventType.FETCH_ATTEMPTED, hash=content_id.hash)
    ]


def wait_for_next_pass(caplog: LogCaptureFixture, content_id: ContentId, times: int = 1) -> None:
    """Wait until searches for ``content_id`` have waited ``times`` times for a next pass."""
    wait_until(
        lambda: caplog.text.count(f"The search for {content_id} asks again in") >= times,
        "the search to wait for its next pass",
    )


def content_nearer_to(node_id: ContentId, others: Sequence[ContentId]) -> tuple[ContentId, bytes]:
    """Content whose hash matches ``node_id`` better than it matches any of ``others``."""
    (content,) = contents_nearer_to(node_id, others, 1)
    return content


def contents_nearer_to(
    node_id: ContentId, others: Sequence[ContentId], count: int
) -> list[tuple[ContentId, bytes]]:
    """``count`` items of content whose hashes match ``node_id`` better than any of ``others``."""
    contents: list[tuple[ContentId, bytes]] = []
    index = 0

    while len(contents) < count:
        data = f"content near {node_id}, try {index}".encode()
        content_id = ContentId.for_data(data, "sha256")
        bits = matching_bits(content_id.hash, node_id.hash)

        if all(matching_bits(content_id.hash, other.hash) < bits for other in others):
            contents.append((content_id, data))

        index += 1

    return contents


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

    module = modules.start(
        with_peers(config, min_outgoing_connections=1, min_neighborhood_connections=0)
    )

    bus.wait_for(EventType.CONNECTION_OPENED, node_id=str(peers[0].node_id))
    sleep(0.2)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}


def test_a_neighbor_is_dialed_for_the_second_set(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    tmp_path: Path,
) -> None:
    own = identity.node_id.hash[0]
    elsewhere = [
        FixturePeer(
            tmp_path / f"elsewhere-{index}",
            identity_where(lambda node_id: node_id.hash[0] != own),
        )
        for index in range(2)
    ]
    neighbor = FixturePeer(
        tmp_path / "neighbor", identity_where(lambda node_id: node_id.hash[0] == own)
    )

    try:
        write_lists(config.storage, node_list(identity, *elsewhere, neighbor))
        module = modules.start(
            with_peers(config, min_outgoing_connections=1, min_neighborhood_connections=1)
        )
        bus.wait_for(EventType.CONNECTION_OPENED, count=2)
        sleep(0.2)
        connected = module.connected

    finally:
        for peer in (*elsewhere, neighbor):
            peer.stop()

    # Without the second set, the next peer listed would have made up the
    # number instead of the neighbor listed after it.
    assert connected == {
        elsewhere[0].node_id: elsewhere[0].endpoint,
        neighbor.node_id: neighbor.endpoint,
    }


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
    module.handle(node_list_updated(config))

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


def test_a_node_is_dialed_at_each_endpoint_in_turn_until_one_reaches_it(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    dead = f"http://127.0.0.1:{closed_port()}"
    node_id = str(peers[0].node_id)
    write_lists(config.storage, {**node_list(identity), dead: node_id, peers[0].endpoint: node_id})

    module = modules.start(config)

    (opened,) = bus.wait_for(EventType.CONNECTION_OPENED)
    assert (opened["node_id"], opened["endpoint"]) == (node_id, peers[0].endpoint)
    (failed,) = bus.events(EventType.CONNECTION_FAILED)
    assert (failed["node_id"], failed["endpoint"]) == (node_id, dead)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}
    assert bus.events(EventType.NODE_UNREACHED) == []


def test_an_unreachable_peer_rests_once_every_endpoint_has_failed(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    now: list[float],
) -> None:
    endpoints = [f"http://127.0.0.1:{closed_port()}" for _ in range(2)]
    write_lists(
        config.storage,
        {**node_list(identity), **{endpoint: str(OTHER_ID) for endpoint in endpoints}},
    )

    module = modules.start(config)

    failed = bus.wait_for(EventType.CONNECTION_FAILED, count=2)
    assert [(message["node_id"], message["endpoint"]) for message in failed] == [
        (str(OTHER_ID), endpoint) for endpoint in endpoints
    ]
    # One attempt at the node, however many endpoints it tried.
    (unreached,) = bus.wait_for(EventType.NODE_UNREACHED)
    assert unreached["node_id"] == str(OTHER_ID)

    module.on_idle()
    sleep(0.2)
    assert len(bus.events(EventType.CONNECTION_FAILED)) == 2

    now[0] += RETRY_DELAY
    module.on_idle()

    bus.wait_for(EventType.CONNECTION_FAILED, count=4)
    bus.wait_for(EventType.NODE_UNREACHED, count=2)


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
    sleep(0.2)
    # Stats count attempts per node id, and this one has none to count against.
    assert bus.events(EventType.CONNECTION_FAILED) == []
    assert bus.events(EventType.NODE_UNREACHED) == []


@mark.parametrize("comeback", ["its cool-off ends", "it sends its node list"])
def test_a_node_that_stays_unreachable_is_given_up_on_until_it_may_be_back(
    modules: Modules, config: LibranetConfig, bus: Bus, now: list[float], comeback: str
) -> None:
    # Stats and the connection manager together, as a node runs them.
    giving_up = config.model_copy(update={"stats": StatsConfig(max_node_failures=2)})
    endpoint = f"http://127.0.0.1:{closed_port()}"
    stats = StatsModule(
        ModuleName.STATS,
        ModuleQueues(inbox=Queue(), outbox=Queue()),
        giving_up,
        clock=lambda: now[0],
        poll_interval_seconds=0.01,
    )
    stats.on_start()

    try:
        stats.database.record_address_worked(OTHER_ID, endpoint)
        stats.derive()
        module = modules.start(giving_up)
        stats.handle(bus.wait_for(EventType.NODE_UNREACHED)[-1])
        now[0] += RETRY_DELAY
        module.on_idle()
        stats.handle(bus.wait_for(EventType.NODE_UNREACHED, count=2)[-1])

        # Stats has given up on the node, so its new candidate list leaves it out.
        module.handle(node_list_updated(giving_up))
        now[0] += RETRY_DELAY
        module.on_idle()
        sleep(0.2)
        assert len(bus.events(EventType.NODE_UNREACHED)) == 2

        if comeback == "its cool-off ends":
            now[0] += giving_up.stats.node_cool_off_seconds
            stats.on_idle()

        else:
            stats.handle(
                make_message(
                    EventType.NODES_RECEIVED,
                    ModuleName.WEBSERVER,
                    {"nodes": {endpoint: str(OTHER_ID)}, "sources": {endpoint: "advertised"}},
                )
            )
            stats.derive()

        module.handle(node_list_updated(giving_up))

        bus.wait_for(EventType.NODE_UNREACHED, count=3)

    finally:
        stats.on_stop()


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


class Twins:
    """One identity at two endpoints: ``first`` listed as itself, ``second`` under a stale id."""

    def __init__(self, root: Path) -> None:
        self.first = FixturePeer(root / "first")
        self.second = FixturePeer(root / "second", self.first.identity)

    @property
    def node_id(self) -> ContentId:
        return self.first.node_id

    def stop(self) -> None:
        self.first.stop()
        self.second.stop()


@fixture
def twins(tmp_path: Path) -> Iterator[Twins]:
    twins = Twins(tmp_path / "twins")
    yield twins
    twins.stop()


def connect_twin_then_list_its_double(
    modules: Modules, config: LibranetConfig, identity: NodeIdentity, twins: Twins, bus: Bus
) -> ConnectionsModule:
    """Connect the first twin, then learn the second's endpoint under a stale node id."""
    write_lists(config.storage, node_list(identity, twins.first))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED)

    write_lists(
        config.storage,
        {**node_list(identity, twins.first), twins.second.endpoint: str(OTHER_ID)},
    )
    module.handle(node_list_updated(config))
    bus.wait_for(EventType.ADDRESS_VERIFIED)
    return module


def test_an_endpoint_answering_as_a_connected_node_is_closed_and_recorded_for_both(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    twins: Twins,
    bus: Bus,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")

    module = connect_twin_then_list_its_double(modules, config, identity, twins, bus)

    (failed,) = bus.events(EventType.CONNECTION_FAILED)
    assert (failed["node_id"], failed["endpoint"]) == (str(OTHER_ID), twins.second.endpoint)
    (verified,) = bus.events(EventType.ADDRESS_VERIFIED)
    assert (verified["node_id"], verified["endpoint"]) == (
        str(twins.node_id),
        twins.second.endpoint,
    )
    assert len(bus.events(EventType.CONNECTION_OPENED)) == 1
    assert module.connected == {twins.node_id: twins.first.endpoint}
    assert "is already connected" in caplog.text


def test_an_endpoint_answering_as_a_connected_node_is_not_dialed_while_it_stays_connected(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    twins: Twins,
    bus: Bus,
    now: list[float],
) -> None:
    module = connect_twin_then_list_its_double(modules, config, identity, twins, bus)

    now[0] += RETRY_DELAY
    module.on_idle()
    sleep(0.2)

    assert len(bus.events(EventType.CONNECTION_FAILED)) == 1
    assert len(bus.events(EventType.ADDRESS_VERIFIED)) == 1
    assert module.connected == {twins.node_id: twins.first.endpoint}


def test_an_endpoint_answering_as_a_connected_node_is_dialed_once_that_connection_ends(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    twins: Twins,
    bus: Bus,
    now: list[float],
) -> None:
    module = connect_twin_then_list_its_double(modules, config, identity, twins, bus)
    now[0] += RETRY_DELAY
    module.on_idle()

    twins.first.server.drop_connections()

    bus.wait_for(EventType.CONNECTION_CLOSED)
    _, reopened = bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    assert (reopened["node_id"], reopened["endpoint"]) == (
        str(twins.node_id),
        twins.second.endpoint,
    )
    assert len(bus.events(EventType.CONNECTION_FAILED, node_id=str(OTHER_ID))) == 2
    assert module.connected == {twins.node_id: twins.second.endpoint}


def test_an_endpoint_answering_as_a_node_not_connected_is_admitted_as_that_node(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    write_lists(config.storage, {**node_list(identity), peers[0].endpoint: str(OTHER_ID)})

    module = modules.start(config)

    (opened,) = bus.wait_for(EventType.CONNECTION_OPENED)
    assert (opened["node_id"], opened["endpoint"]) == (str(peers[0].node_id), peers[0].endpoint)
    (failed,) = bus.events(EventType.CONNECTION_FAILED)
    assert (failed["node_id"], failed["endpoint"]) == (str(OTHER_ID), peers[0].endpoint)
    assert module.connected == {peers[0].node_id: peers[0].endpoint}
    assert bus.events(EventType.ADDRESS_VERIFIED) == []
    # The walk stopped there, so the node expected has not yet been tried everywhere.
    assert bus.events(EventType.NODE_UNREACHED) == []


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


def test_the_peers_connected_are_named_at_start_as_they_come_and_go_and_when_asked(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    # The peer drops the connection as soon as it goes idle.
    monkeypatch.setattr(RequestHandler, "timeout", 0.3)
    write_lists(config.storage, node_list(identity, peers[0]))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_CLOSED)

    module.handle(make_message(EventType.PEERS_CONNECTED_REQUESTED, ModuleName.EVICTION, {}))

    named = bus.events(EventType.PEERS_CONNECTED, direction=ConnectionDirection.OUTBOUND)
    assert [message["node_ids"] for message in named] == [[], [str(peers[0].node_id)], [], []]


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
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
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
    assert CasStore.for_node(config.storage, peers[1].node_id).read(OFFERED_ID) == OFFERED
    bus.wait_for(EventType.PUT_COMPLETED, hash=OFFERED_ID.hash)
    assert bus.events(EventType.FETCH_FAILED) == []


def test_a_fetch_no_connected_peer_can_answer_fails(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(with_retry_after(config, PEER_RETRY_AFTER))
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)
    now[0] += PEER_RETRY_AFTER
    module.on_idle()

    (failed,) = bus.wait_for(EventType.FETCH_FAILED)
    assert failed["hash"] == NOWHERE_ID.hash
    attempts = bus.events(EventType.FETCH_ATTEMPTED, hash=NOWHERE_ID.hash)
    assert [message["node_id"] for message in attempts] == by_match(NOWHERE_ID, peers) * 2
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
    miss = make_message(
        EventType.DATA_NOT_FOUND,
        ModuleName.WEBSERVER,
        {"algorithm": OFFERED_ID.algorithm, "hash": OFFERED_ID.hash},
    )

    with raises(KeyError):
        module.handle(miss)

    sleep(0.2)
    assert bus.events(EventType.FETCH_FAILED) == []


@mark.parametrize("passes", [2, 3, 4])
def test_each_pass_after_the_first_asks_every_peer_again_once_its_retry_after_has_passed(
    passes: int,
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    three = four_peers[:3]
    write_lists(config.storage, node_list(identity, *three))
    # Longer than the peers' own, so theirs is what each pass waits for.
    module = modules.start(
        with_retry_after(with_peers(config, search_passes=passes), PEER_RETRY_AFTER + 3)
    )
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    best = by_match(NOWHERE_ID, three)

    module.handle(fetch_request(NOWHERE_ID))

    for made in range(1, passes):
        wait_for_next_pass(caplog, NOWHERE_ID, times=made)
        assert asked(bus, NOWHERE_ID) == best * made

        now[0] += PEER_RETRY_AFTER - 1
        module.on_idle()
        sleep(0.2)
        assert asked(bus, NOWHERE_ID) == best * made

        now[0] += 1
        module.on_idle()

    (failed,) = bus.wait_for(EventType.FETCH_FAILED)

    assert failed["hash"] == NOWHERE_ID.hash
    assert asked(bus, NOWHERE_ID) == best * passes


def test_the_second_pass_waits_no_longer_than_this_nodes_own_retry_after(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    three = four_peers[:3]
    write_lists(config.storage, node_list(identity, *three))
    module = modules.start(with_retry_after(config, PEER_RETRY_AFTER - 2))
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)

    now[0] += PEER_RETRY_AFTER - 2
    module.on_idle()

    bus.wait_for(EventType.FETCH_FAILED)
    # Every peer asked to be left longer, so each is passed over.
    assert asked(bus, NOWHERE_ID) == by_match(NOWHERE_ID, three)


def test_a_pass_that_passed_every_peer_over_still_counts(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    three = four_peers[:3]
    write_lists(config.storage, node_list(identity, *three))
    module = modules.start(
        with_retry_after(with_peers(config, search_passes=3), PEER_RETRY_AFTER - 2)
    )
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    best = by_match(NOWHERE_ID, three)
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)

    # The second pass starts before any peer is due, so passes each over.
    now[0] += PEER_RETRY_AFTER - 2
    module.on_idle()
    wait_for_next_pass(caplog, NOWHERE_ID, times=2)
    assert asked(bus, NOWHERE_ID) == best

    # The third waits out the rest of their Retry-After, and is the last.
    now[0] += 2
    module.on_idle()

    bus.wait_for(EventType.FETCH_FAILED)
    assert asked(bus, NOWHERE_ID) == best + best


def test_with_no_retry_after_of_its_own_a_search_never_waits(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
) -> None:
    three = four_peers[:3]
    write_lists(config.storage, node_list(identity, *three))
    module = modules.start(with_retry_after(config, 0))
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)

    module.handle(fetch_request(NOWHERE_ID))

    bus.wait_for(EventType.FETCH_FAILED)
    assert asked(bus, NOWHERE_ID) == by_match(NOWHERE_ID, three)


def test_a_peer_that_cannot_be_asked_is_passed_over_until_the_second_pass(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    three = four_peers[:3]
    write_lists(config.storage, node_list(identity, *three))
    module = modules.start(with_retry_after(config, PEER_RETRY_AFTER))
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    best = by_match(NOWHERE_ID, three)
    retrieve = module.exchange.retrieve
    tried: list[str] = []

    def failing_for_best(session: PeerSession, content_id: ContentId) -> Retrieval:
        tried.append(str(session.node_id))

        if tried[-1] == best[0]:
            raise ConnectionResetError("gone")

        return retrieve(session, content_id)

    monkeypatch.setattr(module.exchange, "retrieve", failing_for_best)

    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)
    now[0] += PEER_RETRY_AFTER
    module.on_idle()

    bus.wait_for(EventType.FETCH_FAILED)
    assert tried == best + best
    assert asked(bus, NOWHERE_ID) == best[1:] + best[1:]


def test_a_search_that_goes_wrong_ends_as_failed(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    caplog: LogCaptureFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    def broken(session: PeerSession, content_id: ContentId) -> Retrieval:
        raise RuntimeError("broken")

    monkeypatch.setattr(module.exchange, "retrieve", broken)

    module.handle(fetch_request(NOWHERE_ID))

    (failed,) = bus.wait_for(EventType.FETCH_FAILED)
    assert failed["hash"] == NOWHERE_ID.hash
    assert f"Searching for {NOWHERE_ID} failed" in caplog.text


def test_a_peer_that_connects_during_a_search_takes_its_place_in_the_order(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    best = by_match(NOWHERE_ID, four_peers)
    others = [peer for peer in four_peers if str(peer.node_id) != best[0]]
    write_lists(config.storage, node_list(identity, *others))
    module = modules.start(with_retry_after(config, PEER_RETRY_AFTER))
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)
    assert asked(bus, NOWHERE_ID) == best[1:]

    write_lists(config.storage, node_list(identity, *four_peers))
    module.handle(node_list_updated(config))
    bus.wait_for(EventType.CONNECTION_OPENED, count=4)
    now[0] += PEER_RETRY_AFTER
    module.on_idle()

    bus.wait_for(EventType.FETCH_FAILED)
    assert asked(bus, NOWHERE_ID) == best[1:] + best


def test_a_search_that_found_nothing_holds_off_another_for_a_while(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    hold = 60
    write_lists(config.storage, node_list(identity, *four_peers[:3]))
    module = modules.start(
        with_retry_after(with_peers(config, failed_search_hold_seconds=hold), PEER_RETRY_AFTER)
    )
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)

    # The search under way answers this one.
    module.handle(fetch_request(NOWHERE_ID))
    now[0] += PEER_RETRY_AFTER
    module.on_idle()
    bus.wait_for(EventType.FETCH_FAILED)
    assert len(asked(bus, NOWHERE_ID)) == 6

    now[0] += hold - 1
    module.handle(fetch_request(NOWHERE_ID))
    assert f"Not searching for {NOWHERE_ID} for 1 seconds" in caplog.text

    now[0] += 1
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID, times=2)
    assert len(asked(bus, NOWHERE_ID)) == 9
    assert len(bus.events(EventType.FETCH_FAILED)) == 1


def test_a_search_that_asked_no_peer_is_not_held(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    module = modules.start(config)

    module.handle(fetch_request(NOWHERE_ID))
    bus.wait_for(EventType.FETCH_FAILED)
    module.handle(fetch_request(NOWHERE_ID))

    bus.wait_for(EventType.FETCH_FAILED, count=2)


def test_content_stored_during_a_search_ends_it(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    four_peers: list[FixturePeer],
    bus: Bus,
    now: list[float],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    write_lists(config.storage, node_list(identity, *four_peers[:3]))
    module = modules.start(with_retry_after(config, PEER_RETRY_AFTER))
    bus.wait_for(EventType.CONNECTION_OPENED, count=3)
    module.handle(fetch_request(NOWHERE_ID))
    wait_for_next_pass(caplog, NOWHERE_ID)

    module.handle(stored(NOWHERE_ID, OTHER_ID))
    now[0] += PEER_RETRY_AFTER
    module.on_idle()
    sleep(0.2)

    assert len(asked(bus, NOWHERE_ID)) == 3
    assert bus.events(EventType.FETCH_FAILED) == []
    assert f"{NOWHERE_ID} arrived while being searched for" in caplog.text

    # It did not fail, so nothing holds off the next.
    module.handle(fetch_request(NOWHERE_ID))
    bus.wait_for(EventType.FETCH_ATTEMPTED, count=6, hash=NOWHERE_ID.hash)


# -- Handing off -----------------------------------------------------------


def test_a_hand_off_goes_to_the_best_matching_peers(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    module.handle(eviction_notice(HELD_ID, copies=2))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    best_first = nearest(HELD_ID.hash, [peer.node_id for peer in peers], 2)
    assert (answer["algorithm"], answer["hash"]) == ("sha256", HELD_ID.hash)
    assert answer["node_ids"] == [str(node_id) for node_id in best_first]
    assert len(bus.events(EventType.DATA_SENT, hash=HELD_ID.hash)) == 2

    for peer in peers:
        peer.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


def test_a_hand_off_stops_once_enough_peers_accept(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = (
        next(peer for peer in peers if peer.node_id == node_id)
        for node_id in nearest(HELD_ID.hash, [peer.node_id for peer in peers], 2)
    )

    module.handle(eviction_notice(HELD_ID, copies=1))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    assert answer["node_ids"] == [str(best.node_id)]
    best.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)
    assert other.bus.events(EventType.PUT_COMPLETED, hash=HELD_ID.hash) == []


def test_a_hand_off_the_best_peer_refuses_goes_to_the_next_best(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = best_and_other(HELD_ID, peers)
    hand_off = module.exchange.hand_off
    offered: list[ContentId] = []

    def refused_by_best(session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        offered.append(session.node_id)
        return session.node_id != best.node_id and hand_off(session, content_id, body)

    monkeypatch.setattr(module.exchange, "hand_off", refused_by_best)

    module.handle(eviction_notice(HELD_ID))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    assert answer["node_ids"] == [str(other.node_id)]
    assert offered == [best.node_id, other.node_id]
    other.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)
    assert best.bus.events(EventType.PUT_COMPLETED, hash=HELD_ID.hash) == []


def test_a_peer_that_cannot_be_reached_is_passed_over(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = nearest(HELD_ID.hash, [peer.node_id for peer in peers], 2)
    hand_off = module.exchange.hand_off

    def failing_for_best(session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        if session.node_id == best:
            raise ConnectionResetError("gone")

        return hand_off(session, content_id, body)

    monkeypatch.setattr(module.exchange, "hand_off", failing_for_best)

    module.handle(eviction_notice(HELD_ID))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    assert answer["node_ids"] == [str(other)]


def test_a_hand_off_without_connected_peers_falls_short_at_once(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    module = modules.start(config)

    module.handle(eviction_notice(HELD_ID))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    assert answer["node_ids"] == []


def test_content_not_held_is_not_handed_off(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    caplog: LogCaptureFixture,
) -> None:
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    with caplog.at_level(INFO):
        module.handle(eviction_notice(NOWHERE_ID))
        (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)

    assert answer["node_ids"] == []
    assert f"{NOWHERE_ID} is not held" in caplog.text
    assert bus.events(EventType.DATA_SENT) == []


def test_a_hand_off_that_goes_wrong_is_still_answered(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    def broken(session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        raise RuntimeError("broken")

    monkeypatch.setattr(module.exchange, "hand_off", broken)

    module.handle(eviction_notice(HELD_ID))

    (answer,) = bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    assert answer["node_ids"] == []
    assert f"Handing off {HELD_ID} failed" in caplog.text


def test_a_hand_off_under_way_is_not_started_again(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    gate = Event()
    hand_off = module.exchange.hand_off

    def held_up(session: PeerSession, content_id: ContentId, body: bytes) -> bool:
        gate.wait(TIMEOUT)
        return hand_off(session, content_id, body)

    monkeypatch.setattr(module.exchange, "hand_off", held_up)

    module.handle(eviction_notice(HELD_ID))
    module.handle(eviction_notice(HELD_ID))
    gate.set()

    bus.wait_for(EventType.EVICTION_ACKNOWLEDGED)
    sleep(0.2)
    assert len(bus.events(EventType.EVICTION_ACKNOWLEDGED)) == 1

    module.handle(eviction_notice(HELD_ID))

    bus.wait_for(EventType.EVICTION_ACKNOWLEDGED, count=2)


# -- Pushing new content ---------------------------------------------------


def test_new_content_is_pushed_to_the_best_matching_peer_alone(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = best_and_other(HELD_ID, peers)

    module.handle(stored(HELD_ID, identity.node_id))

    best.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)
    (sent,) = bus.wait_for(EventType.DATA_SENT, hash=HELD_ID.hash)
    assert sent["node_id"] == str(best.node_id)
    assert other.bus.events(EventType.PUT_COMPLETED, hash=HELD_ID.hash) == []


def test_content_received_is_pushed_on_to_the_best_matching_peer(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = best_and_other(HELD_ID, peers)

    module.handle(stored(HELD_ID, other.node_id))

    best.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


def test_content_is_not_pushed_back_to_the_peer_it_came_from(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, _ = best_and_other(HELD_ID, peers)

    module.handle(stored(HELD_ID, best.node_id))

    wait_until(lambda: f"Not pushing {HELD_ID} back" in caplog.text, "the push to be skipped")
    assert bus.events(EventType.DATA_SENT) == []


def test_new_content_is_pushed_even_when_this_node_matches_it_better(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
) -> None:
    content_id, data = content_nearer_to(identity.node_id, [peer.node_id for peer in peers])
    CasStore.source_of_truth(config.storage).write(content_id, data)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, _ = best_and_other(content_id, peers)

    module.handle(stored(content_id, identity.node_id))

    best.bus.wait_for(EventType.PUT_COMPLETED, hash=content_id.hash)


def test_new_content_waits_for_a_connection_to_open(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    module = modules.start(config)

    module.handle(stored(HELD_ID, identity.node_id))
    wait_until(lambda: f"No peer to push {HELD_ID} to yet" in caplog.text, "the push to wait")

    write_lists(config.storage, node_list(identity, peers[0]))
    module.handle(node_list_updated(config))

    peers[0].bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


def test_a_push_goes_to_a_peer_that_connected_while_it_was_under_way(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, peers[0]))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED)
    hand_off_many = module.exchange.hand_off_many

    def unreachable_first(
        session: PeerSession, held: Sequence[tuple[ContentId, bytes]]
    ) -> list[bool]:
        if session.node_id != peers[0].node_id:
            return hand_off_many(session, held)

        if peers[1].node_id not in module.connected:
            write_lists(config.storage, node_list(identity, *peers))
            module.handle(node_list_updated(config))
            wait_until(lambda: peers[1].node_id in module.connected, "the second peer")

        raise ConnectionResetError("gone")

    monkeypatch.setattr(module.exchange, "hand_off_many", unreachable_first)

    module.handle(stored(HELD_ID, identity.node_id))

    peers[1].bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


def test_a_peer_that_cannot_be_reached_is_passed_over_for_a_push(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, other = best_and_other(HELD_ID, peers)
    hand_off_many = module.exchange.hand_off_many

    def failing_for_best(
        session: PeerSession, held: Sequence[tuple[ContentId, bytes]]
    ) -> list[bool]:
        if session.node_id == best.node_id:
            raise ConnectionResetError("gone")

        return hand_off_many(session, held)

    monkeypatch.setattr(module.exchange, "hand_off_many", failing_for_best)

    module.handle(stored(HELD_ID, identity.node_id))

    other.bus.wait_for(EventType.PUT_COMPLETED, hash=HELD_ID.hash)


def test_a_push_the_best_peer_refuses_goes_no_further(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG, logger="libranet")
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    best, _ = best_and_other(HELD_ID, peers)
    offered: list[ContentId] = []

    def refusing(session: PeerSession, held: Sequence[tuple[ContentId, bytes]]) -> list[bool]:
        offered.append(session.node_id)
        return [False] * len(held)

    monkeypatch.setattr(module.exchange, "hand_off_many", refusing)

    module.handle(stored(HELD_ID, identity.node_id))

    wait_until(lambda: f"refused {HELD_ID}" in caplog.text, "the push to be refused")
    assert offered == [best.node_id]


def test_content_no_longer_held_is_not_pushed(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    bus: Bus,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(INFO, logger="libranet")
    module = modules.start(config)

    module.handle(stored(NOWHERE_ID, identity.node_id))

    wait_until(lambda: f"{NOWHERE_ID} is no longer held" in caplog.text, "the push to be dropped")
    assert bus.events(EventType.DATA_SENT) == []


def test_a_push_that_goes_wrong_is_logged(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)

    def broken(session: PeerSession, held: Sequence[tuple[ContentId, bytes]]) -> list[bool]:
        raise RuntimeError("broken")

    monkeypatch.setattr(module.exchange, "hand_off_many", broken)

    module.handle(stored(HELD_ID, identity.node_id))

    wait_until(lambda: f"Pushing {HELD_ID} failed" in caplog.text, "the failure to be logged")


# -- Pushing new content in batches ----------------------------------------


@fixture
def one_push_worker(monkeypatch: MonkeyPatch) -> None:
    """A single push worker, so what queues up behind it is taken in a known order."""
    monkeypatch.setattr("libranet.connections.module.PUSH_WORKERS", 1)


class HeldPushes:
    """Stands in for pipelined pushes, recording each, and holding the first until released.

    While the one push worker is held, new content queues up behind it. A
    push to ``unreachable`` fails as if that peer could not be reached.
    """

    def __init__(
        self,
        module: ConnectionsModule,
        monkeypatch: MonkeyPatch,
        unreachable: ContentId | None = None,
    ) -> None:
        self._hand_off_many = module.exchange.hand_off_many
        self._unreachable = unreachable
        self._held = Event()
        self._released = Event()
        # Each push, as the peer it went to and the content ids it carried.
        self.pushes: list[tuple[ContentId, list[ContentId]]] = []
        self.worker: Thread | None = None
        monkeypatch.setattr(module.exchange, "hand_off_many", self._push)

    def wait_until_held(self) -> None:
        assert self._held.wait(TIMEOUT), "timed out waiting for the first push"

    def release(self) -> None:
        self._released.set()

    def _push(self, session: PeerSession, held: Sequence[tuple[ContentId, bytes]]) -> list[bool]:
        self.pushes.append((session.node_id, [content_id for content_id, _ in held]))

        if not self._held.is_set():
            self.worker = current_thread()
            self._held.set()
            self._released.wait(TIMEOUT)

        if session.node_id == self._unreachable:
            raise ConnectionResetError("gone")

        return self._hand_off_many(session, held)


@mark.usefixtures("one_push_worker")
def test_new_content_waiting_is_pushed_in_pipelined_batches_each_to_its_best_peer(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    # pylint: disable=too-many-locals
    store = CasStore.source_of_truth(config.storage)
    content_ids: list[ContentId] = []

    for index in range(1 + 2 * PIPELINE_DEPTH):
        data = f"new content {index}".encode()
        content_ids.append(ContentId.for_data(data, "sha256"))
        store.write(content_ids[-1], data)

    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    pushes = HeldPushes(module, monkeypatch)
    first, *waiting = content_ids

    module.handle(stored(first, identity.node_id))
    pushes.wait_until_held()

    for content_id in waiting:
        module.handle(stored(content_id, identity.node_id))

    pushes.release()

    bus.wait_for(EventType.DATA_SENT, count=len(content_ids))
    best = {content_id: by_match(content_id, peers)[0] for content_id in content_ids}
    expected = [(best[first], [first])]

    # Each batch the worker took, one pipelined push to each peer it is bound for.
    for start in range(0, len(waiting), PIPELINE_DEPTH):
        batch = waiting[start : start + PIPELINE_DEPTH]

        for node_id in dict.fromkeys(best[content_id] for content_id in batch):
            expected.append((node_id, [item for item in batch if best[item] == node_id]))

    assert [(str(node_id), pushed) for node_id, pushed in pushes.pushes] == expected
    assert len(pushes.pushes) < len(content_ids)


@mark.usefixtures("one_push_worker")
def test_what_could_not_reach_its_best_peer_goes_on_together_to_the_next_best(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    # pylint: disable=too-many-locals
    best, other = peers
    contents = contents_nearer_to(best.node_id, [other.node_id], 4)
    store = CasStore.source_of_truth(config.storage)

    for content_id, data in contents:
        store.write(content_id, data)

    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    pushes = HeldPushes(module, monkeypatch, unreachable=best.node_id)
    first, *waiting = [content_id for content_id, _ in contents]

    module.handle(stored(first, identity.node_id))
    pushes.wait_until_held()

    for content_id in waiting:
        module.handle(stored(content_id, identity.node_id))

    pushes.release()

    sent = bus.wait_for(EventType.DATA_SENT, count=len(contents))
    assert pushes.pushes == [
        (best.node_id, [first]),
        (other.node_id, [first]),
        (best.node_id, waiting),
        (other.node_id, waiting),
    ]
    assert {message["node_id"] for message in sent} == {str(other.node_id)}


@mark.usefixtures("one_push_worker")
def test_a_push_worker_told_to_stop_while_taking_a_batch_still_stops(
    modules: Modules,
    config: LibranetConfig,
    identity: NodeIdentity,
    peers: list[FixturePeer],
    bus: Bus,
    monkeypatch: MonkeyPatch,
) -> None:
    store = CasStore.source_of_truth(config.storage)
    store.write(HELD_ID, HELD)
    store.write(OFFERED_ID, OFFERED)
    write_lists(config.storage, node_list(identity, *peers))
    module = modules.start(config)
    bus.wait_for(EventType.CONNECTION_OPENED, count=2)
    pushes = HeldPushes(module, monkeypatch)

    module.handle(stored(HELD_ID, identity.node_id))
    pushes.wait_until_held()
    # Queued behind the push held, and taken with being told to stop.
    module.handle(stored(OFFERED_ID, identity.node_id))
    module.on_stop()
    pushes.release()

    assert pushes.worker is not None
    pushes.worker.join(TIMEOUT)
    assert not pushes.worker.is_alive()


# -- Reverse DNS -----------------------------------------------------------


def nodes_received(nodes: Mapping[str, str], sources: Mapping[str, str]) -> Message:
    return make_message(
        EventType.NODES_RECEIVED, ModuleName.WEBSERVER, {"nodes": nodes, "sources": sources}
    )


def test_addresses_peers_were_observed_at_are_looked_up(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    asked: list[str] = []

    def names(address: str) -> Sequence[str]:
        asked.append(address)
        return ["peer.example.org"] if address == "203.0.113.9" else []

    module = modules.start(config, names)

    module.handle(
        nodes_received(
            {
                "http://203.0.113.9:4300": str(OTHER_ID),
                "http://198.51.100.7:8080": str(OTHER_ID),
                "http://198.51.100.8:8080": str(OTHER_ID),
            },
            {
                "http://203.0.113.9:4300": "observed",
                "http://198.51.100.7:8080": "advertised",
            },
        )
    )

    (named,) = bus.wait_for(EventType.NODES_RECEIVED)
    assert named["nodes"] == {"http://peer.example.org:4300": str(OTHER_ID)}
    assert named["sources"] == {"http://peer.example.org:4300": "reverse_dns"}
    # Only the observed address was looked up.
    assert asked == ["203.0.113.9"]


def test_reverse_dns_can_be_switched_off(
    modules: Modules, config: LibranetConfig, bus: Bus
) -> None:
    asked: list[str] = []

    def names(address: str) -> Sequence[str]:
        asked.append(address)
        return ["peer.example.org"]

    module = modules.start(with_peers(config, reverse_dns=False), names)

    module.handle(
        nodes_received(
            {"http://203.0.113.9:4300": str(OTHER_ID)},
            {"http://203.0.113.9:4300": "observed"},
        )
    )

    sleep(0.2)
    assert bus.events(EventType.NODES_RECEIVED) == []
    assert asked == []


# -- Lifecycle -------------------------------------------------------------


def test_stopping_a_module_that_never_started_is_harmless(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    module = ConnectionsModule(ModuleName.CONNECTIONS, queues, config)

    module.on_stop()

    with raises(RuntimeError, match="not running"):
        module.exchange  # pylint: disable=pointless-statement


def test_the_module_subscribes_to_node_lists_fetches_hand_offs_and_new_content() -> None:
    assert ConnectionsModule.subscriptions == {
        EventType.NODE_LIST_UPDATED,
        EventType.NODES_RECEIVED,
        EventType.FETCH_REQUESTED,
        EventType.EVICTION_NOTICE,
        EventType.DATA_STORED,
        EventType.PEERS_CONNECTED_REQUESTED,
    }


def test_factory_builds_a_connections_module(config: LibranetConfig, queues: ModuleQueues) -> None:
    module = connections_module_factory(ModuleName.CONNECTIONS, config, queues)

    assert isinstance(module, ConnectionsModule)
    assert module.name == ModuleName.CONNECTIONS
