"""Tests for making a drop of a page's own content."""

from __future__ import annotations
from base64 import b64encode
from hashlib import sha256
from json import dumps, loads
from threading import Thread
from typing import Any, Mapping
from zlib import decompress

from pytest import fixture, mark, raises

from libranet.bundle.protection import strip_targeting
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR, DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_TOO_LARGE, INVALID_CONFIG_REQUEST
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DROP_PATH, DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 4096
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 12
TARGET = "user:alice"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def uploads(storage: StorageConfig, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, CasStore.source_of_truth(storage), NODE_ID, recorder)


@fixture
def handler(uploads: OwnUploads) -> DropHandler:
    return DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)


def drop_request(value: object, *, content_type: str = JSON_CONTENT_TYPE) -> Request:
    """``POST /data/drop`` carrying ``value`` as JSON."""
    return Request(
        "POST",
        DROP_PATH,
        headers={"Content-Type": content_type},
        client_address="203.0.113.42",
        body=RequestBody.of(dumps(value).encode("utf-8")),
    )


def asking(content: bytes, seconds: float = 0, minimum_bits: int = 0) -> dict[str, object]:
    return {
        "target": TARGET,
        "base64": b64encode(content).decode("ascii"),
        "seconds": seconds,
        "minimum_bits": minimum_bits,
    }


def incompressible(size_bytes: int) -> bytes:
    """``size_bytes`` that zlib cannot shrink."""
    blocks = (sha256(b"%d" % number).digest() for number in range(size_bytes // 32 + 1))
    return b"".join(blocks)[:size_bytes]


def made(response: Response) -> ContentId:
    """What ``response`` says names the drop made, checking how it says it."""
    assert response.status == 201, response.body
    assert response.headers["Content-Type"] == JSON_CONTENT_TYPE
    answer = loads(response.body)
    content_id = ContentId.parse(answer["id"])
    assert response.headers["Location"] == f"/data/{content_id}"
    assert answer["target"] == DropTarget.of(TARGET).hex
    return content_id


def uploaded(storage: StorageConfig, content_id: ContentId) -> bytes:
    """The drop uploaded from this node as ``content_id``, decompressed if need be."""
    stored = CasStore.for_node(storage, NODE_ID).read(content_id)
    return stored if content_id.matches(stored) else decompress(stored)


def test_a_drop_is_uploaded_from_this_node_and_announced(
    handler: DropHandler, storage: StorageConfig, recorder: Recorder
) -> None:
    response = handler(drop_request({"target": TARGET, "text": "hello", "seconds": 0.05}))

    content_id = made(response)
    data = uploaded(storage, content_id)
    assert strip_targeting(data) == b"hello"
    assert data.startswith(b"hello" + DROP_SEPARATOR)
    assert loads(response.body)["matching_bits"] == matching_bits(
        content_id.hash, DropTarget.of(TARGET).hex
    )
    assert recorder.payloads(EventType.PUT_COMPLETED) == [
        {**content_id.fields(), "node_id": str(NODE_ID)}
    ]


def test_a_drop_matches_the_minimum_asked_for(handler: DropHandler) -> None:
    response = handler(drop_request(asking(b"content", 0, MAX_MINIMUM_BITS)))

    made(response)
    assert loads(response.body)["matching_bits"] >= MAX_MINIMUM_BITS


def test_a_drop_larger_than_an_object_is_stored_compressed(
    handler: DropHandler, storage: StorageConfig
) -> None:
    content = b"a" * (MAX_BYTES * 2)

    content_id = made(handler(drop_request(asking(content))))

    stored = CasStore.for_node(storage, NODE_ID).read(content_id)
    assert len(stored) <= MAX_BYTES
    assert strip_targeting(decompress(stored)) == content


@mark.parametrize("size_bytes", [MAX_BYTES, MAX_BYTES * 2])
def test_content_that_does_not_fit_even_compressed_is_413_before_any_search(
    handler: DropHandler, recorder: Recorder, size_bytes: int
) -> None:
    # Asking for every bit a hash has would never end, were it searched for.
    handler = DropHandler(handler.uploads, MAX_BYTES, MAX_SECONDS, 256)

    response = handler(drop_request(asking(incompressible(size_bytes), 0, 256)))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert loads(response.body)["max_bytes"] == MAX_BYTES
    assert recorder.messages == []


@mark.parametrize(
    "seconds, minimum_bits, complaint",
    [
        (MAX_SECONDS + 0.5, 0, '"seconds" may be at most 1.0 here, got 1.5'),
        (0, MAX_MINIMUM_BITS + 1, '"minimum_bits" may be at most 12 here, got 13'),
    ],
)
def test_a_search_past_a_ceiling_is_400_before_it_starts(
    handler: DropHandler, recorder: Recorder, seconds: float, minimum_bits: int, complaint: str
) -> None:
    response = handler(drop_request(asking(b"content", seconds, minimum_bits)))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert loads(response.body)["detail"] == complaint
    assert recorder.messages == []


def test_a_body_that_is_no_drop_is_400(handler: DropHandler) -> None:
    response = handler(drop_request({"target": TARGET, "seconds": 0}))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST


def test_a_body_that_does_not_say_it_is_json_is_415(handler: DropHandler) -> None:
    assert handler(drop_request(asking(b"x"), content_type="text/plain")).status == 415


def test_a_request_waits_its_turn_while_another_is_searched_for(handler: DropHandler) -> None:
    answers: list[Response] = []
    waiting = Thread(target=lambda: answers.append(handler(drop_request(asking(b"waits")))))

    with handler.turns.turn():
        waiting.start()
        waiting.join(timeout=0.2)

        assert waiting.is_alive()
        assert answers == []

    waiting.join(timeout=10)
    assert not waiting.is_alive()
    made(answers[0])


@mark.parametrize(
    "max_seconds, max_minimum_bits, complaint",
    [(-1.0, 0, "max_seconds"), (0.0, -1, "max_minimum_bits"), (0.0, 257, "max_minimum_bits")],
)
def test_impossible_ceilings_are_refused(
    uploads: OwnUploads, max_seconds: float, max_minimum_bits: int, complaint: str
) -> None:
    with raises(ValueError, match=complaint):
        DropHandler(uploads, MAX_BYTES, max_seconds, max_minimum_bits)
