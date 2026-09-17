"""End-to-end tests of the read path against a live server and a temp CAS.

Published messages land on a plain in-process queue, so no dispatcher runs.
"""

from __future__ import annotations
from http.client import HTTPConnection, HTTPResponse
from json import loads
from logging import getLogger
from pathlib import Path
from queue import Empty, Queue
from socket import create_connection
from threading import Thread
from typing import Iterator

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_CONTENT_ADDRESS, PROBLEM_CONTENT_TYPE
from libranet.supervision.stubs import StubModule
from libranet.webserver.http_types import Request, Response
from libranet.webserver.server import LibranetHTTPServer, build_router

CONTENT = b"hello libranet"
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
MISSING_ID = ContentId.for_data(b"not stored", "sha256")
RETRY_AFTER_SECONDS = 7


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def store(storage: StorageConfig) -> CasStore:
    store = source_of_truth_store(storage)
    store.write(CONTENT_ID, CONTENT)
    return store


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def server(
    storage: StorageConfig, store: CasStore, queues: ModuleQueues
) -> Iterator[LibranetHTTPServer]:
    publisher = StubModule(ModuleName.WEBSERVER, queues)
    server = LibranetHTTPServer(
        ("127.0.0.1", 0),
        build_router(storage, RETRY_AFTER_SECONDS, publisher.publish),
        getLogger("test.webserver"),
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
    assert _published(queues) == []


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

    (message,) = _published(queues)
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
    connection.request("PUT", f"/data/{CONTENT_ID}", body=b"data")
    response = connection.getresponse()
    body = response.read()

    assert response.status == 405
    assert response.getheader("Allow") == "GET"
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

    server.router.add("GET", "/boom", explode)
    host, port = server.server_address[:2]
    connection = HTTPConnection(str(host), int(port), timeout=5)

    try:
        response, body = _get(connection, "/boom")

    finally:
        connection.close()

    assert response.status == 500
    assert loads(body) == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
        "instance": "/boom",
    }
