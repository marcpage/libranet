"""Tests for making a drop of a page's own content."""

from __future__ import annotations
from base64 import b64encode
from hashlib import sha256
from json import dumps, loads
from logging import ERROR, WARNING
from dataclasses import replace
from pathlib import Path
from threading import Thread
from typing import Any, Mapping, cast
from zlib import decompress

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import IncorrectKeyError, IncorrectPasswordError
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.protection import PasswordKey, strip_targeting
from libranet.bundle.storing import store_object
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DROP_SEPARATOR, DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_CONFIG_HOSTS, StorageConfig
from libranet.identity.people import PersonKey
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_TOO_LARGE, CONTENT_UNAVAILABLE, INVALID_REQUEST
from libranet.protocol.drop_requests import DropRequest, Encryption
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.app_registry import (
    ROOT_APPLICATION,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DROP_PATH, DropEncryption, DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.own_pages import OwnPages
from libranet.webserver.site_checks import SiteChecks

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 4096
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 12
TARGET = "user:alice"
RETRY_AFTER_SECONDS = 3
APP_BUNDLE = ContentId.for_data(b"an application's bundle", "sha256")
PASSWORD = "correct horse"
# A page of the trusted movie application, as a local client's browser names it.
MOVIE_PAGE = {"Host": "localhost:8080", "Referer": "http://localhost:8080/movie/"}


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


@fixture(scope="module")
def person() -> PersonKey:
    return PersonKey.generate(2048)


@fixture
def encryption(
    storage: StorageConfig, uploads: OwnUploads, recorder: Recorder, tmp_path: Path
) -> DropEncryption:
    """What encrypts drops, for pages of a trusted ``movie`` and an untrusted ``other``."""
    applications = RegisteredApplications(
        dict.fromkeys((ROOT_APPLICATION, "movie", "other"), APP_BUNDLE),
        frozenset({ROOT_APPLICATION, "movie"}),
    )
    return DropEncryption(
        uploads,
        recorder,
        OwnPages(ApplicationRegistry(tmp_path / "applications.json", applications)),
        SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint"),
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )


@fixture
def encrypting(uploads: OwnUploads, encryption: DropEncryption) -> DropHandler:
    return DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS, encryption=encryption)


def drop_request(
    value: object,
    *,
    content_type: str = JSON_CONTENT_TYPE,
    client_address: str = "203.0.113.42",
    headers: dict[str, str] | None = None,
) -> Request:
    """``POST /data/drop`` carrying ``value`` as JSON, from ``client_address``, with ``headers``."""
    return Request(
        "POST",
        DROP_PATH,
        headers={"Content-Type": content_type, **(headers or {})},
        client_address=client_address,
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
    assert problem_type(response) == INVALID_REQUEST
    assert loads(response.body)["detail"] == complaint
    assert recorder.messages == []


def test_a_body_that_is_no_drop_is_400(handler: DropHandler) -> None:
    response = handler(drop_request({"target": TARGET, "seconds": 0}))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST


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


def encrypted(content: bytes, encrypt: object, minimum_bits: int = 0) -> dict[str, object]:
    """A drop of ``content``, asking for it to be encrypted as ``encrypt`` says."""
    return {**asking(content, 0, minimum_bits), "encrypt": encrypt}


def by_password(username: str = "alice", password: str = PASSWORD) -> dict[str, str]:
    return {"username": username, "password": password}


def test_a_drop_encrypted_for_a_person_is_opened_by_their_key_alone(
    encrypting: DropHandler, uploads: OwnUploads, storage: StorageConfig, person: PersonKey
) -> None:
    store_object(person.public_key, uploads, MAX_BYTES)
    content = b"for alice's eyes only"

    content_id = made(
        encrypting(drop_request(encrypted(content, {"for": str(person.person_id)}, 4)))
    )

    stored = uploaded(storage, content_id)
    block = EncryptedForPerson.decoded(strip_targeting(stored))
    assert content not in stored
    assert block.decrypted(person.private_key, MAX_BYTES) == content
    assert matching_bits(content_id.hash, DropTarget.of(TARGET).hex) >= 4

    with raises(IncorrectKeyError):
        block.decrypted(PersonKey.generate(2048).private_key, MAX_BYTES)


def test_a_persons_key_not_held_is_asked_for_and_503_before_any_drop(
    encrypting: DropHandler, recorder: Recorder, person: PersonKey
) -> None:
    response = encrypting(drop_request(encrypted(b"x", {"for": str(person.person_id)})))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [person.person_id.fields()]
    assert not recorder.payloads(EventType.PUT_COMPLETED)


def test_a_blocked_persons_key_is_404_and_not_asked_for(
    uploads: OwnUploads,
    storage: StorageConfig,
    recorder: Recorder,
    encryption: DropEncryption,
    person: PersonKey,
) -> None:
    write_atomically(storage.blocked_list_path, BlockedContent.body([person.person_id]))
    blocking = DropEncryption(
        encryption.content,
        encryption.publish,
        encryption.pages,
        encryption.checks,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )
    handler = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS, encryption=blocking)

    response = handler(drop_request(encrypted(b"x", {"for": str(person.person_id)})))

    assert response.status == 404
    assert recorder.messages == []


def test_an_id_naming_no_persons_key_is_400(
    encrypting: DropHandler, uploads: OwnUploads, recorder: Recorder
) -> None:
    not_a_key = store_object(b"not a key", uploads, MAX_BYTES)
    recorder.messages.clear()

    response = encrypting(drop_request(encrypted(b"x", {"for": str(not_a_key)})))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert loads(response.body)["detail"] == f'"for", {not_a_key}, names no public key'
    assert recorder.messages == []


def test_a_persons_key_held_that_is_not_it_is_500_and_a_warning(
    encrypting: DropHandler, storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    person_id = ContentId.for_data(b"what it should be", "sha256")
    CasStore.source_of_truth(storage).write(person_id, b"damaged on disk")

    with caplog.at_level(WARNING):
        response = encrypting(drop_request(encrypted(b"x", {"for": str(person_id)})))

    assert response.status == 500
    assert f"What is held for {person_id} is not it" in caplog.text


def test_a_drop_encrypted_by_a_password_is_opened_by_it_alone(
    encrypting: DropHandler, storage: StorageConfig
) -> None:
    content = b"notes to myself"
    request = drop_request(
        encrypted(content, by_password(" Alice ")), client_address="127.0.0.1", headers=MOVIE_PAGE
    )

    content_id = made(encrypting(request))

    stored = strip_targeting(uploaded(storage, content_id))
    assert content not in stored
    # A random IV, named in the descriptor.
    assert b"\0PW-ARGON2ID-AES256-CBC-IV:" in stored
    assert PasswordKey.of_user("alice", PASSWORD).unprotect(stored, MAX_BYTES) == content

    with raises(IncorrectPasswordError):
        PasswordKey.of_user("alice", "another password").unprotect(stored, MAX_BYTES)


@mark.parametrize(
    "client_address, headers",
    [
        ("203.0.113.42", MOVIE_PAGE),
        ("127.0.0.1", {**MOVIE_PAGE, "Host": "evil.example"}),
        ("127.0.0.1", {**MOVIE_PAGE, "Referer": "http://localhost:8080/other/"}),
        ("127.0.0.1", {"Host": "localhost:8080"}),
    ],
)
def test_only_a_local_clients_trusted_page_encrypts_by_a_password(
    encrypting: DropHandler, recorder: Recorder, client_address: str, headers: dict[str, str]
) -> None:
    request = drop_request(
        encrypted(b"x", by_password()), client_address=client_address, headers=headers
    )

    response = encrypting(request)

    assert response.status == 403
    assert recorder.messages == []


@mark.parametrize("encrypt", ["person", "password"])
def test_content_that_does_not_fit_once_encrypted_is_413(
    encrypting: DropHandler, uploads: OwnUploads, person: PersonKey, encrypt: str
) -> None:
    store_object(person.public_key, uploads, MAX_BYTES)
    asked = {"for": str(person.person_id)} if encrypt == "person" else by_password()
    # It fits as it is, but not once encrypted.
    content = incompressible(MAX_BYTES - len(DROP_SEPARATOR))

    response = encrypting(
        drop_request(encrypted(content, asked), client_address="127.0.0.1", headers=MOVIE_PAGE)
    )

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE


def test_a_drop_handler_that_encrypts_nothing_refuses_to(
    handler: DropHandler, recorder: Recorder, person: PersonKey
) -> None:
    response = handler(drop_request(encrypted(b"x", {"for": str(person.person_id)})))

    assert response.status == 400
    assert loads(response.body)["detail"] == '"encrypt" is not served here'
    assert recorder.messages == []


def test_an_encryption_no_check_expects_is_500_and_an_error(
    encryption: DropEncryption, caplog: LogCaptureFixture
) -> None:
    asked = DropRequest(DropTarget.of(TARGET), b"x", 0, 0, cast(Encryption, "rot13"))

    with caplog.at_level(ERROR):
        response = encryption.encrypted(drop_request({}), asked, max_object_bytes=MAX_BYTES)

    assert isinstance(response, Response)
    assert response.status == 500
    assert "it asks for 'rot13', which is no encryption known" in caplog.text


def test_a_key_is_derived_in_its_turn(encrypting: DropHandler, encryption: DropEncryption) -> None:
    answers: list[Response] = []
    request = drop_request(
        encrypted(b"waits", by_password()), client_address="127.0.0.1", headers=MOVIE_PAGE
    )
    waiting = Thread(target=lambda: answers.append(encrypting(request)))

    with encryption.derivations.turn():
        waiting.start()
        waiting.join(timeout=0.2)

        assert waiting.is_alive()
        assert answers == []

    waiting.join(timeout=10)
    assert not waiting.is_alive()
    made(answers[0])


def test_an_encryption_refuses_a_negative_retry_after(encryption: DropEncryption) -> None:
    with raises(ValueError, match="retry_after_seconds"):
        replace(encryption, retry_after_seconds=-1)
