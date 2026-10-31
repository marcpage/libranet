"""Tests for what this node says to a peer, against live fixture peers.

Most fixture peers are this node's own web server (Steps 5, 7, 9) over a temp
CAS, with an identity of their own. What the client learns shows in what it
publishes; what the peer learns shows in what the peer's web server
publishes. Peers that misbehave are raw sockets answering with canned bytes.
"""

# pylint: disable=too-many-lines

from __future__ import annotations
from json import dumps
from logging import DEBUG, WARNING, LogRecord, getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import create_server, socket
from threading import Thread
from typing import Iterator, Mapping, Sequence
from zlib import compress

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.archive import ArchiveSink
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import (
    IdentityConfig,
    LibranetConfig,
    NetworkConfig,
    PeerConfig,
    StorageConfig,
)
from libranet.connections.errors import ConnectionClosedError, PeerAuthenticationError
from libranet.connections.peer_connection import PeerConnection
from libranet.connections.peer_exchange import PeerExchange, Retrieval
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.identity.authentication import RequestAuthenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.server import LibranetHTTPServer, build_router

from tests.helpers import new_identity, shunned_list, with_node_key
from tests.stubs import StubModule

LOGGER = getLogger("test.connections")
TIMEOUT = 5.0
# The Retry-After the fixture peer sends with a 503.
RETRY_AFTER = 7

HELD = b"content the client holds"
HELD_ID = ContentId.for_data(HELD, "sha256")
OFFERED = b"content the peer holds"
OFFERED_ID = ContentId.for_data(OFFERED, "sha256")
NOWHERE_ID = ContentId.for_data(b"content nobody holds", "sha256")
OTHER_ID = ContentId.for_data(b"some other node's key", "sha256")
THIRD_ID = ContentId.for_data(b"a third node's key", "sha256")


def drain(queue: ModuleQueues, into: list[Message]) -> list[Message]:
    while True:
        try:
            into.append(queue.outbox.get(block=False))

        except Empty:
            return into


class FixturePeer:
    """This node's web server on a free local port, with an identity of its own."""

    def __init__(self, root: Path, identity: NodeIdentity | None = None) -> None:
        self.identity = identity or new_identity()
        # Answering a search with whatever it holds nearest, however far, so
        # that every peer asked answers something.
        self.storage = StorageConfig(
            data_dir=root / "data", cache_dir=root / "cache", search_min_bits=0
        )
        self.store = CasStore.source_of_truth(self.storage)
        self.queues = ModuleQueues(inbox=Queue(), outbox=Queue())
        self._seen: list[Message] = []
        self.server = LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_router(
                self.storage,
                RETRY_AFTER,
                StubModule(ModuleName.WEBSERVER, self.queues).publish,
                RequestAuthenticator.of(LibranetConfig(storage=self.storage)),
                allow_unsigned_api_reads=True,
                config_port=8180,
            ),
            getLogger("test.webserver"),
            MessageSigner(self.identity),
        )
        self._thread = Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def write_lists(
        self,
        nodes: Mapping[str, str] | None = None,
        sought: Sequence[ContentId] = (),
        searches: Sequence[str] = (),
    ) -> None:
        write_lists(self.storage, nodes, sought, searches)

    def published(self, event: EventType) -> list[Message]:
        return [message for message in drain(self.queues, self._seen) if message["event"] == event]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()


def write_lists(
    storage: StorageConfig,
    nodes: Mapping[str, str] | None,
    sought: Sequence[ContentId],
    searches: Sequence[str] = (),
) -> None:
    """Derived node and seek lists, as the stats module would write them."""
    storage.derived_dir.mkdir(parents=True, exist_ok=True)

    if nodes is not None:
        storage.node_list_path.write_text(dumps({"nodes": nodes}))

    storage.seek_list_path.write_text(
        dumps({"data": [str(c) for c in sought], "search": list(searches)})
    )


@fixture
def peer(tmp_path: Path) -> Iterator[FixturePeer]:
    """A peer holding its own public key, as every node does."""
    peer = FixturePeer(tmp_path / "peer")
    peer.identity.publish_public_key(peer.store)
    yield peer
    peer.stop()


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    root = tmp_path / "client"
    return with_node_key(
        LibranetConfig(
            storage=StorageConfig(
                data_dir=root / "data",
                cache_dir=root / "cache",
                # What answering a search entry is tested against, whatever the default.
                search_min_bits=16,
            ),
            identity=IdentityConfig(key_dir=root / "keys"),
            peers=PeerConfig(
                connect_timeout_seconds=TIMEOUT,
                request_timeout_seconds=TIMEOUT,
            ),
        )
    )


@fixture
def identity(config: LibranetConfig) -> NodeIdentity:
    return NodeIdentity.load(config)


@fixture
def exchange(identity: NodeIdentity, config: LibranetConfig, queues: ModuleQueues) -> PeerExchange:
    return PeerExchange(
        identity, config, StubModule(ModuleName.CONNECTIONS, queues).publish, LOGGER
    )


@fixture
def session(
    exchange: PeerExchange, peer: FixturePeer, queues: ModuleQueues
) -> Iterator[PeerSession]:
    session = exchange.open(peer.endpoint)
    # Opening announces the peer's key as stored, which is tested on its own.
    drain(queues, [])
    yield session
    session.close()


def published(queues: ModuleQueues, event: EventType) -> list[Message]:
    return [message for message in drain(queues, []) if message["event"] == event]


def content_fields(content_id: ContentId, node_id: ContentId) -> dict[str, str]:
    return {"algorithm": content_id.algorithm, "hash": content_id.hash, "node_id": str(node_id)}


def payload(message: Message) -> dict[str, object]:
    return {key: value for key, value in message.items() if key not in {"timestamp", "source"}}


# -- Steps 1 and 2: establishing who the peer is --------------------------


def test_first_contact_proves_the_peers_identity_and_swaps_keys(
    exchange: PeerExchange,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    session = exchange.open(peer.endpoint)

    try:
        assert session.node_id == peer.identity.node_id
        assert session.endpoint == peer.endpoint
        assert not session.closed
        client_store = CasStore.source_of_truth(config.storage)
        assert client_store.read(peer.identity.node_id) == peer.identity.public_key
        # The peer holds this node's key at once, so it could verify from then on.
        assert peer.store.read(identity.node_id) == identity.public_key
        (asked,) = peer.published(EventType.DATA_REQUESTED)
        assert asked["hash"] == peer.identity.node_id.hash
        # The key is announced as stored, as the validator announces what it stores.
        (announced,) = published(queues, EventType.DATA_STORED)
        assert payload(announced) == {
            "event": EventType.DATA_STORED,
            **content_fields(peer.identity.node_id, peer.identity.node_id),
            "size": len(peer.identity.public_key),
        }

    finally:
        session.close()


def test_a_key_already_held_is_not_asked_for(
    exchange: PeerExchange, peer: FixturePeer, config: LibranetConfig, queues: ModuleQueues
) -> None:
    peer.identity.publish_public_key(CasStore.source_of_truth(config.storage))

    opened = exchange.open(peer.endpoint)
    opened.close()

    assert opened.node_id == peer.identity.node_id
    assert peer.published(EventType.DATA_REQUESTED) == []
    assert published(queues, EventType.DATA_STORED) == []


def test_a_compressed_public_key_is_held_as_sent(
    exchange: PeerExchange, tmp_path: Path, config: LibranetConfig, queues: ModuleQueues
) -> None:
    peer = FixturePeer(tmp_path / "compressing")
    compressed = compress(peer.identity.public_key)
    peer.store.write(peer.identity.node_id, compressed)

    try:
        # Opening verifies the key's response against the key as held.
        session = exchange.open(peer.endpoint)
        session.close()

    finally:
        peer.stop()

    assert session.node_id == peer.identity.node_id
    assert CasStore.source_of_truth(config.storage).read(peer.identity.node_id) == compressed
    (announced,) = published(queues, EventType.DATA_STORED)
    assert announced["size"] == len(compressed)


def test_a_peer_that_does_not_send_its_key_is_refused(
    exchange: PeerExchange, tmp_path: Path, config: LibranetConfig
) -> None:
    peer = FixturePeer(tmp_path / "keyless")

    try:
        with raises(PeerAuthenticationError, match="did not send its public key"):
            exchange.open(peer.endpoint)

    finally:
        peer.stop()

    assert not CasStore.source_of_truth(config.storage).exists(peer.identity.node_id)


def test_an_endpoint_this_node_cannot_dial_is_refused(exchange: PeerExchange) -> None:
    with raises(ValueError, match="Cannot dial"):
        exchange.open("https://127.0.0.1:1")


# -- Peers that misbehave --------------------------------------------------


class RawPeer:
    """Accepts one connection and answers its requests with canned bytes, in turn."""

    def __init__(self, responses: Sequence[bytes]) -> None:
        self._listener = create_server(("127.0.0.1", 0))
        self._listener.settimeout(TIMEOUT)
        self._responses = responses
        self._thread = Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self._listener.getsockname()[1]}"

    def join(self) -> None:
        self._thread.join(TIMEOUT)

    def _serve(self) -> None:
        with self._listener:
            try:
                connection, _ = self._listener.accept()

            except OSError:
                return

        with connection:
            connection.settimeout(TIMEOUT)
            buffer = b""

            try:
                for response in self._responses:
                    buffer = _skip_request(connection, buffer)
                    connection.sendall(response)

            except OSError:
                return


def _skip_request(sock: socket, buffer: bytes) -> bytes:
    """Read one whole request from ``sock``; returns what arrived after it."""
    while b"\r\n\r\n" not in buffer:
        data = sock.recv(65536)

        if not data:
            raise ConnectionError("The client closed the connection")

        buffer += data

    head, _, rest = buffer.partition(b"\r\n\r\n")
    length = 0

    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")

        if name.strip().lower() == b"content-length":
            length = int(value)

    while len(rest) < length:
        data = sock.recv(65536)

        if not data:
            raise ConnectionError("The client closed the connection")

        rest += data

    return rest[length:]


def signed(
    identity: NodeIdentity,
    status: int,
    body: bytes = b"",
    headers: Mapping[str, str] | None = None,
) -> bytes:
    fields = MessageSigner(identity).sign_response(
        status, {"Content-Length": str(len(body)), **(headers or {})}, body
    )
    head = "".join(f"{name}: {value}\r\n" for name, value in fields.items())
    return f"HTTP/1.1 {status} Reason\r\n{head}\r\n".encode() + body


def test_a_peer_that_does_not_sign_is_refused(exchange: PeerExchange) -> None:
    peer = RawPeer([b"HTTP/1.1 201 Created\r\nContent-Length: 0\r\n\r\n"])

    with raises(PeerAuthenticationError, match="failed verification"):
        exchange.open(peer.endpoint)

    peer.join()


def test_a_peer_that_changes_identity_is_refused(
    exchange: PeerExchange, config: LibranetConfig
) -> None:
    claimed, actual = new_identity(), new_identity()
    actual.publish_public_key(CasStore.source_of_truth(config.storage))
    peer = RawPeer([signed(claimed, 201), signed(actual, 200, claimed.public_key)])

    with raises(PeerAuthenticationError, match="then as"):
        exchange.open(peer.endpoint)

    peer.join()


def test_a_peer_that_cannot_sign_for_the_id_it_claims_is_refused(
    exchange: PeerExchange,
) -> None:
    # Another node's id and public key are public; its private key is not.
    real = new_identity()
    impostor = NodeIdentity(generate_private_key(), real.public_key, real.node_id)
    peer = RawPeer([signed(impostor, 201), signed(impostor, 200, real.public_key)])

    # Only checkable once the key is held, which is after the key is fetched.
    with raises(PeerAuthenticationError, match=r"^Response from .* failed verification"):
        exchange.open(peer.endpoint)

    peer.join()


def test_a_peer_whose_id_is_not_a_key_is_refused(
    exchange: PeerExchange, config: LibranetConfig
) -> None:
    content_id = ContentId.for_data(b"not a key", "sha256")
    posing = NodeIdentity(generate_private_key(), b"not a key", content_id)
    peer = RawPeer([signed(posing, 201), signed(posing, 200, b"not a key")])

    with raises(PeerAuthenticationError, match="Not a PEM public key"):
        exchange.open(peer.endpoint)

    peer.join()
    assert not CasStore.source_of_truth(config.storage).exists(content_id)


def test_a_peer_that_closes_after_identifying_itself_is_refused(
    exchange: PeerExchange, config: LibranetConfig
) -> None:
    closing = new_identity()
    closing.publish_public_key(CasStore.source_of_truth(config.storage))
    peer = RawPeer([signed(closing, 401, headers={"Connection": "close"})])

    with raises(ConnectionClosedError):
        exchange.open(peer.endpoint)

    peer.join()


def test_a_response_the_peer_did_not_prove_closes_the_session(
    peer: FixturePeer, config: LibranetConfig, identity: NodeIdentity
) -> None:
    client_store = CasStore.source_of_truth(config.storage)
    verifier = MessageVerifier(client_store, 5.0, 30.0)

    def session_expecting(node_id: ContentId) -> PeerSession:
        connection = PeerConnection.open(
            "127.0.0.1",
            peer.server.server_address[1],
            MessageSigner(identity),
            LOGGER,
            connect_timeout_seconds=TIMEOUT,
            request_timeout_seconds=TIMEOUT,
            max_body_bytes=1 << 20,
        )
        return PeerSession(connection, peer.endpoint, node_id, verifier)

    unverifiable = session_expecting(peer.identity.node_id)

    with raises(
        PeerAuthenticationError, match=r"^Response to GET /data/nodes from .* failed verification"
    ):
        unverifiable.exchange([PeerRequest("GET", "/data/nodes")])

    assert unverifiable.closed and unverifiable.closed_locally

    peer.identity.publish_public_key(client_store)
    impostor = session_expecting(OTHER_ID)

    with raises(
        PeerAuthenticationError, match=r"^Response to GET /data/nodes from .* was signed by"
    ):
        impostor.exchange([PeerRequest("GET", "/data/nodes")])

    assert impostor.closed and impostor.closed_locally


# -- Steps 3 to 7: what the exchange swaps ---------------------------------


def test_first_contact_swaps_node_lists(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    write_lists(
        config.storage,
        {"http://localhost:8080": str(identity.node_id), "http://192.0.2.1:8080": str(OTHER_ID)},
        [],
    )
    peer.write_lists(
        {
            "http://localhost:4300": str(peer.identity.node_id),
            "http://198.51.100.7:8080": str(OTHER_ID).upper(),
            "http://localhost:9999": str(THIRD_ID),
            "http://203.0.113.5:8080": str(identity.node_id),
            "ftp://198.51.100.8": str(THIRD_ID),
            "http://198.51.100.9:8080": "nonsense",
        }
    )

    exchange.first_contact(session)

    (learned,) = published(queues, EventType.NODES_RECEIVED)
    # `localhost` is resolved to the address the socket reached, and the
    # peer's own entry is kept as one it was observed at.
    assert learned["nodes"] == {
        "http://127.0.0.1:4300": str(peer.identity.node_id),
        "http://198.51.100.7:8080": str(OTHER_ID),
        "http://127.0.0.1:9999": str(THIRD_ID),
    }
    assert learned["sources"] == {"http://127.0.0.1:4300": "observed"}
    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {
        "http://127.0.0.1:8080": str(identity.node_id),
        "http://192.0.2.1:8080": str(OTHER_ID),
    }
    assert told["sources"] == {"http://127.0.0.1:8080": "observed"}


def test_a_node_list_that_cannot_be_read_names_only_this_node_and_is_logged(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    # A directory in its place makes reading it fail with something other than its absence.
    config.storage.node_list_path.mkdir(parents=True)

    exchange.first_contact(session)

    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert set(told["nodes"].values()) == {str(identity.node_id)}
    (record,) = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Cannot read the node list")
    ]
    assert record.levelno == WARNING


def test_a_peers_advertised_entries_are_kept_as_its_own(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    queues: ModuleQueues,
) -> None:
    peer.write_lists({"http://peer.example.org:4300": str(peer.identity.node_id)})

    exchange.first_contact(session)

    (learned,) = published(queues, EventType.NODES_RECEIVED)
    assert learned["nodes"] == {"http://peer.example.org:4300": str(peer.identity.node_id)}
    assert learned["sources"] == {"http://peer.example.org:4300": "advertised"}


def test_a_node_list_naming_no_usable_node_publishes_nothing(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    # Every entry is dropped: this node, and an endpoint that cannot be dialed.
    peer.write_lists(
        {
            "http://203.0.113.5:8080": str(identity.node_id),
            "ftp://198.51.100.8": str(OTHER_ID),
        }
    )

    exchange.first_contact(session)

    assert published(queues, EventType.NODES_RECEIVED) == []


def test_before_the_first_derivation_this_node_sends_itself_alone(
    exchange: PeerExchange, session: PeerSession, peer: FixturePeer, identity: NodeIdentity
) -> None:
    exchange.first_contact(session)

    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {"http://127.0.0.1:8080": str(identity.node_id)}


def test_before_the_first_derivation_this_node_sends_every_endpoint_it_has(
    config: LibranetConfig,
    identity: NodeIdentity,
    peer: FixturePeer,
    queues: ModuleQueues,
) -> None:
    forwarded = config.model_copy(update={"network": NetworkConfig(external_port=4300)})
    exchange = PeerExchange(
        identity, forwarded, StubModule(ModuleName.CONNECTIONS, queues).publish, LOGGER
    )
    session = exchange.open(peer.endpoint)

    try:
        exchange.first_contact(session)

    finally:
        session.close()

    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {
        "http://127.0.0.1:4300": str(identity.node_id),
        "http://127.0.0.1:8080": str(identity.node_id),
    }


def test_lists_a_peer_has_not_derived_yet_are_skipped(
    exchange: PeerExchange, session: PeerSession, queues: ModuleQueues
) -> None:
    exchange.first_contact(session)

    assert published(queues, EventType.NODES_RECEIVED) == []
    assert published(queues, EventType.DATA_SENT) == []


# -- Passing on the nodes newly listed (§3.3, Phase 5 Step 114) ------------


def test_the_nodes_newly_listed_are_those_named_at_an_endpoint_not_named_before(
    exchange: PeerExchange, config: LibranetConfig, identity: NodeIdentity
) -> None:
    own = {"http://localhost:8080": str(identity.node_id)}
    write_lists(config.storage, {**own, "http://192.0.2.1:8080": str(OTHER_ID)}, [])

    assert exchange.newly_listed() == {"http://192.0.2.1:8080": OTHER_ID}
    assert exchange.newly_listed() == {}

    # One node moves, and another is named where it was.
    write_lists(
        config.storage,
        {**own, "http://192.0.2.2:8080": str(OTHER_ID), "http://192.0.2.1:8080": str(THIRD_ID)},
        [],
    )

    assert exchange.newly_listed() == {
        "http://192.0.2.2:8080": OTHER_ID,
        "http://192.0.2.1:8080": THIRD_ID,
    }


def test_the_node_list_as_it_was_when_the_exchange_was_made_names_nothing_newly(
    config: LibranetConfig, identity: NodeIdentity, queues: ModuleQueues
) -> None:
    write_lists(config.storage, {"http://192.0.2.1:8080": str(OTHER_ID)}, [])
    exchange = PeerExchange(
        identity, config, StubModule(ModuleName.CONNECTIONS, queues).publish, LOGGER
    )

    assert exchange.newly_listed() == {}


def test_a_node_list_that_is_not_one_names_nothing_newly_and_is_logged(
    exchange: PeerExchange, config: LibranetConfig, caplog: LogCaptureFixture
) -> None:
    config.storage.derived_dir.mkdir(parents=True)
    config.storage.node_list_path.write_bytes(b"not a list")

    assert exchange.newly_listed() == {}
    (record,) = [
        record
        for record in caplog.records
        if record.getMessage().startswith("Cannot read the node list, so it names no node")
    ]
    assert record.levelno == WARNING


def test_pass_on_posts_the_nodes_with_this_nodes_own_entries(
    exchange: PeerExchange, session: PeerSession, peer: FixturePeer, identity: NodeIdentity
) -> None:
    exchange.pass_on(session, {"http://192.0.2.1:8080": OTHER_ID})

    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {
        "http://127.0.0.1:8080": str(identity.node_id),
        "http://192.0.2.1:8080": str(OTHER_ID),
    }
    assert told["sources"] == {"http://127.0.0.1:8080": "observed"}


def test_pass_on_tells_a_peer_nothing_of_itself(
    exchange: PeerExchange, session: PeerSession, peer: FixturePeer, identity: NodeIdentity
) -> None:
    exchange.pass_on(session, {"http://192.0.2.1:8080": peer.identity.node_id})

    assert peer.published(EventType.NODES_RECEIVED) == []

    exchange.pass_on(
        session,
        {"http://192.0.2.1:8080": peer.identity.node_id, "http://192.0.2.2:8080": OTHER_ID},
    )

    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {
        "http://127.0.0.1:8080": str(identity.node_id),
        "http://192.0.2.2:8080": str(OTHER_ID),
    }


def test_an_unusable_list_is_ignored(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG)
    peer.storage.derived_dir.mkdir(parents=True)
    peer.storage.node_list_path.write_bytes(b"not a list")

    exchange.first_contact(session)

    (record,) = [
        record
        for record in caplog.records
        if record.getMessage().startswith(f"Ignoring a list from {session.endpoint}")
    ]
    assert "(GET /data/nodes)" in record.getMessage()
    # A list a peer sent, refused, is debug.
    assert record.levelno == DEBUG
    assert published(queues, EventType.NODES_RECEIVED) == []


def test_a_peer_that_serves_no_karma_tells_of_no_head(
    exchange: PeerExchange, session: PeerSession, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    exchange.ask_head(session)

    assert published(queues, EventType.KARMA_HEAD_TOLD) == []
    assert f"{session.endpoint} told no Karma head (status 404)" in caplog.text


def asked_head(exchange: PeerExchange, queues: ModuleQueues, answer: bytes) -> list[Message]:
    """What asking a peer that answers ``answer`` for its Karma head publishes."""
    answering = new_identity()
    peer = RawPeer(
        [
            signed(answering, 201),  # this node's key
            signed(answering, 200, answering.public_key),  # its key
            signed(answering, 200, answer),
        ]
    )
    session = exchange.open(peer.endpoint)
    drain(queues, [])

    try:
        exchange.ask_head(session)

    finally:
        session.close()
        peer.join()

    return [payload(message) for message in published(queues, EventType.KARMA_HEAD_TOLD)]


def test_the_head_a_peer_follows_is_published(exchange: PeerExchange, queues: ModuleQueues) -> None:
    (told,) = asked_head(exchange, queues, dumps({"head": str(OTHER_ID)}).encode())

    assert told["head"] == str(OTHER_ID)
    assert told["node_id"] != str(OTHER_ID)


def test_a_peer_following_no_chain_tells_of_no_head(
    exchange: PeerExchange, queues: ModuleQueues
) -> None:
    assert asked_head(exchange, queues, b'{"head": null}') == []


def test_a_head_that_is_not_one_is_ignored(
    exchange: PeerExchange, queues: ModuleQueues, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    assert asked_head(exchange, queues, b'{"head": "not an id"}') == []
    assert "Ignoring a Karma head from" in caplog.text


def test_first_contact_pushes_what_the_peer_seeks(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    peer.write_lists({}, [NOWHERE_ID, HELD_ID])

    exchange.first_contact(session)

    (sent,) = published(queues, EventType.DATA_SENT)
    assert payload(sent) == {
        "event": EventType.DATA_SENT,
        **content_fields(HELD_ID, peer.identity.node_id),
        "size": len(HELD),
    }
    (pushed,) = peer.published(EventType.PUT_COMPLETED)
    assert (pushed["hash"], pushed["node_id"]) == (HELD_ID.hash, str(identity.node_id))
    assert CasStore.for_node(peer.storage, identity.node_id).read(HELD_ID) == HELD
    assert session.pushed == {HELD_ID}


def test_content_that_cannot_be_read_is_a_warning_and_not_pushed(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    # A directory where the content should be cannot be read as a file.
    CasStore.source_of_truth(config.storage).path_for(HELD_ID).mkdir(parents=True)
    peer.write_lists({}, [HELD_ID])

    with caplog.at_level(WARNING, logger=LOGGER.name):
        exchange.first_contact(session)

    (record,) = [record for record in caplog.records if record.name == LOGGER.name]
    assert record.getMessage().startswith(f"Cannot read {HELD_ID}, so it is not pushed: ")
    assert published(queues, EventType.DATA_SENT) == []
    assert peer.published(EventType.PUT_COMPLETED) == []
    # Not noted as pushed, so it is tried again once it can be read.
    assert session.pushed == set()


def test_a_push_the_peer_refuses_is_not_counted_as_sent_or_tried_again(
    exchange: PeerExchange, config: LibranetConfig, queues: ModuleQueues
) -> None:
    refusing = new_identity()
    refusing.publish_public_key(CasStore.source_of_truth(config.storage))
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    seek = dumps({"data": [str(HELD_ID)], "search": []}).encode()
    peer = RawPeer(
        [
            # Answered in the order first contact asks.
            signed(refusing, 201),  # this node's key
            signed(refusing, 202),  # this node's node list
            signed(refusing, 200, seek),  # its seek list: it seeks what this node holds
            signed(refusing, 503),  # its node list: none yet
            signed(refusing, 413),  # the push: but it refuses it
        ]
    )
    session = exchange.open(peer.endpoint)

    try:
        exchange.first_contact(session)

    finally:
        session.close()
        peer.join()

    assert published(queues, EventType.DATA_SENT) == []
    assert session.pushed == {HELD_ID}


def test_first_contact_asks_for_what_this_node_seeks(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    peer.store.write(OFFERED_ID, OFFERED)
    write_lists(config.storage, None, [OFFERED_ID, NOWHERE_ID, HELD_ID])

    exchange.first_contact(session)

    messages = drain(queues, [])
    peer_id = peer.identity.node_id
    assert [payload(message) for message in messages] == [
        {"event": EventType.PUT_COMPLETED, **content_fields(OFFERED_ID, peer_id)},
        {"event": EventType.FETCH_ATTEMPTED, **content_fields(OFFERED_ID, peer_id), "found": True},
        {"event": EventType.FETCH_ATTEMPTED, **content_fields(NOWHERE_ID, peer_id), "found": False},
    ]
    assert CasStore.for_node(config.storage, peer_id).read(OFFERED_ID) == OFFERED


def test_archive_content_is_pushed_and_never_asked_for(
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "held.zip"

    with ArchiveSink.create(archive) as sink:
        sink.write(HELD_ID, HELD)
        sink.write(OFFERED_ID, OFFERED)

    storage = config.storage.model_copy(update={"archives": (archive,)})
    exchange = PeerExchange(
        identity,
        config.model_copy(update={"storage": storage}),
        StubModule(ModuleName.CONNECTIONS, queues).publish,
        LOGGER,
    )
    peer.store.write(OFFERED_ID, OFFERED)
    peer.write_lists({}, [HELD_ID])
    write_lists(storage, None, [OFFERED_ID, NOWHERE_ID])
    session = exchange.open(peer.endpoint)

    try:
        exchange.first_contact(session)

    finally:
        session.close()

    peer_id = peer.identity.node_id
    exchanged = {EventType.DATA_SENT, EventType.FETCH_ATTEMPTED, EventType.PUT_COMPLETED}
    assert [payload(message) for message in drain(queues, []) if message["event"] in exchanged] == [
        {"event": EventType.DATA_SENT, **content_fields(HELD_ID, peer_id), "size": len(HELD)},
        {"event": EventType.FETCH_ATTEMPTED, **content_fields(NOWHERE_ID, peer_id), "found": False},
    ]


def test_content_this_node_has_shunned_is_not_pushed_even_from_an_archive(
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "held.zip"

    with ArchiveSink.create(archive) as sink:
        sink.write(HELD_ID, HELD)

    storage = config.storage.model_copy(update={"archives": (archive,)})
    shunned_list(storage.shunned_list_path, HELD_ID)
    exchange = PeerExchange(
        identity,
        config.model_copy(update={"storage": storage}),
        StubModule(ModuleName.CONNECTIONS, queues).publish,
        LOGGER,
    )
    peer.write_lists({}, [HELD_ID])
    session = exchange.open(peer.endpoint)

    try:
        exchange.first_contact(session)

    finally:
        session.close()

    assert published(queues, EventType.DATA_SENT) == []
    assert peer.published(EventType.PUT_COMPLETED) == []
    assert session.pushed == set()


def test_many_sought_items_are_asked_for_in_pipelined_runs(
    exchange: PeerExchange,
    session: PeerSession,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    depth = config.peers.pipeline_depth
    sought = [ContentId.for_data(str(n).encode(), "sha256") for n in range(depth * 2 + 1)]
    write_lists(config.storage, None, sought)

    exchange.first_contact(session)

    attempts = published(queues, EventType.FETCH_ATTEMPTED)
    assert [message["hash"] for message in attempts] == [content_id.hash for content_id in sought]


def test_refresh_pushes_newly_sought_content_once(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    peer.write_lists({}, [])
    exchange.first_contact(session)
    assert published(queues, EventType.DATA_SENT) == []

    peer.write_lists({}, [HELD_ID])
    exchange.refresh(session)
    exchange.refresh(session)

    (sent,) = published(queues, EventType.DATA_SENT)
    assert sent["hash"] == HELD_ID.hash
    assert len(peer.published(EventType.PUT_COMPLETED)) == 1


# -- Searches: asking a peer, and answering a peer's (Phase 4 Step 93) ------

TARGET = DropTarget.of("a drop a peer searches for")


def held_drop(config: LibranetConfig, content: bytes) -> ContentId:
    """``content`` held here as a drop at :data:`TARGET`, matching it by 16 bits or more."""
    drop = TARGET.placed(content, 0, minimum_bits=16)
    content_id = ContentId.for_data(drop.data, "sha256")
    CasStore.source_of_truth(config.storage).write(content_id, drop.data)
    return content_id


def test_search_asks_the_peer_and_publishes_what_it_answers(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    peer.store.write(OFFERED_ID, OFFERED)

    found = exchange.search(session, OFFERED_ID.hash)

    # It holds its own key, and this node's, sent as the session opened.
    assert found[0] == OFFERED_ID
    assert set(found) == {OFFERED_ID, peer.identity.node_id, identity.node_id}
    (answered,) = published(queues, EventType.SEARCH_ANSWERED)
    assert payload(answered) == {
        "event": EventType.SEARCH_ANSWERED,
        "prefix": OFFERED_ID.hash,
        "node_id": str(peer.identity.node_id),
        "results": [str(content_id) for content_id in found],
    }
    (asked,) = peer.published(EventType.SEARCH_REQUESTED)
    assert (asked["prefix"], asked["node_id"]) == (OFFERED_ID.hash, str(identity.node_id))


def test_a_peer_that_answers_a_search_unusably_teaches_nothing(
    exchange: PeerExchange, queues: ModuleQueues
) -> None:
    answering = new_identity()
    peer = RawPeer(
        [
            signed(answering, 201),  # this node's key
            signed(answering, 200, answering.public_key),  # its key
            signed(answering, 200, b'{"results": "not an array"}'),
        ]
    )
    session = exchange.open(peer.endpoint)
    drain(queues, [])

    try:
        found = exchange.search(session, NOWHERE_ID.hash)

    finally:
        session.close()
        peer.join()

    assert found == []
    assert published(queues, EventType.SEARCH_ANSWERED) == []


def named_to(peer: FixturePeer) -> list[list[str]]:
    """What ``peer`` has been told matches a search it lists, each time it was told."""
    return [message["results"] for message in peer.published(EventType.SEARCH_ANSWERED)]


def test_first_contact_names_what_closely_matches_a_search_the_peer_seeks_pushing_none(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    drop_id = held_drop(config, b"what the peer is looking for")
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    assert matching_bits(TARGET.hex, HELD_ID.hash) < 16
    peer.write_lists({}, [], [TARGET.hex])

    exchange.first_contact(session)

    (named,) = peer.published(EventType.SEARCH_ANSWERED)
    assert (named["prefix"], named["node_id"]) == (TARGET.hex, str(identity.node_id))
    assert named["results"] == [str(drop_id)]
    assert published(queues, EventType.DATA_SENT) == []
    assert peer.published(EventType.PUT_COMPLETED) == []


def test_what_the_peer_comes_to_list_by_id_is_pushed_as_its_seek_list_is_next_read(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    drop_id = held_drop(config, b"what the peer is looking for")
    other_id = held_drop(config, b"what the peer does not want")
    peer.write_lists({}, [], [TARGET.hex])
    exchange.first_contact(session)
    assert {*named_to(peer)[0]} == {str(drop_id), str(other_id)}

    peer.write_lists({}, [drop_id], [TARGET.hex])
    exchange.refresh(session)

    (sent,) = published(queues, EventType.DATA_SENT)
    assert sent["hash"] == drop_id.hash
    (pushed,) = peer.published(EventType.PUT_COMPLETED)
    assert pushed["hash"] == drop_id.hash


def test_fewer_bits_set_name_content_matching_a_search_less_closely(
    identity: NodeIdentity,
    config: LibranetConfig,
    peer: FixturePeer,
    queues: ModuleQueues,
) -> None:
    storage = config.storage.model_copy(update={"search_min_bits": 0})
    exchange = PeerExchange(
        identity,
        config.model_copy(update={"storage": storage}),
        StubModule(ModuleName.CONNECTIONS, queues).publish,
        LOGGER,
    )
    drop_id = held_drop(config, b"what the peer is looking for")
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    peer.write_lists({}, [], [TARGET.hex])
    session = exchange.open(peer.endpoint)

    try:
        exchange.first_contact(session)

    finally:
        session.close()

    (named,) = named_to(peer)
    assert named[0] == str(drop_id)
    assert str(HELD_ID) in named


def test_a_search_prefix_shorter_than_the_bits_needed_is_matched_whole(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
) -> None:
    CasStore.source_of_truth(config.storage).write(HELD_ID, HELD)
    # Eight bits, all of which it shares.
    prefix = HELD_ID.hash[:2]
    peer.write_lists({}, [], [prefix])

    exchange.first_contact(session)

    (named,) = named_to(peer)
    assert named.count(str(HELD_ID)) == 1
    # Both nodes' keys, made afresh each run, are held too, and either is
    # named as well when it shares the eight bits, about one run in 128.
    assert all(ContentId.parse(each).hash.startswith(prefix) for each in named)


def test_what_a_search_matches_is_not_named_once_pushed(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    drop_id = held_drop(config, b"sought twice over")
    peer.write_lists({}, [drop_id], [TARGET.hex])

    exchange.first_contact(session)

    assert [sent["hash"] for sent in published(queues, EventType.DATA_SENT)] == [drop_id.hash]
    assert named_to(peer) == []


def test_what_this_node_shuns_is_not_named(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
) -> None:
    kept_id = held_drop(config, b"kept")
    shunned_id = held_drop(config, b"shunned")
    shunned_list(config.storage.shunned_list_path, shunned_id)
    peer.write_lists({}, [], [TARGET.hex])

    exchange.first_contact(session)

    assert named_to(peer) == [[str(kept_id)]]


def test_what_a_search_matches_is_named_once_while_the_peer_lists_it_and_what_comes_later(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
) -> None:
    first = held_drop(config, b"held when first asked")
    peer.write_lists({}, [], [TARGET.hex])
    exchange.first_contact(session)
    later = held_drop(config, b"held only later")

    exchange.refresh(session)
    exchange.refresh(session)

    assert named_to(peer) == [[str(first)], [str(later)]]

    peer.write_lists({}, [], [])
    exchange.refresh(session)
    peer.write_lists({}, [], [TARGET.hex])
    exchange.refresh(session)

    # Listed anew, all of it is named anew.
    assert {*named_to(peer)[2]} == {str(first), str(later)}


def test_a_peer_refusing_what_is_named_to_it_is_logged_at_debug(
    exchange: PeerExchange,
    config: LibranetConfig,
    caplog: LogCaptureFixture,
) -> None:
    refusing = new_identity()
    refusing.publish_public_key(CasStore.source_of_truth(config.storage))
    held_drop(config, b"what it will not hear of")
    seek = dumps({"data": [], "search": [TARGET.hex]}).encode()
    peer = RawPeer(
        [
            # Answered in the order first contact asks.
            signed(refusing, 201),  # this node's key
            signed(refusing, 202),  # this node's node list
            signed(refusing, 200, seek),  # its seek list: it searches for the drop
            signed(refusing, 503),  # its node list: none yet
            signed(refusing, 400),  # what is named: but it refuses it
        ]
    )
    session = exchange.open(peer.endpoint)

    try:
        with caplog.at_level(DEBUG):
            exchange.first_contact(session)

    finally:
        session.close()
        peer.join()

    assert "took no ids" in caplog.text


def test_retrieve_hands_what_the_peer_sends_to_the_validator(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    compressed = compress(OFFERED)
    peer.store.write(OFFERED_ID, compressed)

    assert exchange.retrieve(session, OFFERED_ID) == Retrieval(found=True)

    # Kept exactly as received, compressed or not, as uploads are.
    assert CasStore.for_node(config.storage, peer.identity.node_id).read(OFFERED_ID) == compressed
    assert [message["event"] for message in drain(queues, [])] == [
        EventType.PUT_COMPLETED,
        EventType.FETCH_ATTEMPTED,
    ]


def test_retrieve_reports_content_the_peer_lacks_and_when_to_ask_again(
    exchange: PeerExchange, session: PeerSession, queues: ModuleQueues
) -> None:
    assert exchange.retrieve(session, NOWHERE_ID) == Retrieval(
        found=False, retry_after_seconds=RETRY_AFTER
    )

    (attempt,) = drain(queues, [])
    assert (attempt["event"], attempt["found"]) == (EventType.FETCH_ATTEMPTED, False)


def test_retrieve_refuses_content_that_is_not_what_was_asked_for(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG)
    peer.store.write(OFFERED_ID, b"something else")

    assert exchange.retrieve(session, OFFERED_ID) == Retrieval(found=False)

    (record,) = [
        record
        for record in caplog.records
        if record.getMessage().endswith(f"sent content that is not {OFFERED_ID}")
    ]
    assert record.levelno == DEBUG
    assert not CasStore.for_node(config.storage, peer.identity.node_id).exists(OFFERED_ID)
    (attempt,) = drain(queues, [])
    assert attempt["found"] is False


# -- Handing content off ---------------------------------------------------


def test_hand_off_pushes_content_the_peer_did_not_ask_for(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    assert exchange.hand_off(session, HELD_ID, HELD)

    (sent,) = published(queues, EventType.DATA_SENT)
    assert payload(sent) == {
        "event": EventType.DATA_SENT,
        **content_fields(HELD_ID, peer.identity.node_id),
        "size": len(HELD),
    }
    assert CasStore.for_node(peer.storage, identity.node_id).read(HELD_ID) == HELD
    assert session.pushed == {HELD_ID}


def test_a_peer_that_already_holds_what_is_handed_off_accepts_it(
    exchange: PeerExchange, session: PeerSession, peer: FixturePeer, queues: ModuleQueues
) -> None:
    peer.store.write(HELD_ID, HELD)

    assert exchange.hand_off(session, HELD_ID, HELD)

    assert peer.published(EventType.PUT_COMPLETED) == []
    assert len(published(queues, EventType.DATA_SENT)) == 1


def test_a_hand_off_the_peer_refuses_is_not_accepted(
    exchange: PeerExchange, config: LibranetConfig, queues: ModuleQueues
) -> None:
    refusing = new_identity()
    refusing.publish_public_key(CasStore.source_of_truth(config.storage))
    peer = RawPeer([signed(refusing, 201), signed(refusing, 507)])
    session = exchange.open(peer.endpoint)

    try:
        assert not exchange.hand_off(session, HELD_ID, HELD)

    finally:
        session.close()
        peer.join()

    assert published(queues, EventType.DATA_SENT) == []


def test_hand_off_many_says_which_of_them_the_peer_accepted(
    exchange: PeerExchange, config: LibranetConfig, queues: ModuleQueues
) -> None:
    choosy = new_identity()
    choosy.publish_public_key(CasStore.source_of_truth(config.storage))
    peer = RawPeer(
        [
            signed(choosy, 201),  # this node's key
            signed(choosy, 202),  # the first pushed
            signed(choosy, 507),  # the second, refused
            signed(choosy, 202),  # the third
        ]
    )
    held = [(HELD_ID, HELD), (OFFERED_ID, OFFERED), (NOWHERE_ID, b"content nobody holds")]
    session = exchange.open(peer.endpoint)

    try:
        accepted = exchange.hand_off_many(session, held)

    finally:
        session.close()
        peer.join()

    assert accepted == [True, False, True]
    sent = published(queues, EventType.DATA_SENT)
    assert [message["hash"] for message in sent] == [HELD_ID.hash, NOWHERE_ID.hash]
    assert session.pushed == {HELD_ID, OFFERED_ID, NOWHERE_ID}


def own_seek_list_records(caplog: LogCaptureFixture) -> list[LogRecord]:
    return [
        record
        for record in caplog.records
        if record.getMessage().startswith("Cannot read this node's own seek list: ")
    ]


def test_first_contact_before_a_seek_list_of_its_own_is_derived_logs_nothing_of_it(
    exchange: PeerExchange, session: PeerSession, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    exchange.first_contact(session)

    assert own_seek_list_records(caplog) == []


def test_first_contact_with_an_unreadable_seek_list_of_its_own_logs_a_warning(
    exchange: PeerExchange, session: PeerSession, config: LibranetConfig, caplog: LogCaptureFixture
) -> None:
    config.storage.derived_dir.mkdir(parents=True, exist_ok=True)
    config.storage.seek_list_path.write_bytes(b"not json")

    exchange.first_contact(session)

    (record,) = own_seek_list_records(caplog)
    assert record.levelno == WARNING
