"""End-to-end tests of the read, write, and list paths against a live server and a temp CAS.

Published messages land on a plain in-process queue, so no dispatcher runs.
Every response is expected to be signed by the server's node key.
"""

# pylint: disable=too-many-lines

from __future__ import annotations
from base64 import b64encode
from errno import EADDRINUSE
from hashlib import sha256
from http.client import HTTPConnection, HTTPResponse, IncompleteRead
from json import dumps, loads
from logging import DEBUG, ERROR, WARNING, getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import SHUT_WR, create_connection, socket
from threading import Thread
from time import monotonic, sleep
from typing import Callable, Iterator
from zlib import compress

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import FileBundle, Metadata
from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.resolved_files import ResolvedFiles
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.authentication import RequestAuthenticator
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
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE, REQUEST_PATH_HEADER
from libranet.stats.module import StatsModule
from libranet.validator.module import ValidatorModule
from libranet.webserver.app_handler import CONFIG_APP_POLICY
from libranet.webserver.app_registry import Application, ApplicationRegistry
from libranet.webserver.app_store import StoredValue
from libranet.webserver.config_auth import CONFIG_REALM
from libranet.webserver.config_credential import ConfigCredential
from libranet.webserver.config_handlers import NodeDescription
from libranet.webserver.errors import ResponseCutShortError
from libranet.webserver.http_types import Request, RequestBody, Response, StreamedBody
from libranet.webserver.local_folders import LocalFolders
from libranet.webserver.router import Router
from libranet.webserver.server import LibranetHTTPServer, build_config_router, build_router

from tests.helpers import with_node_key
from tests.stubs import StubModule

CONTENT = b"hello libranet"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
MISSING_ID = ContentId.for_data(b"not stored", "sha256")
RETRY_AFTER_SECONDS = 7
APP_BUNDLE_ID = ContentId.for_data(b"an application's directory bundle", "sha256")
SERVER_IDENTITY = NodeIdentity.from_private_key(generate_private_key(), "sha256")
SERVER_NODE = NodeDescription(SERVER_IDENTITY.node_id, NetworkConfig())
FILM = b"0123456789"


@fixture
def store(storage: StorageConfig) -> CasStore:
    store = CasStore.source_of_truth(storage)
    store.write(CONTENT_ID, CONTENT)
    return store


@fixture
def server_keys(tmp_path: Path) -> MessageVerifier:
    """How a client that already holds the server's public key checks responses."""
    keys = CasStore(tmp_path / "client-keys", 4)
    SERVER_IDENTITY.publish_public_key(keys)
    return MessageVerifier(keys, 5.0, 30.0)


@fixture
def allow_unsigned_api_reads() -> bool:
    """Whether the server serves unsigned ``/data`` reads; tests may parametrize it."""
    return True


@fixture
def applications() -> dict[str, ContentId]:
    """The applications registered when the server starts, by name; tests may parametrize it."""
    return {}


@fixture
def registry(storage: StorageConfig) -> ApplicationRegistry:
    """The registry the server reads, from its own copy, as another writer of the file would."""
    return ApplicationRegistry(storage.applications_path)


@fixture
def credential(storage: StorageConfig) -> ConfigCredential:
    """Where this node's `/config` credential would be captured."""
    return ConfigCredential.of(LibranetConfig(storage=storage))


def serving(server: LibranetHTTPServer) -> Iterator[LibranetHTTPServer]:
    """``server``, serving until the test that asked for it is done."""
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join()


def connected(server: LibranetHTTPServer) -> Iterator[HTTPConnection]:
    """A connection to ``server``, closed when the test that asked for it is done."""
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)
    yield connection
    connection.close()


@fixture
def config_server(
    storage: StorageConfig,
    queues: ModuleQueues,
    applications: dict[str, ContentId],
    registry: ApplicationRegistry,
    credential: ConfigCredential,
) -> Iterator[LibranetHTTPServer]:
    """The server on ``/config``'s port, with the applications registered."""
    for name, bundle in applications.items():
        registry.register(Application.create(name, bundle))

    yield from serving(
        LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_config_router(
                storage,
                RETRY_AFTER_SECONDS,
                StubModule(ModuleName.WEBSERVER, queues).publish,
                config_credential=credential,
                node=SERVER_NODE,
            ),
            getLogger("test.webserver"),
            MessageSigner(SERVER_IDENTITY),
        )
    )


@fixture
def server(
    storage: StorageConfig,
    store: CasStore,
    queues: ModuleQueues,
    allow_unsigned_api_reads: bool,
    config_server: LibranetHTTPServer,
) -> Iterator[LibranetHTTPServer]:
    """The server on the main port, which sends ``/config``'s pages to ``config_server``."""
    # `store` is asked for so that what it holds is there to be served.
    # pylint: disable=unused-argument
    yield from serving(
        LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_router(
                storage,
                RETRY_AFTER_SECONDS,
                StubModule(ModuleName.WEBSERVER, queues).publish,
                RequestAuthenticator.of(LibranetConfig(storage=storage)),
                allow_unsigned_api_reads=allow_unsigned_api_reads,
                config_port=config_server.server_port,
            ),
            getLogger("test.webserver"),
            MessageSigner(SERVER_IDENTITY),
        )
    )


@fixture
def connection(server: LibranetHTTPServer) -> Iterator[HTTPConnection]:
    yield from connected(server)


@fixture
def config_connection(config_server: LibranetHTTPServer) -> Iterator[HTTPConnection]:
    yield from connected(config_server)


def _get(
    connection: HTTPConnection,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
) -> tuple[HTTPResponse, bytes]:
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    return response, response.read()


def _from_page(connection: HTTPConnection, page: str) -> dict[str, str]:
    """The ``Referer`` a request from the page at ``page`` on ``connection``'s node carries."""
    return {"Referer": f"http://{connection.host}:{connection.port}{page}"}


# What a request put to a router directly says of where it is from: a page
# of the movie application, which the local server trusts.
MOVIE_PAGE = {"Host": "localhost:8080", "Referer": "http://localhost:8080/movie/"}


def _resolve(storage: StorageConfig, bundle: ContentId, entry_path: str, content: bytes) -> None:
    """Resolve the file at ``entry_path`` in ``bundle`` as the unbundler would, holding its part."""
    part = ContentId.for_data(content, "sha256")
    CasStore.source_of_truth(storage).write(part, content)
    entry = FileBundle(
        (str(part),),
        Metadata(size_bytes=len(content), algorithm="sha256", hash=sha256(content).hexdigest()),
        part_sizes_bytes=(len(content),),
    )
    resolved = ResolvedFiles(storage.resolved_files_dir, storage.hash_prefix_length)
    write_atomically(resolved.entry_for(bundle, entry_path), compress(encode_bundle(entry)))


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


def test_data_and_search_read_content_archives(
    storage: StorageConfig, store: CasStore, queues: ModuleQueues, tmp_path: Path
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
            RequestAuthenticator.of(LibranetConfig(storage=storage)),
            allow_unsigned_api_reads=True,
            config_port=8180,
            content=LayeredSource(store, [archive]),
        )
        data = router.dispatch(Request("GET", f"/data/{archived}", client_address="127.0.0.1"))
        stored = router.dispatch(Request("GET", f"/data/{CONTENT_ID}", client_address="127.0.0.1"))
        found = router.dispatch(
            Request("GET", f"/data/search/{archived.hash[:8]}", client_address="127.0.0.1")
        )

    assert (data.status, data.body) == (200, b"archived")
    assert (stored.status, stored.body) == (200, CONTENT)
    # The archived content matches best; what the store holds follows it.
    assert loads(found.body)["results"][0] == str(archived)
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


def test_search_answers_with_what_is_held_nearest_even_sharing_no_bit(
    connection: HTTPConnection,
) -> None:
    # The opposite top bit to the content's first digit, so they share none.
    other = "0" if int(CONTENT_ID.hash[0], 16) >= 8 else "f"

    response, body = _get(connection, f"/data/search/{other}")

    assert response.status == 200
    assert loads(body) == {"results": [str(CONTENT_ID)]}


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
    assert CasStore.for_node(storage, identity.node_id).read(content_id) == body
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
    validator = ValidatorModule(
        ModuleName.VALIDATOR, ModuleQueues(Queue(), Queue()), LibranetConfig(storage=storage)
    )

    # First contact: a peer pushes its own public key before anything else,
    # and it is held at once so the rest of the exchange can be verified. It
    # is announced as stored rather than handed to the validator.
    response, _ = _put(connection, identity.node_id, identity.public_key, identity)
    assert response.status == 201
    assert [message["event"] for message in _published(queues)] == [EventType.DATA_STORED]

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
    validator = ValidatorModule(
        ModuleName.VALIDATOR, ModuleQueues(Queue(), Queue()), LibranetConfig(storage=storage)
    )
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
        f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\n".encode()
        + f"Content-Length: {10 << 20}\r\n\r\n".encode(),
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
        f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\n".encode()
        + b"Transfer-Encoding: chunked\r\n\r\n",
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


def _stream_at(
    server: LibranetHTTPServer,
    length_bytes: int | None,
    chunks: Iterator[bytes],
    method: str = "GET",
) -> str:
    """Where ``server`` answers ``method`` with ``chunks``, as a body of ``length_bytes``."""

    def streamed(_request: Request) -> Response:
        return Response(
            200, headers={"Content-Type": "text/plain"}, stream=StreamedBody(length_bytes, chunks)
        )

    # Added under /data, since every other path is already the applications' route.
    server.router.add(method, "/data/stream", streamed)
    return "/data/stream"


def test_a_streamed_body_is_sent_as_produced_and_signed_over_its_headers_alone(
    server: LibranetHTTPServer, connection: HTTPConnection, server_keys: MessageVerifier
) -> None:
    path = _stream_at(server, 7, iter([b"one ", b"two"]))

    response, body = _get(connection, path)
    headers = dict(response.getheaders())
    again, _ = _get(connection, f"/data/{CONTENT_ID}")

    assert (response.status, body) == (200, b"one two")
    assert response.getheader("Content-Length") == "7"
    assert response.getheader("Connection") is None
    assert CONTENT_DIGEST_HEADER not in headers
    assert server_keys.verify_response(response.status, headers) == SERVER_IDENTITY.node_id
    assert again.status == 200

    with raises(InvalidSignatureError, match="content-digest"):
        server_keys.verify_response(response.status, headers, body)


def test_a_streamed_body_of_unknown_length_is_sent_until_the_connection_closes(
    server: LibranetHTTPServer, connection: HTTPConnection
) -> None:
    path = _stream_at(server, None, iter([b"one ", b"two"]))

    response, body = _get(connection, path)

    assert (response.status, body) == (200, b"one two")
    assert response.getheader("Content-Length") is None
    assert response.getheader("Connection") == "close"


def test_a_streamed_body_is_not_produced_for_a_head_request(
    server: LibranetHTTPServer, connection: HTTPConnection
) -> None:
    produced: list[bytes] = []

    def chunks() -> Iterator[bytes]:
        produced.append(b"one")
        yield b"one"

    path = _stream_at(server, 3, chunks(), method="HEAD")

    response, body = _get(connection, path, method="HEAD")

    assert (response.status, body) == (200, b"")
    assert response.getheader("Content-Length") == "3"
    assert produced == []


def _cut_short(error: Exception) -> Iterator[bytes]:
    yield b"one "
    raise error


@mark.parametrize(
    "error, logged",
    [(ResponseCutShortError("a part did not come"), []), (RuntimeError("boom"), [ERROR])],
)
def test_a_streamed_body_cut_short_closes_the_connection(
    server: LibranetHTTPServer,
    connection: HTTPConnection,
    caplog: LogCaptureFixture,
    error: Exception,
    logged: list[int],
) -> None:
    path = _stream_at(server, 7, _cut_short(error))
    connection.request("GET", path)
    response = connection.getresponse()

    with caplog.at_level(WARNING), raises(IncompleteRead):
        response.read()

    assert response.status == 200
    assert [
        record.levelno for record in caplog.records if record.name == "test.webserver"
    ] == logged


def test_a_client_going_away_mid_body_is_logged_at_debug(
    server: LibranetHTTPServer, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG, "test.webserver")
    produced: list[int] = []

    def chunks() -> Iterator[bytes]:
        for count in range(64):
            produced.append(count)
            yield bytes(1 << 20)

    path = _stream_at(server, 64 << 20, chunks())
    host, port = server.server_address[:2]

    with create_connection((str(host), int(port)), timeout=5) as client:
        client.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        client.recv(1024)

    deadline = monotonic() + 5

    while "mid-body" not in caplog.text and monotonic() < deadline:
        sleep(0.01)

    assert "mid-body" in caplog.text
    assert len(produced) < 64


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
            f"PUT /data/{MISSING_ID} HTTP/1.1\r\nHost: x\r\n".encode()
            + b"Content-Length: 9999999\r\n\r\n",
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


def test_signed_read_is_served(connection: HTTPConnection, store: CasStore) -> None:
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
        "/data/applications",
        "/data/client",
        "/data/directory",
        "/data/imports",
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
    assert CasStore.for_node(storage, identity.node_id).read(content_id) == body
    assert len(_published(queues)) == 1


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
@mark.parametrize("allow_unsigned_api_reads", [True, False])
def test_unsigned_reads_outside_the_api_are_always_honored(
    server: LibranetHTTPServer, storage: StorageConfig
) -> None:
    _resolve(storage, APP_BUNDLE_ID, "index.html", b"<html>")
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)

    try:
        response, body = _get(connection, "/myapp/index.html")

    finally:
        connection.close()

    assert response.status == 200
    assert response.getheader("Content-Type") == "text/html"
    assert body == b"<html>"


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
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
    accessed, asked = _published(queues)
    assert (accessed["event"], accessed["bundle"]) == (EventType.APP_ACCESSED, str(APP_BUNDLE_ID))
    assert asked["event"] == EventType.APP_PATH_NOT_FOUND
    assert (asked["bundle"], asked["path"]) == (str(APP_BUNDLE_ID), "docs/index.html")


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
def test_a_range_of_an_application_file_is_sent_signed_over_its_headers(
    connection: HTTPConnection, storage: StorageConfig, server_keys: MessageVerifier
) -> None:
    _resolve(storage, APP_BUNDLE_ID, "film.bin", FILM)

    response, body = _get(connection, "/myapp/film.bin", headers={"Range": "bytes=2-5"})
    headers = dict(response.getheaders())
    again, _ = _get(connection, "/myapp/film.bin")

    assert (response.status, body) == (206, b"2345")
    assert response.getheader("Content-Range") == "bytes 2-5/10"
    assert response.getheader("Content-Length") == "4"
    assert response.getheader("Accept-Ranges") == "bytes"
    assert response.getheader("ETag") == f'"sha256-{sha256(FILM).hexdigest()}"'
    assert server_keys.verify_response(response.status, headers) == SERVER_IDENTITY.node_id
    assert response.getheader("Connection") is None
    assert again.status == 200


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
def test_a_range_past_the_end_of_an_application_file_is_416(
    connection: HTTPConnection, storage: StorageConfig
) -> None:
    _resolve(storage, APP_BUNDLE_ID, "film.bin", FILM)

    response, body = _get(connection, "/myapp/film.bin", headers={"Range": "bytes=10-"})

    assert response.status == 416
    assert response.getheader("Content-Range") == "bytes */10"
    assert loads(body)["status"] == 416


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
def test_a_head_of_an_application_file_sends_its_headers_alone(
    connection: HTTPConnection, storage: StorageConfig
) -> None:
    _resolve(storage, APP_BUNDLE_ID, "film.bin", FILM)

    response, body = _get(connection, "/myapp/film.bin", method="HEAD")
    again, _ = _get(connection, "/myapp/film.bin")

    assert (response.status, body) == (200, b"")
    assert response.getheader("Content-Length") == "10"
    assert response.getheader("Accept-Ranges") == "bytes"
    assert (again.status, again.getheader("Content-Length")) == (200, "10")


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
def test_an_application_answers_get_and_head_alone(connection: HTTPConnection) -> None:
    response, _ = _get(connection, "/myapp/film.bin", method="DELETE")

    assert response.status == 405
    assert response.getheader("Allow") == "GET, HEAD"


@mark.parametrize(
    "path, reads_into",
    [
        (f"/data/{CONTENT_ID}", None),
        (f"/data/{CONTENT_ID}/", ""),
        (f"/data/{CONTENT_ID}/docs/film.mp4", "docs/film.mp4"),
        (f"/data/{CONTENT_ID}/AES256-CBC", "AES256-CBC"),
        (f"/data/{CONTENT_ID}/AES256-CBC/{'a1' * 32}/film.mp4", "film.mp4"),
        (f"/data/search/{CONTENT_ID.hash[:4]}", None),
        ("/data/store/movie", None),
        ("/data/store/movie/last", None),
        ("/data/client", None),
        ("/data/applications", None),
    ],
)
def test_a_path_going_on_past_an_id_reads_into_its_bundle_and_no_other_does(
    local_server: LibranetHTTPServer, queues: ModuleQueues, path: str, reads_into: str | None
) -> None:
    response = local_server.router.dispatch(
        Request("GET", path, headers=MOVIE_PAGE, client_address="127.0.0.1")
    )

    asked = [message for message in _published(queues) if message["event"] == "app.path_not_found"]
    # Each reached a handler of its own, not the router's answer for no route.
    assert b"No resource exists at this path." not in response.body
    assert [message["path"] for message in asked] == ([] if reads_into is None else [reads_into])
    sandboxed = response.headers.get("Content-Security-Policy") == "sandbox"
    assert sandboxed is (reads_into is not None)


def test_a_bundle_is_read_into_by_get_and_head_alone(connection: HTTPConnection) -> None:
    # Without a Referer, which reading into a bundle never asks for.
    head, _ = _get(connection, f"/data/{CONTENT_ID}/film.mp4", method="HEAD")
    put, _ = _get(connection, f"/data/{CONTENT_ID}/film.mp4", method="PUT")

    assert head.status == 503
    assert put.status == 405
    assert put.getheader("Allow") == "GET, HEAD"


def test_the_access_log_leaves_out_the_key_of_an_encrypted_id(
    connection: HTTPConnection, caplog: LogCaptureFixture
) -> None:
    key = sha256(b"what an encrypted bundle decrypts to").hexdigest()

    with caplog.at_level(DEBUG, logger="test.webserver"):
        response, _ = _get(connection, f"/data/{CONTENT_ID}/AES256-CBC/{key}/film.mp4")

    assert response.status == 503
    assert f"/data/{CONTENT_ID}/AES256-CBC/<key>/film.mp4" in caplog.text
    assert key not in caplog.text


def test_data_is_sent_whole_whatever_range_is_asked_for(connection: HTTPConnection) -> None:
    response, body = _get(connection, f"/data/{CONTENT_ID}", headers={"Range": "bytes=0-1"})

    assert (response.status, body) == (200, CONTENT)
    assert response.getheader("Accept-Ranges") is None
    assert response.getheader("Content-Range") is None


def test_config_passes_a_local_client_on_to_the_credential_challenge(
    config_connection: HTTPConnection, queues: ModuleQueues
) -> None:
    # A remote client never gets this far: it is refused with 403 instead.
    response, body = _config(config_connection, "/config/api/backups")

    assert response.status == 401
    assert (
        response.getheader("WWW-Authenticate") == 'Basic realm="Libranet /config", charset="UTF-8"'
    )
    assert loads(body)["instance"] == "/config/api/backups"
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
        ModuleName.STATS,
        ModuleQueues(Queue(), Queue()),
        with_node_key(LibranetConfig(storage=storage)),
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

    # The peer's address is one to try, and is published once it has worked.
    assert "http://127.0.0.1:4300" not in loads(nodes)["nodes"]
    assert loads(storage.candidate_list_path.read_bytes())["nodes"] == [
        {"node_id": str(peer.node_id), "endpoints": ["http://127.0.0.1:4300"]}
    ]
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
    """A `/config` request, any body sent as JSON unless ``headers`` say otherwise.

    It is from the `/config` page, as its `Referer` says, as the page's own are.
    """
    sent = {
        **_from_page(connection, "/config/"),
        **({} if body is None else {"Content-Type": JSON_CONTENT_TYPE}),
    }
    connection.request(method, path, body=body, headers={**sent, **(headers or {})})
    response = connection.getresponse()
    return response, response.read()


def test_the_first_config_request_captures_its_credentials_and_is_served(
    config_connection: HTTPConnection, credential: ConfigCredential
) -> None:
    response, body = _config(config_connection, "/config/api", headers=_credentials())

    assert response.status == 200
    assert "/config/api/backups" in {entry["path"] for entry in loads(body)["endpoints"]}
    assert credential.captured


def test_config_requests_afterwards_are_checked_against_what_was_captured(
    config_connection: HTTPConnection, queues: ModuleQueues
) -> None:
    _config(config_connection, "/config/api", headers=_credentials())
    allowed, _ = _config(config_connection, "/config/api/backups", headers=_credentials())
    refused, body = _config(
        config_connection, "/config/api/backups", headers=_credentials(password="guessed")
    )

    assert allowed.status == 503
    assert refused.status == 401
    assert refused.getheader("WWW-Authenticate") == CONFIG_CHALLENGE
    assert loads(body)["type"] == CREDENTIAL_REQUIRED
    assert _published(queues) == []


def test_a_config_request_without_credentials_is_challenged(
    config_connection: HTTPConnection, credential: ConfigCredential
) -> None:
    response, body = _config(config_connection, "/config/api/backups")

    assert response.status == 401
    assert response.getheader("WWW-Authenticate") == CONFIG_CHALLENGE
    assert response.getheader(REQUEST_PATH_HEADER) == "/config/api/backups"
    assert loads(body)["type"] == CREDENTIAL_REQUIRED
    assert not credential.captured


def test_a_config_endpoint_publishes_what_the_backup_module_will_act_on(
    config_connection: HTTPConnection, queues: ModuleQueues
) -> None:
    response, body = _config(
        config_connection,
        "/config/api/backups",
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
    config_server: LibranetHTTPServer, credential: ConfigCredential, queues: ModuleQueues
) -> None:
    # The live server only ever sees loopback clients, so the remote source
    # address is put to the router directly.
    request = Request(
        "GET",
        "/config/api/backups",
        headers=_credentials(),
        client_address="203.0.113.42",
    )
    response = config_server.router.dispatch(request)

    assert response.status == 403
    assert not credential.captured
    assert _published(queues) == []


@mark.parametrize(
    "sent",
    [
        {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "same-site", "Origin": "http://127.0.0.1:3000"},
        {"Origin": "https://evil.example"},
        {"Host": "evil.example:8080", "Sec-Fetch-Site": "same-origin"},
    ],
)
@mark.parametrize("method", ["GET", "POST"])
def test_another_sites_config_request_is_refused_before_it_can_capture_anything(
    config_connection: HTTPConnection,
    credential: ConfigCredential,
    queues: ModuleQueues,
    registry: ApplicationRegistry,
    sent: dict[str, str],
    method: str,
) -> None:
    response, body = _config(
        config_connection,
        "/config/api/applications",
        method,
        {**_credentials(), "Content-Type": "text/plain", **sent},
        dumps({"name": "/", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"),
    )

    assert response.status == 403
    assert response.getheader("Content-Type") == PROBLEM_CONTENT_TYPE
    assert response.getheader("Access-Control-Allow-Origin") is None
    assert loads(body)["instance"] == "/config/api/applications"
    assert not credential.captured
    assert not registry.path.exists()
    assert _published(queues) == []


def test_the_config_pages_own_requests_are_served(
    config_connection: HTTPConnection,
    config_server: LibranetHTTPServer,
    registry: ApplicationRegistry,
) -> None:
    host, port = config_server.server_address[:2]
    page = {"Sec-Fetch-Site": "same-origin", "Origin": f"http://{str(host)}:{int(port)}"}
    opened, _ = _config(
        config_connection, "/config/api", headers={**_credentials(), "Sec-Fetch-Site": "none"}
    )
    registered, _ = _config(
        config_connection,
        "/config/api/applications",
        "POST",
        {**_credentials(), **page},
        dumps({"name": "wiki", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"),
    )

    assert opened.status == 200
    assert registered.status == 200
    assert registry.applications().bundles == {"wiki": APP_BUNDLE_ID}


@mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded"])
def test_a_config_body_not_sent_as_json_is_not_acted_on(
    config_connection: HTTPConnection,
    registry: ApplicationRegistry,
    content_type: str,
) -> None:
    response, body = _config(
        config_connection,
        "/config/api/applications",
        "POST",
        {**_credentials(), "Content-Type": content_type},
        dumps({"name": "/", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"),
    )
    # The body was read, so the connection carries the next request.
    after, _ = _config(config_connection, "/config/api", headers=_credentials())

    assert response.status == 415
    assert response.getheader("Content-Type") == PROBLEM_CONTENT_TYPE
    assert loads(body)["instance"] == "/config/api/applications"
    assert not registry.path.exists()
    assert after.status == 200


@mark.parametrize("applications", [{"config": APP_BUNDLE_ID}])
def test_every_path_beneath_config_but_the_apis_is_the_config_applications(
    config_connection: HTTPConnection, queues: ModuleQueues
) -> None:
    redirect, _ = _config(config_connection, "/config", headers=_credentials())
    # Where the endpoints were before they moved beneath /config/api, too.
    statuses = [
        _config(config_connection, path, headers=_credentials())[0].status
        for path in ("/config/", "/config/backups", "/config/restores/a/b")
    ]

    assert redirect.status == 302
    assert redirect.getheader("Location") == "/config/"
    assert statuses == [503, 503, 503]
    accessed, *asked = _published(queues)
    assert (accessed["event"], accessed["bundle"]) == (EventType.APP_ACCESSED, str(APP_BUNDLE_ID))
    assert [(message["event"], message["bundle"], message["path"]) for message in asked] == [
        (EventType.APP_PATH_NOT_FOUND, str(APP_BUNDLE_ID), path)
        for path in ("index.html", "backups", "restores/a/b")
    ]


def test_a_config_api_path_no_endpoint_serves_is_not_the_page(
    config_connection: HTTPConnection,
) -> None:
    response, body = _config(config_connection, "/config/api/unknown", headers=_credentials())

    assert response.status == 404
    assert response.getheader("Content-Type") == PROBLEM_CONTENT_TYPE
    assert loads(body)["instance"] == "/config/api/unknown"


def test_the_node_is_described_as_it_was_started(config_connection: HTTPConnection) -> None:
    response, body = _config(config_connection, "/config/api/node", headers=_credentials())

    assert response.status == 200
    assert loads(body) == SERVER_NODE.value()
    assert loads(body)["node_id"] == str(SERVER_IDENTITY.node_id)


@mark.parametrize("applications", [{"config": APP_BUNDLE_ID}])
@mark.parametrize("path", ["/config", "/config/", "/Config/index.html", "/%63onfig/"])
@mark.parametrize(
    "client_address, headers, status",
    [("203.0.113.42", _credentials(), 403), ("127.0.0.1", {}, 401)],
)
def test_the_config_application_is_served_only_to_an_authenticated_local_client(
    config_server: LibranetHTTPServer,
    credential: ConfigCredential,
    queues: ModuleQueues,
    path: str,
    client_address: str,
    headers: dict[str, str],
    status: int,
) -> None:
    # The live server only ever sees loopback clients, so the requests are put
    # to the router directly.
    response = config_server.router.dispatch(
        Request("GET", path, headers=headers, client_address=client_address)
    )

    assert response.status == status
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    assert not credential.captured
    # Nothing was resolved, or asked of the unbundler.
    assert _published(queues) == []


@mark.parametrize("applications", [{"config": APP_BUNDLE_ID}])
def test_the_config_application_answers_a_head(
    config_connection: HTTPConnection, storage: StorageConfig
) -> None:
    _resolve(storage, APP_BUNDLE_ID, "index.html", b"<html>")

    response, body = _config(config_connection, "/config/", "HEAD", _credentials())

    assert (response.status, body) == (200, b"")
    assert response.getheader("Content-Length") == "6"
    assert response.getheader("Content-Security-Policy") == CONFIG_APP_POLICY


def test_an_application_registered_through_config_is_served_at_once(
    connection: HTTPConnection,
    config_connection: HTTPConnection,
    storage: StorageConfig,
    queues: ModuleQueues,
) -> None:
    registered, body = _config(
        config_connection,
        "/config/api/applications",
        "POST",
        _credentials(),
        dumps({"name": "Wiki", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"),
    )

    assert registered.status == 200
    assert loads(body) == {"name": "wiki", "bundle": str(APP_BUNDLE_ID)}

    asked, _ = _get(connection, "/wiki/")
    accessed, message = _published(queues)
    _resolve(storage, APP_BUNDLE_ID, "index.html", b"<html>")
    served, page = _get(connection, "/wiki/")

    assert asked.status == 503
    assert accessed["event"] == EventType.APP_ACCESSED
    assert (message["event"], message["bundle"]) == (
        EventType.APP_PATH_NOT_FOUND,
        str(APP_BUNDLE_ID),
    )
    assert (served.status, page) == (200, b"<html>")

    removed, _ = _config(
        config_connection, "/config/api/applications/WIKI", "DELETE", _credentials()
    )
    gone, _ = _get(connection, "/wiki/")

    assert removed.status == 204
    assert gone.status == 404


@fixture
def local_server(
    storage: StorageConfig, queues: ModuleQueues, tmp_path: Path
) -> Iterator[LibranetHTTPServer]:
    """The main port's server, offering local clients a Movies folder holding a film.

    It serves a movie application, which the operator trusts.
    """
    (tmp_path / "Movies").mkdir()
    (tmp_path / "Movies" / "Film.mp4").write_bytes(FILM)
    registry = ApplicationRegistry(storage.applications_path)
    registry.register(Application.create("movie", APP_BUNDLE_ID))
    registry.trust("movie", True)
    yield from serving(
        LibranetHTTPServer(
            ("127.0.0.1", 0),
            build_router(
                storage,
                RETRY_AFTER_SECONDS,
                StubModule(ModuleName.WEBSERVER, queues).publish,
                RequestAuthenticator.of(LibranetConfig(storage=storage)),
                allow_unsigned_api_reads=True,
                config_port=8180,
                local_folders=LocalFolders({"Movies": tmp_path / "Movies"}),
                node_id=SERVER_IDENTITY.node_id,
            ),
            getLogger("test.webserver"),
            MessageSigner(SERVER_IDENTITY),
        )
    )


@fixture
def local_connection(local_server: LibranetHTTPServer) -> Iterator[HTTPConnection]:
    yield from connected(local_server)


def test_a_local_client_lists_the_folders_offered_and_what_they_hold(
    local_connection: HTTPConnection,
) -> None:
    page = _from_page(local_connection, "/movie/")
    client, client_body = _get(local_connection, "/data/client", headers=page)
    folders, folders_body = _get(local_connection, "/data/directory", headers=page)
    movies, movies_body = _get(local_connection, "/data/directory/Movies", headers=page)
    film, _ = _get(local_connection, "/data/directory/Movies/Film.mp4", headers=page)

    assert client.status == 200
    assert loads(client_body) == {"local": True}
    assert folders.status == 200
    assert loads(folders_body) == {"entries": {"Movies": {"type": "directory"}}}
    # Not taken for /data/{algorithm}/{hash}, whose pattern it also fits.
    assert movies.status == 200
    assert movies.getheader("Content-Type") == JSON_CONTENT_TYPE
    assert loads(movies_body)["entries"]["Film.mp4"]["size"] == len(FILM)
    assert film.status == 404


@mark.parametrize("path", ["/data/directory", "/data/directory/Movies"])
def test_another_client_or_sites_page_is_refused_the_folders(
    local_server: LibranetHTTPServer, local_connection: HTTPConnection, path: str
) -> None:
    # The live server only ever sees loopback clients, so the remote request
    # is put to the router directly.
    remote = local_server.router.dispatch(Request("GET", path, client_address="203.0.113.42"))
    named, _ = _get(local_connection, path, headers={"Host": "evil.example"})
    cross_site, _ = _get(local_connection, path, headers={"Sec-Fetch-Site": "cross-site"})

    assert remote.status == 403
    assert named.status == 403
    assert cross_site.status == 403


def test_a_local_client_imports_a_file_from_a_folder_offered(
    local_connection: HTTPConnection, queues: ModuleQueues, tmp_path: Path
) -> None:
    local_connection.request(
        "POST",
        "/data/imports",
        body=dumps({"path": "Movies/Film.mp4"}).encode("utf-8"),
        headers={"Content-Type": JSON_CONTENT_TYPE, **_from_page(local_connection, "/movie/")},
    )
    response = local_connection.getresponse()
    body = response.read()
    status, _ = _get(
        local_connection, "/data/imports", headers=_from_page(local_connection, "/movie/")
    )

    # Not taken for /data/{algorithm}/{hash}, whose pattern it also fits.
    assert response.status == 202
    import_id = loads(body)["import_id"]
    (asked,) = [
        message for message in _published(queues) if message["event"] == EventType.IMPORT_REQUESTED
    ]
    assert (asked["import_id"], asked["path"], asked["local_path"]) == (
        import_id,
        "Movies/Film.mp4",
        str((tmp_path / "Movies" / "Film.mp4").resolve()),
    )
    # The backup module has reported nothing to this server.
    assert status.status == 503


@mark.parametrize(
    "path, method",
    [
        ("/data/directory", "GET"),
        ("/data/imports", "GET"),
        ("/data/imports", "POST"),
        ("/data/bundles", "POST"),
    ],
)
def test_an_untrusted_applications_page_is_refused_what_only_trusted_ones_may_ask(
    local_server: LibranetHTTPServer,
    storage: StorageConfig,
    queues: ModuleQueues,
    path: str,
    method: str,
) -> None:
    ApplicationRegistry(storage.applications_path).register(
        Application.create("other", APP_BUNDLE_ID)
    )
    body = b'{"path": "Movies/Film.mp4"}' if path == "/data/imports" else b'{"add": {}}'
    sent = Request(
        method,
        path,
        headers={
            "Host": "localhost:8080",
            "Referer": "http://localhost:8080/other/",
            "Content-Type": JSON_CONTENT_TYPE,
        },
        client_address="127.0.0.1",
        body=RequestBody.of(body if method == "POST" else b""),
    )

    response = local_server.router.dispatch(sent)

    assert response.status == 403
    assert "'other', which is not trusted" in loads(response.body)["detail"]
    assert _published(queues) == []


@mark.parametrize("method", ["GET", "POST"])
def test_another_client_or_sites_page_is_refused_imports(
    local_server: LibranetHTTPServer, local_connection: HTTPConnection, method: str
) -> None:
    # The live server only ever sees loopback clients, so the remote request
    # is put to the router directly.
    remote = local_server.router.dispatch(
        Request(method, "/data/imports", client_address="203.0.113.42")
    )
    cross_site, _ = _get(
        local_connection, "/data/imports", method, headers={"Sec-Fetch-Site": "cross-site"}
    )

    assert remote.status == 403
    assert cross_site.status == 403


def test_a_local_client_makes_a_bundle_stored_as_an_upload_from_this_node(
    local_server: LibranetHTTPServer,
    local_connection: HTTPConnection,
    storage: StorageConfig,
    queues: ModuleQueues,
) -> None:
    local_connection.request(
        "POST",
        "/data/bundles",
        body=b'{"add": {"info.json": {"text": "{}"}}}',
        headers={"Content-Type": JSON_CONTENT_TYPE, **_from_page(local_connection, "/movie/")},
    )
    response = local_connection.getresponse()
    made = ContentId.parse(loads(response.read())["bundle"])
    # The live server only ever sees loopback clients, so the remote request
    # is put to the router directly.
    remote = local_server.router.dispatch(
        Request("POST", "/data/bundles", client_address="203.0.113.42")
    )

    # Not taken for /data/{algorithm}/{hash}, whose pattern it also fits.
    assert response.status == 201
    assert response.getheader("Location") == f"/data/{made}/"
    assert CasStore.for_node(storage, SERVER_IDENTITY.node_id).exists(made)
    assert {**made.fields(), "node_id": str(SERVER_IDENTITY.node_id)} in [
        {key: message[key] for key in ("algorithm", "hash", "node_id")}
        for message in _published(queues)
        if message["event"] == EventType.PUT_COMPLETED
    ]
    assert remote.status == 403


def test_bundles_are_made_only_by_a_node_that_knows_its_id(
    storage: StorageConfig, queues: ModuleQueues
) -> None:
    router = build_router(
        storage,
        RETRY_AFTER_SECONDS,
        StubModule(ModuleName.WEBSERVER, queues).publish,
        RequestAuthenticator.of(LibranetConfig(storage=storage)),
        allow_unsigned_api_reads=True,
        config_port=8180,
    )

    response = router.dispatch(
        Request("POST", "/data/bundles", client_address="127.0.0.1", body=RequestBody.of(b"{}"))
    )

    assert response.status == 404


def test_a_local_client_keeps_a_value_in_an_applications_store(
    local_server: LibranetHTTPServer, local_connection: HTTPConnection, storage: StorageConfig
) -> None:
    local_connection.request(
        "PUT",
        "/data/store/Movie/last",
        body=b'{"name": "Family"}',
        headers={"Content-Type": JSON_CONTENT_TYPE, **_from_page(local_connection, "/Movie/")},
    )
    put = local_connection.getresponse()
    put.read()
    page = _from_page(local_connection, "/movie/index.html")
    read, body = _get(local_connection, "/data/store/movie/last", headers=page)
    listed, listing = _get(local_connection, "/data/store/movie", headers=page)
    other, _ = _get(
        local_connection, "/data/store/movie", headers=_from_page(local_connection, "/")
    )
    # The live server only ever sees loopback clients, so the remote requests
    # are put to the router directly.
    remote = local_server.router.dispatch(
        Request("GET", "/data/store/movie/last", headers=MOVIE_PAGE, client_address="203.0.113.42")
    )
    remote_put = local_server.router.dispatch(
        Request("PUT", "/data/store/movie/last", headers=MOVIE_PAGE, client_address="203.0.113.42")
    )

    # Not taken for /data/{algorithm}/{hash}, whose pattern the store's fits.
    assert put.status == 201
    assert read.status == 200
    assert read.getheader("ETag") == StoredValue.of({"name": "Family"}).tag()
    assert loads(body) == {"name": "Family"}
    assert listed.status == 200
    assert loads(listing) == {"values": {"last": {"name": "Family"}}}
    assert other.status == 403
    assert remote.status == 200
    assert remote.body == body
    assert remote_put.status == 403
    (saved,) = storage.application_stores_dir.iterdir()
    assert saved.name == f"{sha256(b'movie').hexdigest()}.json"


@mark.parametrize(
    "method, path, allowed",
    [
        ("PUT", "/data/store/movie", "GET"),
        ("DELETE", "/data/store/movie", "GET"),
        ("HEAD", "/data/store/movie", "GET"),
        ("PUT", "/data/directory/Movies", "GET"),
        ("PUT", "/data/search/ab", "GET"),
        ("PUT", "/data/nodes/x", None),
        ("PUT", "/data/seek/x", None),
        ("PUT", "/data/client/x", None),
        ("PUT", "/data/imports/x", None),
        ("PUT", "/data/bundles/x", None),
        ("GET", "/data/bundles/x", None),
        ("GET", "/data/bundles", "POST"),
        ("PUT", "/data/applications/x", None),
    ],
)
def test_a_path_beneath_another_data_endpoint_is_never_taken_for_content(
    local_server: LibranetHTTPServer,
    caplog: LogCaptureFixture,
    method: str,
    path: str,
    allowed: str | None,
) -> None:
    sent = Request(
        method,
        path,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        client_address="127.0.0.1",
        body=RequestBody.of(b"1"),
    )

    with caplog.at_level(WARNING):
        response = local_server.router.dispatch(sent)

    assert response.status == (404 if allowed is None else 405)
    assert response.headers.get("Allow") == allowed
    assert caplog.text == ""


def test_a_remote_client_is_told_it_is_not_local(local_server: LibranetHTTPServer) -> None:
    response = local_server.router.dispatch(
        Request("GET", "/data/client", headers=MOVIE_PAGE, client_address="203.0.113.42")
    )

    assert response.status == 200
    assert loads(response.body) == {"local": False}


@mark.parametrize("applications", [{"myapp": APP_BUNDLE_ID}])
def test_any_client_is_told_which_applications_are_served_as_registered(
    server: LibranetHTTPServer, connection: HTTPConnection, config_connection: HTTPConnection
) -> None:
    # The live server only ever sees loopback clients, so the remote request
    # is put to the router directly.
    page = {"Host": "localhost:8080", "Referer": "http://localhost:8080/myapp/"}
    remote = server.router.dispatch(
        Request("GET", "/data/applications", headers=page, client_address="203.0.113.42")
    )
    registered, _ = _config(
        config_connection,
        "/config/api/applications",
        "POST",
        _credentials(),
        dumps({"name": "Wiki", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"),
    )
    listed, body = _get(connection, "/data/applications", headers=_from_page(connection, "/wiki/"))
    unnamed, _ = _get(connection, "/data/applications")

    assert remote.status == 200
    assert loads(remote.body) == {"applications": {"myapp": str(APP_BUNDLE_ID)}, "trusted": []}
    assert registered.status == 200
    assert listed.status == 200
    assert listed.getheader("Content-Type") == JSON_CONTENT_TYPE
    assert loads(body) == {
        "applications": {"myapp": str(APP_BUNDLE_ID), "wiki": str(APP_BUNDLE_ID)},
        "trusted": [],
    }
    assert unnamed.status == 403


@mark.parametrize(
    "client_address, headers, status",
    [
        ("203.0.113.42", _credentials(), 403),
        ("127.0.0.1", {"Host": "localhost:8180", "Referer": "http://localhost:8180/config/"}, 401),
    ],
)
def test_the_registry_is_changed_only_by_an_authenticated_local_client(
    config_server: LibranetHTTPServer,
    registry: ApplicationRegistry,
    client_address: str,
    headers: dict[str, str],
    status: int,
) -> None:
    # The live server only ever sees loopback clients, so the requests are put
    # to the router directly.
    body = RequestBody.of(dumps({"name": "wiki", "bundle": str(APP_BUNDLE_ID)}).encode("utf-8"))
    response = config_server.router.dispatch(
        Request(
            "POST",
            "/config/api/applications",
            headers=headers,
            client_address=client_address,
            body=body,
        )
    )

    assert response.status == status
    assert not body.consumed
    assert not registry.path.exists()


def test_a_registry_that_cannot_be_read_leaves_the_rest_of_the_node_served(
    connection: HTTPConnection, config_connection: HTTPConnection, registry: ApplicationRegistry
) -> None:
    write_atomically(registry.path, b"{not json")

    data, _ = _get(connection, f"/data/{CONTENT_ID}")
    application, _ = _get(connection, "/wiki/")
    index, _ = _config(config_connection, "/config/api", headers=_credentials())
    listing, body = _config(config_connection, "/config/api/applications", headers=_credentials())
    listed, problem = _get(connection, "/data/applications", headers=_from_page(connection, "/"))

    assert data.status == 200
    assert application.status == 500
    # Any client may list the applications, and is not told where the file is.
    assert listed.status == 500
    assert str(registry.path) not in loads(problem)["detail"]
    assert index.status == 200
    assert listing.status == 500
    assert str(registry.path) in loads(body)["detail"]


def test_the_main_port_sends_a_config_page_to_configs_port(
    server: LibranetHTTPServer, config_server: LibranetHTTPServer, connection: HTTPConnection
) -> None:
    redirect, _ = _get(connection, "/config/")
    location = redirect.getheader("Location") or ""
    host, port = config_server.server_address[:2]

    assert redirect.status == 302
    assert location == f"http://{str(host)}:{int(port)}/config/"
    assert redirect.getheader("WWW-Authenticate") is None

    # What is there is /config, which asks for its credential.
    followed = HTTPConnection(str(host), int(port), timeout=5)

    try:
        followed.request("GET", "/config/")
        challenge = followed.getresponse()
        challenge.read()

    finally:
        followed.close()

    assert challenge.status == 401
    assert challenge.getheader("WWW-Authenticate") == CONFIG_CHALLENGE
    assert server.server_port != int(port)


@mark.parametrize(
    "method, path",
    [("GET", "/config/api"), ("GET", "/config/api/backups"), ("POST", "/config/api/backups")],
)
def test_the_main_port_never_takes_the_config_credential(
    connection: HTTPConnection,
    credential: ConfigCredential,
    queues: ModuleQueues,
    method: str,
    path: str,
) -> None:
    response, body = _config(connection, path, method, _credentials(), b"{}")

    assert response.status == 404
    assert response.getheader("WWW-Authenticate") is None
    assert "/config is served on port" in loads(body)["detail"]
    assert not credential.captured
    assert _published(queues) == []


def test_the_main_port_refuses_config_to_a_remote_client(server: LibranetHTTPServer) -> None:
    # The live server only ever sees loopback clients, so the remote source
    # address is put to the router directly.
    response = server.router.dispatch(
        Request("GET", "/config/", headers=_credentials(), client_address="203.0.113.42")
    )

    assert response.status == 403
    assert "Location" not in response.headers


@mark.parametrize("applications", [{"wiki": APP_BUNDLE_ID}])
@mark.parametrize("path", ["/", f"/data/{CONTENT_ID}", "/data/nodes", "/wiki/"])
def test_configs_port_serves_nothing_but_config(
    config_connection: HTTPConnection, queues: ModuleQueues, path: str
) -> None:
    response, _ = _get(config_connection, path)

    assert response.status == 404
    assert _published(queues) == []


def _free_port() -> int:
    """A port nothing listens on, for now."""
    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@mark.parametrize(
    "held_at, bound_at",
    [("0.0.0.0", "127.0.0.1"), ("127.0.0.1", "0.0.0.0"), ("127.0.0.1", "127.0.0.1")],
)
def test_a_port_something_on_this_machine_answers_on_is_refused(
    held_at: str, bound_at: str
) -> None:
    with socket() as held:
        held.bind((held_at, 0))
        held.listen()
        port = int(held.getsockname()[1])

        with raises(OSError) as raised:
            LibranetHTTPServer(
                (bound_at, port),
                Router(),
                getLogger("test.webserver"),
                MessageSigner(SERVER_IDENTITY),
            )

    assert raised.value.errno == EADDRINUSE


def test_the_first_free_port_is_listened_on_and_each_taken_one_logged(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(WARNING)

    with socket() as first, socket() as second:
        for held in (first, second):
            held.bind(("127.0.0.1", 0))
            held.listen()

        taken = [int(held.getsockname()[1]) for held in (first, second)]
        free = _free_port()
        server = LibranetHTTPServer.first_free(
            "127.0.0.1",
            [*taken, free],
            Router(),
            getLogger("test.webserver"),
            MessageSigner(SERVER_IDENTITY),
        )
        server.server_close()

    assert server.server_port == free
    assert [record.getMessage().split(",")[0] for record in caplog.records] == [
        f"Cannot listen on port {port}" for port in taken
    ]


def test_with_every_port_taken_none_is_listened_on() -> None:
    with socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = int(held.getsockname()[1])

        with raises(OSError) as raised:
            LibranetHTTPServer.first_free(
                "127.0.0.1",
                [port],
                Router(),
                getLogger("test.webserver"),
                MessageSigner(SERVER_IDENTITY),
            )

    assert raised.value.errno == EADDRINUSE


def test_an_address_that_cannot_be_listened_at_stops_the_search_at_once(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(WARNING)

    with raises(OSError) as raised:
        LibranetHTTPServer.first_free(
            "256.0.0.1",
            [_free_port(), _free_port()],
            Router(),
            getLogger("test.webserver"),
            MessageSigner(SERVER_IDENTITY),
        )

    assert raised.value.errno != EADDRINUSE
    assert caplog.records == []


def test_an_invalid_content_id_is_logged_at_debug(
    connection: HTTPConnection, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    _get(connection, "/data/sha256/xyz")

    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.data_handler"]
    assert record.levelno == DEBUG
    assert record.getMessage().startswith("Refusing GET /data/sha256/xyz: ")


def test_missing_content_is_logged_at_debug(
    connection: HTTPConnection, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    _get(connection, f"/data/{MISSING_ID}")

    assert [
        (level, message)
        for name, level, message in caplog.record_tuples
        if name == "libranet.webserver.data_handler"
    ] == [(DEBUG, f"{MISSING_ID} is not held here, so it is asked for")]


def test_an_invalid_search_prefix_is_logged_at_debug(
    connection: HTTPConnection, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(DEBUG)

    _get(connection, "/data/search/nothex")

    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.search_handler"]
    assert record.levelno == DEBUG
    assert record.getMessage().startswith("Refusing GET /data/search/nothex: ")


def test_a_content_id_under_an_unsupported_algorithm_is_logged_as_a_warning(
    connection: HTTPConnection, caplog: LogCaptureFixture
) -> None:
    path = "/data/md5/" + "0" * 32
    response, _ = _get(connection, path)

    assert response.status == 400
    (record,) = [r for r in caplog.records if r.name == "libranet.webserver.data_handler"]
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Refusing GET {path}: ")
