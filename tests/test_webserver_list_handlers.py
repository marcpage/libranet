"""Tests for the node and seek list handlers, called through a router without a server.

A request reaches these handlers after the server's signature guard, so a
signed request is built here with the outcome the guard would have attached.
"""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from queue import Empty, Queue
from zlib import compress

from pytest import fixture, mark

from libranet.cas.content_id import ContentId
from libranet.identity.authentication import AuthenticationResult, AuthenticationStatus
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.problems import (
    CONTENT_TOO_LARGE,
    INVALID_LIST,
    PROBLEM_CONTENT_TYPE,
    SIGNATURE_REQUIRED,
)
from libranet.supervision.stubs import StubModule
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.list_handlers import (
    NODES_PATH,
    SEEK_PATH,
    ListFileHandler,
    NodeListHandler,
    SeekListHandler,
)
from libranet.webserver.router import Router

MAX_BYTES = 512
RETRY_AFTER_SECONDS = 9
PEER_ADDRESS = "203.0.113.42"
SENDER_ID = ContentId.for_data(b"the sender's public key", "sha256")
OTHER_ID = ContentId.for_data(b"another node's public key", "sha256")
SOUGHT_ID = ContentId.for_data(b"sought content", "sha256")
VERIFIED = AuthenticationResult(AuthenticationStatus.VERIFIED, SENDER_ID)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def node_list_path(tmp_path: Path) -> Path:
    return tmp_path / "lists" / "nodes.json"


@fixture
def seek_list_path(tmp_path: Path) -> Path:
    return tmp_path / "lists" / "seek.json"


@fixture
def router(node_list_path: Path, seek_list_path: Path, queues: ModuleQueues) -> Router:
    publish = StubModule(ModuleName.WEBSERVER, queues).publish
    router = Router()
    router.add("GET", NODES_PATH, ListFileHandler(node_list_path, RETRY_AFTER_SECONDS))
    router.add("POST", NODES_PATH, NodeListHandler(MAX_BYTES, publish))
    router.add("GET", SEEK_PATH, ListFileHandler(seek_list_path, RETRY_AFTER_SECONDS))
    router.add("POST", SEEK_PATH, SeekListHandler(MAX_BYTES, publish))
    return router


def post(
    path: str,
    value: object,
    authentication: AuthenticationResult | None = VERIFIED,
    *,
    compressed: bool = False,
) -> Request:
    """A POST of ``value`` as JSON from :data:`PEER_ADDRESS`."""
    body = dumps(value).encode("utf-8")
    return Request(
        "POST",
        path,
        client_address=PEER_ADDRESS,
        body=RequestBody.of(compress(body) if compressed else body),
        authentication=authentication,
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def problem_type(response: Response) -> str:
    assert response.headers["Content-Type"] == PROBLEM_CONTENT_TYPE
    problem = loads(response.body)
    assert problem["status"] == response.status
    return str(problem["type"])


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
def test_derived_lists_are_served_as_written(
    router: Router, node_list_path: Path, seek_list_path: Path, path: str
) -> None:
    node_list_path.parent.mkdir()
    node_list_path.write_bytes(b'{"nodes":{"http://localhost:8080":"sha256/abc"}}')
    seek_list_path.write_bytes(b'{"data":[],"search":["ab"]}')
    expected = node_list_path if path == NODES_PATH else seek_list_path

    response = router.dispatch(Request("GET", path))

    assert response.status == 200
    assert response.headers["Content-Type"] == "application/json"
    assert response.body == expected.read_bytes()


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
def test_a_list_not_derived_yet_is_503(router: Router, path: str) -> None:
    response = router.dispatch(Request("GET", path))

    assert response.status == 503
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert problem_type(response) == "about:blank"
    assert loads(response.body)["instance"] == path


def test_a_node_list_is_published_with_localhost_resolved(
    router: Router, queues: ModuleQueues
) -> None:
    request = post(
        NODES_PATH,
        {
            "nodes": {
                "http://localhost:4300": str(SENDER_ID),
                "https://libranet.example.org:443": str(OTHER_ID),
            }
        },
    )

    response = router.dispatch(request)

    assert response.status == 202
    assert response.body == b""
    assert not response.close
    (message,) = published(queues)
    assert message["event"] == EventType.NODES_RECEIVED
    assert message["source"] == ModuleName.WEBSERVER
    assert message["nodes"] == {
        "http://203.0.113.42:4300": str(SENDER_ID),
        "https://libranet.example.org:443": str(OTHER_ID),
    }


def test_unusable_node_list_entries_are_dropped(router: Router, queues: ModuleQueues) -> None:
    request = post(
        NODES_PATH,
        {
            "nodes": {
                "ftp://192.0.2.9:21": str(OTHER_ID),
                "http://192.0.2.10:80": 12,
                "http://192.0.2.11:80": str(OTHER_ID),
            }
        },
    )

    assert router.dispatch(request).status == 202
    assert published(queues)[0]["nodes"] == {"http://192.0.2.11:80": str(OTHER_ID)}


def test_a_compressed_node_list_is_accepted(router: Router, queues: ModuleQueues) -> None:
    request = post(NODES_PATH, {"nodes": {"http://localhost": str(SENDER_ID)}}, compressed=True)

    assert router.dispatch(request).status == 202
    assert published(queues)[0]["nodes"] == {"http://203.0.113.42": str(SENDER_ID)}


def test_a_provisionally_trusted_sender_may_post(router: Router, queues: ModuleQueues) -> None:
    provisional = AuthenticationResult(AuthenticationStatus.PROVISIONAL, SENDER_ID)

    response = router.dispatch(post(NODES_PATH, {"nodes": {}}, provisional))

    assert response.status == 202
    assert published(queues)[0]["nodes"] == {}


def test_a_seek_list_is_published_for_its_signer(router: Router, queues: ModuleQueues) -> None:
    request = post(
        SEEK_PATH,
        {"data": [f"SHA256/{SOUGHT_ID.hash.upper()}", "sha256/nope"], "search": ["ABCD", "xyz"]},
    )

    response = router.dispatch(request)

    assert response.status == 202
    assert response.body == b""
    (message,) = published(queues)
    assert message["event"] == EventType.SEEK_RECEIVED
    assert message["source"] == ModuleName.WEBSERVER
    assert message["node_id"] == str(SENDER_ID)
    assert message["data"] == [str(SOUGHT_ID)]
    assert message["search"] == ["abcd"]


def test_a_compressed_seek_list_is_accepted(router: Router, queues: ModuleQueues) -> None:
    request = post(SEEK_PATH, {"search": ["ab"]}, compressed=True)

    assert router.dispatch(request).status == 202
    assert published(queues)[0]["search"] == ["ab"]


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
@mark.parametrize(
    "authentication", [None, AuthenticationResult(AuthenticationStatus.UNAUTHENTICATED)]
)
def test_unsigned_posts_are_refused_and_keep_the_connection(
    router: Router,
    queues: ModuleQueues,
    path: str,
    authentication: AuthenticationResult | None,
) -> None:
    request = post(path, {"nodes": {}, "data": []}, authentication)

    response = router.dispatch(request)

    assert response.status == 401
    assert problem_type(response) == SIGNATURE_REQUIRED
    assert not response.close
    assert request.body.consumed
    assert published(queues) == []


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
def test_oversized_posts_are_refused_unread(
    router: Router, queues: ModuleQueues, path: str
) -> None:
    request = Request(
        "POST",
        path,
        client_address=PEER_ADDRESS,
        body=RequestBody.of(b" " * (MAX_BYTES + 1)),
        authentication=VERIFIED,
    )

    response = router.dispatch(request)

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert not request.body.consumed
    assert published(queues) == []


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
@mark.parametrize("value", [[], {"nodes": [], "data": {}}])
def test_misshapen_lists_are_400(
    router: Router, queues: ModuleQueues, path: str, value: object
) -> None:
    response = router.dispatch(post(path, value))

    assert response.status == 400
    assert problem_type(response) == INVALID_LIST
    assert loads(response.body)["instance"] == path
    assert published(queues) == []


@mark.parametrize("path", [NODES_PATH, SEEK_PATH])
def test_a_body_that_is_not_json_is_400(router: Router, queues: ModuleQueues, path: str) -> None:
    request = Request("POST", path, body=RequestBody.of(b"not a list"), authentication=VERIFIED)

    response = router.dispatch(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_LIST
    assert published(queues) == []
