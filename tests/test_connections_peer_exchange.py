"""Tests of what this node says to a peer, against live fixture peers.

Most fixture peers are this node's own web server (Steps 5, 7, 9) over a temp
CAS, with an identity of their own. What the client learns shows in what it
publishes; what the peer learns shows in what the peer's web server
publishes. Peers that misbehave are raw sockets answering with canned bytes.
"""

from __future__ import annotations
from json import dumps
from logging import getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import create_server, socket
from threading import Thread
from typing import Iterator, Mapping, Sequence
from zlib import compress

from pytest import LogCaptureFixture, fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.store import node_store, source_of_truth_store
from libranet.config.models import IdentityConfig, LibranetConfig, PeerConfig, StorageConfig
from libranet.connections.errors import ConnectionClosedError, PeerAuthenticationError
from libranet.connections.peer_connection import open_connection
from libranet.connections.peer_exchange import PIPELINE_DEPTH, PeerExchange
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.identity.authentication import request_authenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity, load_node_identity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule
from libranet.webserver.server import LibranetHTTPServer, build_router

LOGGER = getLogger("test.connections")
TIMEOUT = 5.0

HELD = b"content the client holds"
HELD_ID = ContentId.for_data(HELD, "sha256")
OFFERED = b"content the peer holds"
OFFERED_ID = ContentId.for_data(OFFERED, "sha256")
NOWHERE_ID = ContentId.for_data(b"content nobody holds", "sha256")
OTHER_ID = ContentId.for_data(b"some other node's key", "sha256")
THIRD_ID = ContentId.for_data(b"a third node's key", "sha256")


def new_identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


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
        self.storage = StorageConfig(data_dir=root / "data", cache_dir=root / "cache")
        self.store = source_of_truth_store(self.storage)
        self.queues = ModuleQueues(inbox=Queue(), outbox=Queue())
        self._seen: list[Message] = []
        self.server = LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_router(
                self.storage,
                7,
                StubModule(ModuleName.WEBSERVER, self.queues).publish,
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
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def write_lists(
        self, nodes: Mapping[str, str] | None = None, sought: Sequence[ContentId] = ()
    ) -> None:
        write_lists(self.storage, nodes, sought)

    def published(self, event: EventType) -> list[Message]:
        return [message for message in drain(self.queues, self._seen) if message["event"] == event]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self._thread.join()


def write_lists(
    storage: StorageConfig, nodes: Mapping[str, str] | None, sought: Sequence[ContentId]
) -> None:
    """Derived node and seek lists, as the stats module would write them."""
    storage.derived_dir.mkdir(parents=True, exist_ok=True)

    if nodes is not None:
        storage.node_list_path.write_text(dumps({"nodes": nodes}))

    storage.seek_list_path.write_text(dumps({"data": [str(c) for c in sought], "search": []}))


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
    return LibranetConfig(
        storage=StorageConfig(data_dir=root / "data", cache_dir=root / "cache"),
        identity=IdentityConfig(key_dir=root / "keys"),
        peers=PeerConfig(connect_timeout_seconds=TIMEOUT, request_timeout_seconds=TIMEOUT),
    )


@fixture
def identity(config: LibranetConfig) -> NodeIdentity:
    return load_node_identity(config)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def exchange(identity: NodeIdentity, config: LibranetConfig, queues: ModuleQueues) -> PeerExchange:
    return PeerExchange(
        identity, config, StubModule(ModuleName.CONNECTIONS, queues).publish, LOGGER
    )


@fixture
def session(exchange: PeerExchange, peer: FixturePeer) -> Iterator[PeerSession]:
    session = exchange.open(peer.endpoint)
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
) -> None:
    session = exchange.open(peer.endpoint)

    try:
        assert session.node_id == peer.identity.node_id
        assert session.endpoint == peer.endpoint
        assert not session.closed
        client_store = source_of_truth_store(config.storage)
        assert client_store.read(peer.identity.node_id) == peer.identity.public_key
        # The peer holds this node's key at once, so it could verify from then on.
        assert peer.store.read(identity.node_id) == identity.public_key
        (asked,) = peer.published(EventType.DATA_REQUESTED)
        assert asked["hash"] == peer.identity.node_id.hash

    finally:
        session.close()


def test_a_key_already_held_is_not_asked_for(
    exchange: PeerExchange, peer: FixturePeer, config: LibranetConfig
) -> None:
    peer.identity.publish_public_key(source_of_truth_store(config.storage))

    opened = exchange.open(peer.endpoint)
    opened.close()

    assert opened.node_id == peer.identity.node_id
    assert peer.published(EventType.DATA_REQUESTED) == []


def test_a_compressed_public_key_is_held_as_sent(
    exchange: PeerExchange, tmp_path: Path, config: LibranetConfig
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
    assert source_of_truth_store(config.storage).read(peer.identity.node_id) == compressed


def test_a_peer_that_does_not_send_its_key_is_refused(
    exchange: PeerExchange, tmp_path: Path, config: LibranetConfig
) -> None:
    peer = FixturePeer(tmp_path / "keyless")

    try:
        with raises(PeerAuthenticationError, match="did not send its public key"):
            exchange.open(peer.endpoint)

    finally:
        peer.stop()

    assert not source_of_truth_store(config.storage).exists(peer.identity.node_id)


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
    actual.publish_public_key(source_of_truth_store(config.storage))
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
    assert not source_of_truth_store(config.storage).exists(content_id)


def test_a_peer_that_closes_after_identifying_itself_is_refused(
    exchange: PeerExchange, config: LibranetConfig
) -> None:
    closing = new_identity()
    closing.publish_public_key(source_of_truth_store(config.storage))
    peer = RawPeer([signed(closing, 401, headers={"Connection": "close"})])

    with raises(ConnectionClosedError):
        exchange.open(peer.endpoint)

    peer.join()


def test_a_response_the_peer_did_not_prove_closes_the_session(
    peer: FixturePeer, config: LibranetConfig, identity: NodeIdentity
) -> None:
    client_store = source_of_truth_store(config.storage)
    verifier = MessageVerifier(client_store, 5.0, 30.0)

    def session_expecting(node_id: ContentId) -> PeerSession:
        connection = open_connection(
            "127.0.0.1",
            peer.server.server_address[1],
            MessageSigner(identity),
            LOGGER,
            connect_timeout=TIMEOUT,
            request_timeout=TIMEOUT,
            max_body_bytes=1 << 20,
        )
        return PeerSession(connection, peer.endpoint, node_id, verifier)

    unverifiable = session_expecting(peer.identity.node_id)

    with raises(PeerAuthenticationError, match="failed verification"):
        unverifiable.exchange([PeerRequest("GET", "/data/nodes")])

    assert unverifiable.closed and unverifiable.closed_locally

    peer.identity.publish_public_key(client_store)
    impostor = session_expecting(OTHER_ID)

    with raises(PeerAuthenticationError, match="was signed by"):
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
    assert learned["nodes"] == {
        "http://198.51.100.7:8080": str(OTHER_ID),
        "http://127.0.0.1:9999": str(THIRD_ID),
    }
    (told,) = peer.published(EventType.NODES_RECEIVED)
    assert told["nodes"] == {
        "http://127.0.0.1:8080": str(identity.node_id),
        "http://192.0.2.1:8080": str(OTHER_ID),
    }


def test_a_node_list_naming_no_other_peer_publishes_nothing(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    # Every entry is dropped: the peer itself, this node, and an endpoint
    # that cannot be dialed.
    peer.write_lists(
        {
            "http://localhost:4300": str(peer.identity.node_id),
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


def test_lists_a_peer_has_not_derived_yet_are_skipped(
    exchange: PeerExchange, session: PeerSession, queues: ModuleQueues
) -> None:
    exchange.first_contact(session)

    assert published(queues, EventType.NODES_RECEIVED) == []
    assert published(queues, EventType.DATA_SENT) == []


def test_an_unusable_list_is_ignored(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    peer.storage.derived_dir.mkdir(parents=True)
    peer.storage.node_list_path.write_bytes(b"not a list")

    exchange.first_contact(session)

    assert "Ignoring a list from" in caplog.text
    assert published(queues, EventType.NODES_RECEIVED) == []


def test_first_contact_pushes_what_the_peer_seeks(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    identity: NodeIdentity,
    queues: ModuleQueues,
) -> None:
    source_of_truth_store(config.storage).write(HELD_ID, HELD)
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
    assert node_store(peer.storage, identity.node_id).read(HELD_ID) == HELD
    assert session.pushed == {HELD_ID}


def test_a_push_the_peer_refuses_is_not_counted_as_sent_or_tried_again(
    exchange: PeerExchange, config: LibranetConfig, queues: ModuleQueues
) -> None:
    refusing = new_identity()
    refusing.publish_public_key(source_of_truth_store(config.storage))
    source_of_truth_store(config.storage).write(HELD_ID, HELD)
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
    source_of_truth_store(config.storage).write(HELD_ID, HELD)
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
    assert node_store(config.storage, peer_id).read(OFFERED_ID) == OFFERED


def test_many_sought_items_are_asked_for_in_pipelined_runs(
    exchange: PeerExchange,
    session: PeerSession,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    sought = [ContentId.for_data(str(n).encode(), "sha256") for n in range(PIPELINE_DEPTH * 2 + 1)]
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
    source_of_truth_store(config.storage).write(HELD_ID, HELD)
    peer.write_lists({}, [])
    exchange.first_contact(session)
    assert published(queues, EventType.DATA_SENT) == []

    peer.write_lists({}, [HELD_ID])
    exchange.refresh(session)
    exchange.refresh(session)

    (sent,) = published(queues, EventType.DATA_SENT)
    assert sent["hash"] == HELD_ID.hash
    assert len(peer.published(EventType.PUT_COMPLETED)) == 1


def test_retrieve_hands_what_the_peer_sends_to_the_validator(
    exchange: PeerExchange,
    session: PeerSession,
    peer: FixturePeer,
    config: LibranetConfig,
    queues: ModuleQueues,
) -> None:
    compressed = compress(OFFERED)
    peer.store.write(OFFERED_ID, compressed)

    assert exchange.retrieve(session, OFFERED_ID)

    # Kept exactly as received, compressed or not, as uploads are.
    assert node_store(config.storage, peer.identity.node_id).read(OFFERED_ID) == compressed
    assert [message["event"] for message in drain(queues, [])] == [
        EventType.PUT_COMPLETED,
        EventType.FETCH_ATTEMPTED,
    ]


def test_retrieve_reports_content_the_peer_lacks(
    exchange: PeerExchange, session: PeerSession, queues: ModuleQueues
) -> None:
    assert not exchange.retrieve(session, NOWHERE_ID)

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
    peer.store.write(OFFERED_ID, b"something else")

    assert not exchange.retrieve(session, OFFERED_ID)

    assert f"sent content that is not {OFFERED_ID}" in caplog.text
    assert not node_store(config.storage, peer.identity.node_id).exists(OFFERED_ID)
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
    assert node_store(peer.storage, identity.node_id).read(HELD_ID) == HELD
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
    refusing.publish_public_key(source_of_truth_store(config.storage))
    peer = RawPeer([signed(refusing, 201), signed(refusing, 507)])
    session = exchange.open(peer.endpoint)

    try:
        assert not exchange.hand_off(session, HELD_ID, HELD)

    finally:
        session.close()
        peer.join()

    assert published(queues, EventType.DATA_SENT) == []
