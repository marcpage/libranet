"""End-to-end tests of the read, write, and list paths against a live server and a temp CAS.

Published messages land on a plain in-process queue, so no dispatcher runs.
Every response is expected to be signed by the server's node key.
"""

from __future__ import annotations
from base64 import b64encode
from http.client import HTTPConnection, HTTPResponse
from json import dumps, loads
from logging import getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import SHUT_WR, create_connection
from threading import Thread
from typing import Callable, Iterator
from zlib import compress

from pytest import fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore, node_store, source_of_truth_store
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.authentication import request_authenticator
from libranet.identity.content_digest import CONTENT_DIGEST_HEADER
from libranet.identity.errors import InvalidSignatureError, UnknownKeyError
from libranet.identity.keys import generate_private_key
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.signatures import MessageSigner, MessageVerifier
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    CREDENTIAL_REQUIRED,
    INVALID_CONTENT_ADDRESS,
    INVALID_SIGNATURE,
    PROBLEM_CONTENT_TYPE,
    SIGNATURE_REQUIRED,
)
from libranet.stats.module import StatsModule
from libranet.supervision.stubs import StubModule
from libranet.unbundler.resolved_files import ResolvedFiles
from libranet.validator.module import ValidatorModule
from libranet.webserver.config_auth import CONFIG_REALM
from libranet.webserver.config_credential import ConfigCredential, load_config_credential
from libranet.webserver.http_types import Request, Response
from libranet.webserver.server import REQUEST_PATH_HEADER, LibranetHTTPServer, build_router

CONTENT = b"hello libranet"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
MISSING_ID = ContentId.for_data(b"not stored", "sha256")
RETRY_AFTER_SECONDS = 7
APP_BUNDLE_ID = ContentId.for_data(b"an application's directory bundle", "sha256")
SERVER_IDENTITY = NodeIdentity.from_private_key(generate_private_key(), "sha256")


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def store(storage: StorageConfig) -> CasStore:
    store = source_of_truth_store(storage)
    store.write(CONTENT_ID, CONTENT)
    return store


@fixture
def server_keys(tmp_path: Path) -> MessageVerifier:
    """How a client that already holds the server's public key checks responses."""
    keys = CasStore(tmp_path / "client-keys", 4)
    SERVER_IDENTITY.publish_public_key(keys)
    return MessageVerifier(keys, 5.0, 30.0)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def allow_unsigned_api_reads() -> bool:
    """Whether the server serves unsigned ``/data`` reads; tests may parametrize it."""
    return True


@fixture
def applications() -> dict[str, str]:
    """The applications the server serves, by name; tests may parametrize it."""
    return {}


@fixture
def credential(storage: StorageConfig) -> ConfigCredential:
    """Where this node's `/config` credential would be captured."""
    return load_config_credential(LibranetConfig(storage=storage))


@fixture
def server(
    storage: StorageConfig,
    store: CasStore,
    queues: ModuleQueues,
    allow_unsigned_api_reads: bool,
    applications: dict[str, str],
    credential: ConfigCredential,
) -> Iterator[LibranetHTTPServer]:
    publisher = StubModule(ModuleName.WEBSERVER, queues)
    server = LibranetHTTPServer(
        ("127.0.0.1", 0),
        build_router(
            storage,
            RETRY_AFTER_SECONDS,
            publisher.publish,
            request_authenticator(LibranetConfig(storage=storage)),
            allow_unsigned_api_reads=allow_unsigned_api_reads,
            config_credential=credential,
            applications=applications,
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
def connection(server: LibranetHTTPServer) -> Iterator[HTTPConnection]:
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)
    yield connection
    connection.close()


def _get(connection: HTTPConnection, path: str, method: str = "GET") -> tuple[HTTPResponse, bytes]:
    connection.request(method, path)
    response = connection.getresponse()
    return response, response.read()


def _put(
    connection: HTTPConnection,
    content_id: ContentId,
    body: bytes,
    identity: NodeIdentity | None,
    sent: bytes | None = None,
) -> tuple[HTTPResponse, bytes]:
    """PUT ``sent`` (default ``body``) with a signature over ``body``, if ``identity``."""
    path = f"/data/{content_id}"
    headers = (
        {} if identity is None else MessageSigner(identity).sign_request("PUT", path, {}, body)
    )
    connection.request("PUT", path, body=body if sent is None else sent, headers=headers)
    response = connection.getresponse()
    return response, response.read()


def _post(
    connection: HTTPConnection,
    path: str,
    value: object,
    identity: NodeIdentity | None,
    sent: bytes | None = None,
    *,
    compressed: bool = False,
) -> tuple[HTTPResponse, bytes]:
    """POST ``sent`` (default ``value`` as JSON) with a signature over ``value``, if ``identity``.

    ``compressed`` zlib-compresses the JSON, and the signature covers it compressed.
    """
    body = dumps(value).encode("utf-8")
    body = compress(body) if compressed else body
    headers = (
        {} if identity is None else MessageSigner(identity).sign_request("POST", path, {}, body)
    )
    connection.request("POST", path, body=body if sent is None else sent, headers=headers)
    response = connection.getresponse()
    return response, response.read()


def _exchange(server: LibranetHTTPServer, request: bytes, *, end_input: bool = False) -> bytes:
    """Everything the server sends back on a fresh connection for raw ``request`` bytes."""
    host, port = server.server_address[:2]

    with create_connection((str(host), int(port)), timeout=5) as sock:
        sock.sendall(request)

        if end_input:
            sock.shutdown(SHUT_WR)

        return sock.makefile("rb").read()


def _signed_get(
    connection: HTTPConnection, path: str, identity: NodeIdentity, signed_path: str | None = None
) -> tuple[HTTPResponse, bytes]:
    """GET ``path`` with ``identity``'s signature over ``signed_path`` (default ``path``)."""
    headers = MessageSigner(identity).sign_request("GET", signed_path or path, {})
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    return response, response.read()


def _parse(reply: bytes) -> tuple[int, dict[str, str], bytes]:
    """The status, headers, and body of one raw response read to the end."""
    head, _, body = reply.partition(b"\r\n\r\n")
    status_line, *lines = head.decode("latin-1").split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines)
    return int(status_line.split()[1]), headers, body


def _new_identity() -> NodeIdentity:
    return NodeIdentity.from_private_key(generate_private_key(), "sha256")


def _published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def test_stored_content_is_served(connection: HTTPConnection, queues: ModuleQueues) -> None:
    response, body = _get(connection, f"/data/{CONTENT_ID}")

    assert response.status == 200
    assert body == CONTENT
    assert response.getheader("Content-Type") == "application/octet-stream"
    assert response.getheader("Content-Length") == str(len(CONTENT))
    assert "immutable" in (response.getheader("Cache-Control") or "")

    (message,) = _published(queues)
    assert message["event"] == EventType.DATA_REQUESTED
    assert message["source"] == ModuleName.WEBSERVER
    assert message["algorithm"] == "sha256"
    assert message["hash"] == CONTENT_ID.hash
    # The test client connects over loopback, so it is not a peer.
    assert message["external"] is False


def test_hash_case_and_query_string_are_ignored(connection: HTTPConnection) -> None:
    response, body = _get(connection, f"/data/SHA256/{CONTENT_ID.hash.upper()}?x=1")

    assert response.status == 200
    assert body == CONTENT


def test_missing_content_is_503_and_published(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    response, body = _get(connection, f"/data/{MISSING_ID}")

    assert response.status == 503
    assert response.getheader("Content-Type") == PROBLEM_CONTENT_TYPE
    assert response.getheader("Retry-After") == str(RETRY_AFTER_SECONDS)
    problem = loads(body)
    assert problem["type"] == CONTENT_UNAVAILABLE
    assert problem["status"] == 503
    assert problem["instance"] == f"/data/{MISSING_ID}"
    assert problem["retry_after"] == RETRY_AFTER_SECONDS

    requested, message = _published(queues)
    assert requested["event"] == EventType.DATA_REQUESTED
    assert requested["hash"] == MISSING_ID.hash
    assert message["event"] == EventType.DATA_NOT_FOUND
    assert message["source"] == ModuleName.WEBSERVER
    assert message["algorithm"] == "sha256"
    assert message["hash"] == MISSING_ID.hash


def test_invalid_content_ids_are_400(connection: HTTPConnection, queues: ModuleQueues) -> None:
    for path in ("/data/sha256/xyz", "/data/md5/" + "0" * 32, "/data/sha256/" + "0" * 63):
        response, body = _get(connection, path)

        assert response.status == 400, path
        assert loads(body)["type"] == INVALID_CONTENT_ADDRESS

    assert _published(queues) == []


def test_search_scans_caches_and_publishes(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    prefix = CONTENT_ID.hash[:6].upper()

    response, body = _get(connection, f"/data/search/{prefix}")

    assert response.status == 200
    assert response.getheader("Content-Type") == "application/json"
    assert loads(body) == {"results": [str(CONTENT_ID)]}

    cache_file = storage.search_cache_dir / prefix[:4].lower() / f"{prefix.lower()}.json"
    assert cache_file.read_bytes() == body

    (message,) = _published(queues)
    assert message["event"] == EventType.SEARCH_REQUESTED
    assert message["prefix"] == prefix.lower()
    assert message["cache_path"] == str(cache_file)


def test_data_and_search_read_content_archives(
    storage: StorageConfig,
    store: CasStore,
    queues: ModuleQueues,
    credential: ConfigCredential,
    tmp_path: Path,
) -> None:
    archived = ContentId.for_data(b"archived", "sha256")
    path = tmp_path / "held.zip"

    with ArchiveSink.create(path) as sink:
        sink.write(archived, b"archived")

    with ArchiveSource.open(path) as archive:
        router = build_router(
            storage,
            RETRY_AFTER_SECONDS,
            StubModule(ModuleName.WEBSERVER, queues).publish,
            request_authenticator(LibranetConfig(storage=storage)),
            allow_unsigned_api_reads=True,
            config_credential=credential,
            content=LayeredSource(store, [archive]),
        )
        data = router.dispatch(Request("GET", f"/data/{archived}", client_address="127.0.0.1"))
        stored = router.dispatch(Request("GET", f"/data/{CONTENT_ID}", client_address="127.0.0.1"))
        found = router.dispatch(
            Request("GET", f"/data/search/{archived.hash[:8]}", client_address="127.0.0.1")
        )

    assert (data.status, data.body) == (200, b"archived")
    assert (stored.status, stored.body) == (200, CONTENT)
    assert loads(found.body) == {"results": [str(archived)]}
    assert EventType.DATA_NOT_FOUND not in {message["event"] for message in _published(queues)}


def test_search_serves_a_fresh_cache_file(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    cache_file = storage.search_cache_dir / "abcd" / "abcdef.json"
    cache_file.parent.mkdir(parents=True)
    cache_file.write_bytes(b'{"results":["sha256/enriched"]}')

    response, body = _get(connection, "/data/search/abcdef")

    assert response.status == 200
    assert body == b'{"results":["sha256/enriched"]}'
    assert len(_published(queues)) == 1


def test_search_with_no_matches_is_an_empty_list(connection: HTTPConnection) -> None:
    other = "0" if CONTENT_ID.hash[0] != "0" else "f"

    response, body = _get(connection, f"/data/search/{other}")

    assert response.status == 200
    assert loads(body) == {"results": []}


def test_invalid_search_prefix_is_400(connection: HTTPConnection, queues: ModuleQueues) -> None:
    response, body = _get(connection, "/data/search/nothex")

    assert response.status == 400
    assert loads(body)["status"] == 400
    assert _published(queues) == []


def test_unknown_path_is_404_problem(connection: HTTPConnection) -> None:
    response, body = _get(connection, "/data/sha256")

    assert response.status == 404
    assert response.getheader("Content-Type") == PROBLEM_CONTENT_TYPE
    assert loads(body)["title"] == "Not Found"


def test_unsupported_method_is_405_and_closes_when_a_body_was_sent(
    connection: HTTPConnection,
) -> None:
    connection.request("POST", f"/data/{CONTENT_ID}", body=b"data")
    response = connection.getresponse()
    body = response.read()

    assert response.status == 405
    assert response.getheader("Allow") == "GET, PUT"
    assert response.getheader("Connection") == "close"
    assert loads(body)["status"] == 405


def test_head_gets_headers_without_a_body(connection: HTTPConnection) -> None:
    response, body = _get(connection, f"/data/{CONTENT_ID}", method="HEAD")

    assert response.status == 405
    assert body == b""


def test_connection_is_kept_alive_across_requests(connection: HTTPConnection) -> None:
    for _ in range(3):
        response, body = _get(connection, f"/data/{CONTENT_ID}")

        assert response.status == 200
        assert response.getheader("Connection") is None
        assert body == CONTENT


def test_responses_echo_the_request_target(connection: HTTPConnection) -> None:
    for target in (
        f"/data/{CONTENT_ID}",
        f"/data/{MISSING_ID}",
        f"/data/search/{CONTENT_ID.hash[:4]}?limit=1",
        "/nowhere",
    ):
        response, _ = _get(connection, target)

        assert response.getheader(REQUEST_PATH_HEADER) == target


def test_request_line_that_does_not_parse_echoes_no_path(server: LibranetHTTPServer) -> None:
    """Not even the path of the connection's previous request."""
    reply = _exchange(server, b"GET /nowhere HTTP/1.1\r\nHost: x\r\n\r\nGET /a b HTTP/1.1\r\n\r\n")

    refusal = reply.index(b"HTTP/1.1 400")
    assert f"{REQUEST_PATH_HEADER}: /nowhere\r\n".encode() in reply[:refusal]
    assert REQUEST_PATH_HEADER.encode() not in reply[refusal:]


def test_unknown_methods_get_a_501_problem(server: LibranetHTTPServer) -> None:
    host, port = server.server_address[:2]

    with create_connection((str(host), int(port)), timeout=5) as sock:
        sock.sendall(b"BREW /data HTTP/1.1\r\nHost: x\r\n\r\n")
        reply = sock.makefile("rb").read()

    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 501")
    assert b"Content-Type: application/problem+json" in head
    assert loads(body)["status"] == 501


def test_handler_crash_is_a_500_problem(server: LibranetHTTPServer) -> None:
    def explode(request: Request) -> Response:
        raise RuntimeError("boom")

    # Added under /data, since every other path is already the applications' route.
    server.router.add("GET", "/data/boom", explode)
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)

    try:
        response, body = _get(connection, "/data/boom")

    finally:
        connection.close()

    assert response.status == 500
    assert loads(body) == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "instance": "/data/boom",
    }


def test_signed_upload_is_accepted_and_the_connection_reused(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    identity = _new_identity()
    body = b"new content"
    content_id = ContentId.for_data(body, "sha256")

    response, reply = _put(connection, content_id, body, identity)

    assert response.status == 202
    assert reply == b""
    assert response.getheader("Connection") is None
    assert node_store(storage, identity.node_id).read(content_id) == body
    (message,) = _published(queues)
    assert message["event"] == EventType.PUT_COMPLETED
    assert message["node_id"] == str(identity.node_id)

    response, reply = _get(connection, f"/data/{CONTENT_ID}")

    assert response.status == 200
    assert reply == CONTENT


def test_upload_of_stored_content_is_204(connection: HTTPConnection, queues: ModuleQueues) -> None:
    response, reply = _put(connection, CONTENT_ID, CONTENT, _new_identity())

    assert response.status == 204
    assert reply == b""
    assert response.getheader("Content-Length") is None
    assert _published(queues) == []


def test_uploaded_content_is_served_once_validated(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    identity = _new_identity()
    validator = ValidatorModule(ModuleName.VALIDATOR, ModuleQueues(Queue(), Queue()), storage)

    # First contact: a peer pushes its own public key before anything else,
    # and it is held at once so the rest of the exchange can be verified.
    response, _ = _put(connection, identity.node_id, identity.public_key, identity)
    assert response.status == 201
    assert _published(queues) == []

    upload = b"uploaded after the key"
    upload_id = ContentId.for_data(upload, "sha256")
    response, _ = _put(connection, upload_id, upload, identity)
    assert response.status == 202

    response, _ = _get(connection, f"/data/{upload_id}")
    assert response.status == 503

    completed, *_ = _published(queues)
    validator.handle(completed)
    response, body = _get(connection, f"/data/{upload_id}")

    assert response.status == 200
    assert body == upload
    assert _get(connection, f"/data/{identity.node_id}")[1] == identity.public_key


def test_a_key_someone_else_pushed_compressed_still_verifies_its_owner(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig, store: CasStore
) -> None:
    owner, other = _new_identity(), _new_identity()
    validator = ValidatorModule(ModuleName.VALIDATOR, ModuleQueues(Queue(), Queue()), storage)
    compressed = compress(owner.public_key)

    response, _ = _put(connection, owner.node_id, compressed, other)
    assert response.status == 202
    validator.handle(_published(queues)[0])
    assert store.read(owner.node_id) == compressed

    upload = b"sent by the key's owner"
    response, _ = _put(connection, ContentId.for_data(upload, "sha256"), upload, owner)

    # Refused as an invalid signature (and the connection closed) if the
    # compressed key could not be used.
    assert response.status == 202
    assert response.getheader("Connection") is None


def test_unsigned_upload_is_401_and_keeps_the_connection(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    response, reply = _put(connection, MISSING_ID, b"not stored", None)

    assert response.status == 401
    assert response.getheader("Connection") is None
    assert loads(reply)["type"] == SIGNATURE_REQUIRED
    assert _published(queues) == []
    assert _get(connection, f"/data/{CONTENT_ID}")[0].status == 200


def test_bad_signature_is_401_and_closes_the_connection(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    response, reply = _put(connection, MISSING_ID, b"not stored", _new_identity(), sent=b"forged")

    assert response.status == 401
    assert response.getheader("Connection") == "close"
    assert loads(reply)["type"] == INVALID_SIGNATURE
    assert not storage.incoming_dir.exists()
    assert _published(queues) == []


def test_oversized_upload_is_refused_without_waiting_for_the_body(
    server: LibranetHTTPServer, queues: ModuleQueues
) -> None:
    reply = _exchange(
        server,
        f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\nContent-Length: {10 << 20}\r\n\r\n".encode(),
    )

    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 413")
    assert b"Connection: close" in head
    assert loads(body)["type"] == CONTENT_TOO_LARGE
    assert loads(body)["max_bytes"] == 1 << 20
    assert _published(queues) == []


def test_chunked_upload_is_411(server: LibranetHTTPServer) -> None:
    reply = _exchange(
        server,
        f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n".encode(),
    )

    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 411")
    assert b"Connection: close" in head
    assert loads(body)["status"] == 411


def test_invalid_content_length_is_400(server: LibranetHTTPServer, queues: ModuleQueues) -> None:
    for lengths in (["abc"], ["-1"], ["+5"], ["1_0"], ["\u00b2"], ["5", "6"], ["5, 5"]):
        fields = "".join(f"Content-Length: {length}\r\n" for length in lengths)
        reply = _exchange(
            server, f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\n{fields}\r\n".encode()
        )

        head, _, body = reply.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 400"), lengths
        assert b"Connection: close" in head
        assert loads(body)["detail"] == "Invalid Content-Length header."

    assert _published(queues) == []


def test_repeated_identical_content_length_is_accepted(server: LibranetHTTPServer) -> None:
    reply = _exchange(
        server,
        f"GET /data/{CONTENT_ID} HTTP/1.1\r\nHost: x\r\n"
        "Content-Length: 0\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode(),
    )

    assert reply.startswith(b"HTTP/1.1 200")
    assert reply.endswith(CONTENT)


def test_truncated_upload_is_dropped_without_a_response(
    server: LibranetHTTPServer, queues: ModuleQueues, storage: StorageConfig
) -> None:
    reply = _exchange(
        server,
        f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n".encode()
        + b"only ten b",
        end_input=True,
    )

    assert reply == b""
    assert not storage.incoming_dir.exists()
    assert _published(queues) == []


def test_every_routed_response_is_signed_over_what_was_sent(
    connection: HTTPConnection, server_keys: MessageVerifier
) -> None:
    uploader = _new_identity()
    upload = b"signed upload"
    exchanges: list[Callable[[], tuple[HTTPResponse, bytes]]] = [
        lambda: _get(connection, f"/data/{CONTENT_ID}"),
        lambda: _get(connection, f"/data/{MISSING_ID}"),
        lambda: _get(connection, f"/data/search/{CONTENT_ID.hash[:4]}"),
        lambda: _get(connection, "/nowhere"),
        lambda: _get(connection, f"/data/{CONTENT_ID}", method="HEAD"),
        lambda: _put(connection, ContentId.for_data(upload, "sha256"), upload, uploader),
        lambda: _put(connection, CONTENT_ID, CONTENT, uploader),
        lambda: _put(connection, MISSING_ID, b"x", None),
        lambda: _put(connection, MISSING_ID, b"x", uploader, sent=b"y"),
    ]
    statuses = []

    for exchange in exchanges:
        response, body = exchange()
        headers = dict(response.getheaders())
        statuses.append(response.status)

        assert server_keys.verify_response(response.status, headers, body) == (
            SERVER_IDENTITY.node_id
        )
        assert (CONTENT_DIGEST_HEADER in headers) == bool(body)

    assert statuses == [200, 503, 200, 404, 405, 202, 204, 401, 401]


def test_a_changed_body_fails_the_response_signature(
    connection: HTTPConnection, server_keys: MessageVerifier
) -> None:
    response, body = _get(connection, f"/data/{CONTENT_ID}")
    headers = dict(response.getheaders())

    with raises(InvalidSignatureError):
        server_keys.verify_response(response.status, headers, body + b"!")

    with raises(InvalidSignatureError):
        server_keys.verify_response(404, headers, body)


def test_server_errors_and_closing_responses_are_signed(
    server: LibranetHTTPServer, server_keys: MessageVerifier
) -> None:
    for request, expected in (
        (b"BREW /data HTTP/1.1\r\nHost: x\r\n\r\n", 501),
        (b"GET /data HTTP/1.1\r\nHost: x\r\nContent-Length: nope\r\n\r\n", 400),
        (
            f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\nContent-Length: 9999999\r\n\r\n".encode(),
            413,
        ),
    ):
        status, headers, body = _parse(_exchange(server, request))

        assert status == expected, request
        assert server_keys.verify_response(status, headers, body) == SERVER_IDENTITY.node_id


def test_client_can_authenticate_the_server_after_first_contact(
    connection: HTTPConnection, store: CasStore, tmp_path: Path
) -> None:
    SERVER_IDENTITY.publish_public_key(store)
    client = _new_identity()
    client_keys = CasStore(tmp_path / "client-cache", 4)
    verifier = MessageVerifier(client_keys, 5.0, 30.0)

    # Step 1: push our key; the response names the server's identity.
    response, body = _put(connection, client.node_id, client.public_key, client)
    first_reply = (response.status, dict(response.getheaders()), body)

    with raises(UnknownKeyError) as unknown:
        verifier.verify_response(*first_reply)

    server_id = unknown.value.node_id
    assert server_id == SERVER_IDENTITY.node_id

    # Step 2: fetch the server's key, which authenticates that very response.
    response, key = _get(connection, f"/data/{server_id}")
    assert response.status == 200
    assert server_id.matches(key)
    client_keys.write(server_id, key)

    assert verifier.verify_response(*first_reply) == server_id


def test_signed_read_is_served(
    connection: HTTPConnection, store: CasStore, queues: ModuleQueues
) -> None:
    reader = _new_identity()
    reader.publish_public_key(store)

    response, body = _signed_get(connection, f"/data/{CONTENT_ID}", reader)

    assert response.status == 200
    assert body == CONTENT
    assert response.getheader("Connection") is None


def test_read_with_a_failed_signature_is_refused_and_closed(
    connection: HTTPConnection, store: CasStore, queues: ModuleQueues
) -> None:
    reader = _new_identity()
    reader.publish_public_key(store)

    for path in (f"/data/{MISSING_ID}", "/nowhere"):
        response, body = _signed_get(connection, path, reader, signed_path="/elsewhere")

        assert response.status == 401, path
        assert response.getheader("Connection") == "close"
        assert loads(body)["type"] == INVALID_SIGNATURE

    # The refused read was never handled, so no retrieval was requested.
    assert _published(queues) == []


def test_signed_read_with_an_oversized_body_is_refused(server: LibranetHTTPServer) -> None:
    headers = "".join(
        f"{name}: {value}\r\n"
        for name, value in MessageSigner(_new_identity())
        .sign_request("GET", f"/data/{CONTENT_ID}", {}, b"x")
        .items()
    )
    reply = _exchange(
        server,
        f"GET /data/{CONTENT_ID} HTTP/1.1\r\nHost: x\r\n{headers}"
        f"Content-Length: {10 << 20}\r\n\r\n".encode(),
    )

    status, response_headers, body = _parse(reply)
    assert status == 413
    assert response_headers["Connection"] == "close"
    assert loads(body)["type"] == CONTENT_TOO_LARGE


@mark.parametrize("allow_unsigned_api_reads", [False])
def test_unsigned_api_reads_can_be_refused(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    unsigned: list[str] = [
        f"/data/{CONTENT_ID}",
        f"/data/{MISSING_ID}",
        f"/data/search/{CONTENT_ID.hash[:4]}",
        "/data/no/such/endpoint",
    ]

    for path in unsigned:
        response, body = _get(connection, path)

        assert response.status == 401, path
        assert response.getheader("Connection") is None
        assert loads(body)["type"] == SIGNATURE_REQUIRED

    response, body = _get(connection, f"/data/{CONTENT_ID}", method="HEAD")

    assert response.status == 401
    assert body == b""

    # Refused reads were never handled: no retrieval or search was requested.
    assert _published(queues) == []
    assert _get(connection, "/elsewhere")[0].status == 404


@mark.parametrize("allow_unsigned_api_reads", [False])
def test_signing_nodes_are_served_when_unsigned_api_reads_are_refused(
    connection: HTTPConnection, store: CasStore, queues: ModuleQueues
) -> None:
    known = _new_identity()
    known.publish_public_key(store)

    verified, body = _signed_get(connection, f"/data/{CONTENT_ID}", known)

    assert verified.status == 200
    assert body == CONTENT

    # A node whose key is not held yet is trusted provisionally, as ever.
    provisional, _ = _signed_get(connection, f"/data/{MISSING_ID}", _new_identity())

    assert provisional.status == 503
    assert [message["event"] for message in _published(queues)] == [
        EventType.DATA_REQUESTED,
        EventType.DATA_REQUESTED,
        EventType.DATA_NOT_FOUND,
    ]

    forged, body = _signed_get(connection, f"/data/{CONTENT_ID}", known, signed_path="/data")

    assert forged.status == 401
    assert forged.getheader("Connection") == "close"
    assert loads(body)["type"] == INVALID_SIGNATURE


@mark.parametrize("allow_unsigned_api_reads", [True, False])
def test_uploads_always_need_a_valid_signature(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    identity = _new_identity()
    body = b"new content"
    content_id = ContentId.for_data(body, "sha256")

    refused, _ = _put(connection, content_id, body, None)
    forged, _ = _put(connection, content_id, body, identity, sent=b"forged")

    assert refused.status == 401
    assert forged.status == 401
    assert forged.getheader("Connection") == "close"

    connection.close()
    accepted, _ = _put(connection, content_id, body, identity)

    assert accepted.status == 202
    assert node_store(storage, identity.node_id).read(content_id) == body
    assert len(_published(queues)) == 1


@mark.parametrize("applications", [{"myapp": str(APP_BUNDLE_ID)}])
@mark.parametrize("allow_unsigned_api_reads", [True, False])
def test_unsigned_reads_outside_the_api_are_always_honored(
    server: LibranetHTTPServer, storage: StorageConfig
) -> None:
    resolved = ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length)
    write_atomically(resolved.path_for(APP_BUNDLE_ID, "index.html"), b"<html>")
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)

    try:
        response, body = _get(connection, "/myapp/index.html")

    finally:
        connection.close()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html"
    assert body == b"<html>"


@mark.parametrize("applications", [{"myapp": str(APP_BUNDLE_ID)}])
def test_an_application_file_not_yet_resolved_is_asked_for(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    redirect, _ = _get(connection, "/MyApp")
    response, body = _get(connection, "/MyApp/docs/")

    assert redirect.status == 302
    assert redirect.getheader("Location") == "/MyApp/"
    assert response.status == 503
    assert response.getheader("Retry-After") == str(RETRY_AFTER_SECONDS)
    assert loads(body)["type"] == CONTENT_UNAVAILABLE
    (asked,) = _published(queues)
    assert asked["event"] == EventType.APP_PATH_NOT_FOUND
    assert (asked["bundle"], asked["path"]) == (str(APP_BUNDLE_ID), "docs/index.html")


def test_config_passes_a_local_client_on_to_the_credential_challenge(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    # A remote client never gets this far: it is refused with 403 instead.
    response, body = _get(connection, "/config/backups")

    assert response.status == 401
    assert (
        response.getheader("WWW-Authenticate") == 'Basic realm="Libranet /config", charset="UTF-8"'
    )
    assert loads(body)["instance"] == "/config/backups"
    assert _published(queues) == []


def test_lists_are_503_until_derived_and_then_served_as_written(
    connection: HTTPConnection, storage: StorageConfig
) -> None:
    response, body = _get(connection, "/data/nodes")

    assert response.status == 503
    assert response.getheader("Retry-After") == str(RETRY_AFTER_SECONDS)
    assert loads(body)["status"] == 503

    storage.derived_dir.mkdir(parents=True)
    storage.node_list_path.write_bytes(b'{"nodes":{"http://localhost:8080":"sha256/abc"}}')
    storage.seek_list_path.write_bytes(b'{"data":[],"search":["ab"]}')

    for path, derived in (
        ("/data/nodes", storage.node_list_path),
        ("/data/seek", storage.seek_list_path),
    ):
        response, body = _get(connection, path)

        assert response.status == 200
        assert response.getheader("Content-Type") == "application/json"
        assert body == derived.read_bytes()


def test_signed_node_list_is_published_with_localhost_resolved(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    identity = _new_identity()
    nodes = {
        "http://localhost:4300": str(identity.node_id),
        "http://192.0.2.9:8080": str(MISSING_ID),
    }

    response, reply = _post(connection, "/data/nodes", {"nodes": nodes}, identity)

    assert response.status == 202
    assert reply == b""
    assert response.getheader("Connection") is None
    (message,) = _published(queues)
    assert message["event"] == EventType.NODES_RECEIVED
    # The test client reaches the server from IPv4 loopback.
    assert message["nodes"] == {
        "http://127.0.0.1:4300": str(identity.node_id),
        "http://192.0.2.9:8080": str(MISSING_ID),
    }


def test_signed_seek_list_is_published_for_its_signer(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    identity = _new_identity()

    response, _ = _post(connection, "/data/seek", {"data": [str(MISSING_ID)]}, identity)

    assert response.status == 202
    (message,) = _published(queues)
    assert message["event"] == EventType.SEEK_RECEIVED
    assert message["node_id"] == str(identity.node_id)
    assert (message["data"], message["search"]) == ([str(MISSING_ID)], [])


def test_a_compressed_list_may_expand_past_the_transfer_limit(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    value = {"search": ["ab"] * 200_000}

    assert len(dumps(value)) > storage.max_object_bytes

    response, _ = _post(connection, "/data/seek", value, _new_identity(), compressed=True)

    assert response.status == 202
    (message,) = _published(queues)
    assert message["search"] == value["search"]


@mark.parametrize("allow_unsigned_api_reads", [True, False])
@mark.parametrize("path", ["/data/nodes", "/data/seek"])
def test_list_posts_always_need_a_valid_signature(
    connection: HTTPConnection, queues: ModuleQueues, path: str
) -> None:
    identity = _new_identity()
    value: dict[str, object] = {"nodes": {}, "data": []}

    refused, body = _post(connection, path, value, None)

    assert refused.status == 401
    assert loads(body)["type"] == SIGNATURE_REQUIRED
    assert refused.getheader("Connection") is None

    forged, body = _post(connection, path, value, identity, sent=b'{"nodes":{},"data":[]}  ')

    assert forged.status == 401
    assert loads(body)["type"] == INVALID_SIGNATURE
    assert forged.getheader("Connection") == "close"
    assert _published(queues) == []


def test_posted_lists_reach_the_served_files_through_the_stats_module(
    connection: HTTPConnection, queues: ModuleQueues, storage: StorageConfig
) -> None:
    peer = _new_identity()
    _post(connection, "/data/nodes", {"nodes": {"http://localhost:4300": str(peer.node_id)}}, peer)
    _post(connection, "/data/seek", {"data": [str(CONTENT_ID)]}, peer)
    # A miss here puts an entry on this node's own seek list.
    _get(connection, f"/data/{MISSING_ID}")

    stats = StatsModule(
        ModuleName.STATS, ModuleQueues(Queue(), Queue()), LibranetConfig(storage=storage)
    )
    stats.on_start()

    try:
        for message in _published(queues):
            stats.handle(message)

        stats.derive()

    finally:
        stats.on_stop()

    _, nodes = _get(connection, "/data/nodes")
    _, seek = _get(connection, "/data/seek")

    assert loads(nodes)["nodes"]["http://127.0.0.1:4300"] == str(peer.node_id)
    # The peer's own seek list is recorded, but only this node's is served.
    assert loads(seek) == {"data": [str(MISSING_ID)], "search": []}


CONFIG_USER = "admin"
CONFIG_PASSWORD = "correct horse"
CONFIG_CHALLENGE = f'Basic realm="{CONFIG_REALM}", charset="UTF-8"'


def _credentials(user: str = CONFIG_USER, password: str = CONFIG_PASSWORD) -> dict[str, str]:
    """The `Authorization` header a `/config` client sends."""
    encoded = b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {encoded}"}


def _config(
    connection: HTTPConnection,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[HTTPResponse, bytes]:
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    return response, response.read()


def test_the_first_config_request_captures_its_credentials_and_is_served(
    connection: HTTPConnection, credential: ConfigCredential
) -> None:
    response, body = _config(connection, "/config", headers=_credentials())

    assert response.status == 200
    assert "/config/backups" in {entry["path"] for entry in loads(body)["endpoints"]}
    assert credential.captured


def test_config_requests_afterwards_are_checked_against_what_was_captured(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    _config(connection, "/config", headers=_credentials())
    allowed, _ = _config(connection, "/config/backups", headers=_credentials())
    refused, body = _config(connection, "/config/backups", headers=_credentials(password="guessed"))

    assert allowed.status == 503
    assert refused.status == 401
    assert refused.getheader("WWW-Authenticate") == CONFIG_CHALLENGE
    assert loads(body)["type"] == CREDENTIAL_REQUIRED
    assert _published(queues) == []


def test_a_config_request_without_credentials_is_challenged(
    connection: HTTPConnection, credential: ConfigCredential
) -> None:
    response, body = _config(connection, "/config/backups")

    assert response.status == 401
    assert response.getheader("WWW-Authenticate") == CONFIG_CHALLENGE
    assert response.getheader(REQUEST_PATH_HEADER) == "/config/backups"
    assert loads(body)["type"] == CREDENTIAL_REQUIRED
    assert not credential.captured


def test_a_config_endpoint_publishes_what_the_backup_module_will_act_on(
    connection: HTTPConnection, queues: ModuleQueues
) -> None:
    response, body = _config(
        connection,
        "/config/backups",
        "POST",
        _credentials(),
        dumps({"directory": "/home/me/documents"}).encode("utf-8"),
    )

    assert response.status == 202
    (message,) = _published(queues)
    assert message["event"] == EventType.BACKUP_JOB_CONFIGURED
    assert message["job_id"] == loads(body)["job_id"]
    assert message["directory"] == "/home/me/documents"


def test_a_remote_config_request_is_refused_before_it_can_capture_anything(
    server: LibranetHTTPServer, credential: ConfigCredential, queues: ModuleQueues
) -> None:
    # The live server only ever sees loopback clients, so the remote source
    # address is put to the router directly.
    request = Request(
        "GET",
        "/config/backups",
        headers=_credentials(),
        client_address="203.0.113.42",
    )
    response = server.router.dispatch(request)

    assert response.status == 403
    assert not credential.captured
    assert _published(queues) == []
