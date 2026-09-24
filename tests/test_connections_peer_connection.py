"""Tests of the pipelining peer client.

Most tests drive the client against a fake peer on the other end of a
socket pair, which reads requests and writes raw response bytes exactly
when each test says. The rest point it at a live web server over a temp CAS.
"""

from __future__ import annotations
from logging import getLogger
from pathlib import Path
from queue import Queue
from socket import AF_INET, AF_INET6, SHUT_WR, create_server, socket, socketpair
from threading import Event, Thread
from time import monotonic, sleep
from typing import Iterator, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises, skip

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.connections.errors import ConnectionClosedError, MalformedResponseError
from libranet.connections.peer_connection import PeerConnection, open_connection
from libranet.identity.authentication import request_authenticator
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.stubs import StubModule
from libranet.webserver.config_credential import load_config_credential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.server import REQUEST_PATH_HEADER, LibranetHTTPServer, build_router

CLIENT_IDENTITY = NodeIdentity.from_private_key(generate_private_key(), "sha256")
CLIENT_SIGNER = MessageSigner(CLIENT_IDENTITY)
SERVER_IDENTITY = NodeIdentity.from_private_key(generate_private_key(), "sha256")
LOGGER = getLogger("test.connections")
MAX_BODY_BYTES = 1 << 20
TIMEOUT = 5.0

CONTENT = b"hello libranet"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
MISSING_ID = ContentId.for_data(b"not stored", "sha256")


def _connection(sock: socket, request_timeout: float = TIMEOUT) -> PeerConnection:
    return PeerConnection(
        sock,
        "peer.test:8080",
        CLIENT_SIGNER,
        LOGGER,
        request_timeout=request_timeout,
        max_body_bytes=MAX_BODY_BYTES,
    )


@fixture
def pair() -> Iterator[tuple[PeerConnection, socket]]:
    """A client connection, and the fake peer's end of it."""
    client, peer = socketpair()
    peer.settimeout(TIMEOUT)
    connection = _connection(client)
    yield connection, peer
    connection.close()
    peer.close()


def _read_heads(peer: socket, count: int) -> list[bytes]:
    """The heads of the next ``count`` requests, none of which has a body."""
    data = b""

    while data.count(b"\r\n\r\n") < count:
        received = peer.recv(65536)
        assert received, "the client closed the connection"
        data += received

    return data.split(b"\r\n\r\n")[:count]


def _read_requests(peer: socket, count: int) -> list[str]:
    """The request lines of the next ``count`` requests, none of which has a body."""
    return [head.split(b"\r\n", 1)[0].decode() for head in _read_heads(peer, count)]


def _response(status: int, body: bytes = b"", headers: Mapping[str, str] | None = None) -> bytes:
    fields = {"Content-Length": str(len(body)), **(headers or {})}
    head = "".join(f"{name}: {value}\r\n" for name, value in fields.items())
    return f"HTTP/1.1 {status} Reason\r\n{head}\r\n".encode() + body


def _wait_until_closed(connection: PeerConnection) -> None:
    deadline = monotonic() + TIMEOUT

    while not connection.closed:
        assert monotonic() < deadline, "the connection stayed open"
        sleep(0.01)


def test_requests_are_all_sent_before_any_response_arrives(
    pair: tuple[PeerConnection, socket], caplog: LogCaptureFixture
) -> None:
    connection, peer = pair
    targets = ["/data/one", "/data/two?x=1", "/data/three"]

    futures = [connection.request("GET", target) for target in targets]

    assert _read_requests(peer, 3) == [f"GET {target} HTTP/1.1" for target in targets]
    assert not any(future.done() for future in futures)

    peer.sendall(
        b"".join(
            _response(200, target.encode(), {REQUEST_PATH_HEADER: target}) for target in targets
        )
    )

    assert [future.result(TIMEOUT).body for future in futures] == [t.encode() for t in targets]
    assert caplog.text == ""


def test_waiting_requests_cannot_be_cancelled(pair: tuple[PeerConnection, socket]) -> None:
    connection, _ = pair

    assert not connection.request("GET", "/").cancel()


def test_response_to_head_has_no_body(pair: tuple[PeerConnection, socket]) -> None:
    connection, peer = pair
    head = connection.request("HEAD", "/a")
    get = connection.request("GET", "/b")
    _read_requests(peer, 2)

    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\n" + _response(200, b"hello"))

    assert head.result(TIMEOUT).body == b""
    assert get.result(TIMEOUT).body == b"hello"


def test_mismatched_request_path_echo_is_logged(
    pair: tuple[PeerConnection, socket], caplog: LogCaptureFixture
) -> None:
    connection, peer = pair
    future = connection.request("GET", "/a")
    _read_requests(peer, 1)

    peer.sendall(_response(200, b"", {REQUEST_PATH_HEADER: "/b"}))

    assert future.result(TIMEOUT).status == 200
    assert f"GET /a from peer.test:8080 has {REQUEST_PATH_HEADER} /b" in caplog.text


def test_peer_closing_fails_the_requests_it_did_not_answer(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair
    first = connection.request("GET", "/one")
    second = connection.request("GET", "/two")
    _read_requests(peer, 2)

    peer.sendall(_response(200, b"one"))
    peer.close()

    assert first.result(TIMEOUT).body == b"one"

    with raises(ConnectionClosedError):
        second.result(TIMEOUT)

    assert connection.closed

    with raises(ConnectionClosedError):
        connection.request("GET", "/three")


def test_idle_connection_closed_by_the_peer_is_noticed(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair

    peer.close()

    _wait_until_closed(connection)


def test_connection_close_response_fails_the_rest(pair: tuple[PeerConnection, socket]) -> None:
    connection, peer = pair
    first = connection.request("GET", "/one")
    second = connection.request("GET", "/two")
    _read_requests(peer, 2)

    peer.sendall(_response(200, b"bye", {"Connection": "close"}))

    assert first.result(TIMEOUT).closes_connection

    with raises(ConnectionClosedError):
        second.result(TIMEOUT)


def test_body_running_until_close_is_delivered(pair: tuple[PeerConnection, socket]) -> None:
    connection, peer = pair
    future = connection.request("GET", "/")
    _read_requests(peer, 1)

    peer.sendall(b"HTTP/1.1 200 OK\r\n\r\nall of ")
    peer.sendall(b"it")
    peer.close()

    assert future.result(TIMEOUT).body == b"all of it"


def test_close_partway_through_a_response_is_malformed(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair
    future = connection.request("GET", "/")
    _read_requests(peer, 1)

    peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nshort")
    peer.close()

    with raises(MalformedResponseError):
        future.result(TIMEOUT)


def test_malformed_response_fails_every_waiting_request(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair
    first = connection.request("GET", "/one")
    second = connection.request("GET", "/two")
    _read_requests(peer, 2)

    peer.sendall(b"nonsense\r\n\r\n")

    with raises(MalformedResponseError):
        first.result(TIMEOUT)

    with raises(ConnectionClosedError) as caught:
        second.result(TIMEOUT)

    assert isinstance(caught.value.__cause__, MalformedResponseError)
    assert connection.closed


def test_response_nothing_asked_for_closes_the_connection(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair

    peer.sendall(_response(200))

    _wait_until_closed(connection)


def test_silent_peer_times_out() -> None:
    client, peer = socketpair()

    with peer, _connection(client, request_timeout=0.2) as connection:
        future = connection.request("GET", "/")

        with raises(TimeoutError):
            future.result(TIMEOUT)

        assert connection.closed


def test_idle_connection_does_not_time_out() -> None:
    client, peer = socketpair()

    with peer, _connection(client, request_timeout=0.2) as connection:
        sleep(0.5)

        assert not connection.closed

        future = connection.request("GET", "/")
        _read_requests(peer, 1)
        peer.sendall(_response(204))

        assert future.result(TIMEOUT).status == 204


def test_failed_send_fails_the_connection() -> None:
    client, peer = socketpair()
    client.shutdown(SHUT_WR)

    with peer, _connection(client) as connection:
        with raises(ConnectionClosedError, match="Sending"):
            connection.request("GET", "/").result(TIMEOUT)


def test_close_fails_waiting_requests(pair: tuple[PeerConnection, socket]) -> None:
    connection, peer = pair
    future = connection.request("GET", "/")
    _read_requests(peer, 1)

    connection.close()
    connection.close()

    with raises(ConnectionClosedError, match="closed locally"):
        future.result(TIMEOUT)

    with raises(ConnectionClosedError):
        connection.request("GET", "/")


def test_close_from_a_response_callback(pair: tuple[PeerConnection, socket]) -> None:
    connection, peer = pair
    future = connection.request("GET", "/")
    future.add_done_callback(lambda _: connection.close())
    _read_requests(peer, 1)

    peer.sendall(_response(200, b"done"))

    assert future.result(TIMEOUT).body == b"done"
    _wait_until_closed(connection)


def test_close_callbacks_run_once_waiting_requests_have_failed(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, peer = pair
    future = connection.request("GET", "/")
    _read_requests(peer, 1)
    closed = Event()
    seen: list[bool] = []

    def on_closed() -> None:
        seen.append(future.done())
        closed.set()

    connection.when_closed(on_closed)
    assert not closed.is_set()

    peer.close()

    assert closed.wait(TIMEOUT)
    assert seen == [True]
    assert connection.closed


def test_close_callback_added_after_closing_runs_at_once(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, _ = pair
    connection.close()
    calls: list[str] = []

    connection.when_closed(lambda: calls.append("closed"))

    assert calls == ["closed"]


def test_invalid_request_is_refused_before_it_is_queued(
    pair: tuple[PeerConnection, socket],
) -> None:
    connection, _ = pair

    with raises(ValueError):
        connection.request("GET", "no-slash")

    assert not connection.closed


@mark.parametrize(("family", "host"), [(AF_INET, "127.0.0.1"), (AF_INET6, "::1")])
def test_open_connection_names_the_peer_in_host(family: int, host: str) -> None:
    try:
        listener = create_server((host, 0), family=family)

    except OSError:
        skip(f"{host} is not available")

    port = listener.getsockname()[1]
    authority = f"[{host}]" if family == AF_INET6 else host

    with (
        listener,
        open_connection(
            host,
            port,
            CLIENT_SIGNER,
            LOGGER,
            connect_timeout=TIMEOUT,
            request_timeout=TIMEOUT,
            max_body_bytes=MAX_BODY_BYTES,
        ) as connection,
    ):
        connection.request("GET", "/")
        peer, _ = listener.accept()

        with peer:
            peer.settimeout(TIMEOUT)
            (head,) = _read_heads(peer, 1)

    assert connection.host == f"{authority}:{port}"
    assert f"\r\nHost: {authority}:{port}\r\n".encode() in head


def test_open_connection_to_nothing_raises() -> None:
    with create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]

    with raises(OSError):
        open_connection(
            "127.0.0.1",
            port,
            CLIENT_SIGNER,
            LOGGER,
            connect_timeout=TIMEOUT,
            request_timeout=TIMEOUT,
            max_body_bytes=MAX_BODY_BYTES,
        )


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    """A server CAS holding some content, and the client's key from an earlier handshake."""
    storage = StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    store = source_of_truth_store(storage)
    store.write(CONTENT_ID, CONTENT)
    CLIENT_IDENTITY.publish_public_key(store)
    return storage


@fixture
def server(storage: StorageConfig) -> Iterator[LibranetHTTPServer]:
    publisher = StubModule(ModuleName.WEBSERVER, ModuleQueues(inbox=Queue(), outbox=Queue()))
    server = LibranetHTTPServer(
        ("127.0.0.1", 0),
        build_router(
            storage,
            7,
            publisher.publish,
            request_authenticator(LibranetConfig(storage=storage)),
            allow_unsigned_api_reads=True,
            config_credential=load_config_credential(LibranetConfig(storage=storage)),
            node=NodeDescription(SERVER_IDENTITY.node_id, NetworkConfig()),
        ),
        getLogger("test.webserver"),
        MessageSigner(SERVER_IDENTITY),
    )
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


@fixture
def live(server: LibranetHTTPServer) -> Iterator[PeerConnection]:
    host, port = server.server_address[:2]

    with open_connection(
        str(host),
        int(port),
        CLIENT_SIGNER,
        LOGGER,
        connect_timeout=TIMEOUT,
        request_timeout=TIMEOUT,
        max_body_bytes=MAX_BODY_BYTES,
    ) as connection:
        yield connection


def test_pipelined_requests_to_a_live_server_get_their_own_responses(
    live: PeerConnection, tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    upload = b"new content"
    upload_id = ContentId.for_data(upload, "sha256")
    requests = [
        ("GET", f"/data/{CONTENT_ID}", b""),
        ("GET", f"/data/{MISSING_ID}", b""),
        ("PUT", f"/data/{upload_id}", upload),
        ("GET", f"/data/search/{CONTENT_ID.hash[:4]}?limit=1", b""),
        ("GET", "/data/nowhere", b""),
        ("HEAD", f"/data/{CONTENT_ID}", b""),
        ("GET", f"/data/{CONTENT_ID}", b""),
    ]

    futures = [live.request(method, target, body=body) for method, target, body in requests]
    responses = [future.result(TIMEOUT) for future in futures]

    assert [response.headers[REQUEST_PATH_HEADER] for response in responses] == [
        target for _, target, _ in requests
    ]
    assert [response.status for response in responses] == [200, 503, 202, 200, 404, 405, 200]
    assert responses[0].body == responses[-1].body == CONTENT
    assert responses[5].body == b""
    assert not live.closed
    assert caplog.text == ""

    keys = CasStore(tmp_path / "server-keys", 4)
    SERVER_IDENTITY.publish_public_key(keys)
    verifier = MessageVerifier(keys, 5.0, 30.0)

    for response in responses:
        assert (
            verifier.verify_response(response.status, response.headers, response.body)
            == SERVER_IDENTITY.node_id
        )
