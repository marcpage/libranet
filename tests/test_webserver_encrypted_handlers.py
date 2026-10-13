"""Tests for opening blocks encrypted for whoever is signed in, or by a username and password."""

from __future__ import annotations
from base64 import b64decode
from json import dumps, loads
from logging import WARNING
from pathlib import Path
from threading import Thread
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.person_encryption import EncryptedForPerson
from libranet.bundle.protection import PasswordKey
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import store_object
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.store import CasStore
from libranet.config.models import DEFAULT_CONFIG_HOSTS, StorageConfig
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import (
    CONTENT_UNAVAILABLE,
    INVALID_BUNDLE_SIGNATURE,
    INVALID_CONTENT_ADDRESS,
    INVALID_REQUEST,
    SESSION_REQUIRED,
    UNUSABLE_BUNDLE,
    WRONG_KEY,
)
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.app_registry import (
    ROOT_APPLICATION,
    ApplicationRegistry,
    RegisteredApplications,
)
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropEncryption, DropHandler
from libranet.webserver.encrypted_handlers import ENCRYPTED_PATH, EncryptedDrops
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.own_pages import OwnPages
from libranet.webserver.sessions import Session, Sessions
from libranet.webserver.signed_handlers import SIGNED_PATH, SignedDrops
from libranet.webserver.site_checks import SiteChecks

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
APP_BUNDLE = ContentId.for_data(b"an application's bundle", "sha256")
MAX_BYTES = 64 * 1024
RETRY_AFTER_SECONDS = 3
TEXT = '{"subject":"hello","body":"for one person"}'
PASSWORD = "correct horse"
NOT_STORED = "no-store"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


@fixture(scope="module")
def alice() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def bob() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def alice_key() -> PasswordKey:
    """The key ``alice`` and ``PASSWORD`` derive, derived once for every test."""
    return PasswordKey.of_user("alice", PASSWORD)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def uploads(storage: StorageConfig, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, CasStore.source_of_truth(storage), NODE_ID, recorder)


@fixture
def sessions() -> Sessions:
    return Sessions(60.0)


@fixture
def signed(
    storage: StorageConfig,
    uploads: OwnUploads,
    sessions: Sessions,
    recorder: Recorder,
    tmp_path: Path,
) -> SignedDrops:
    """Signed drops, made encrypted as asked, as the main port makes them."""
    applications = RegisteredApplications(
        dict.fromkeys((ROOT_APPLICATION, "movie"), APP_BUNDLE), frozenset({"movie"})
    )
    encryption = DropEncryption(
        uploads,
        recorder,
        OwnPages(ApplicationRegistry(tmp_path / "applications.json", applications)),
        SiteChecks(DEFAULT_CONFIG_HOSTS, "This endpoint"),
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )
    return SignedDrops(
        uploads,
        DropHandler(uploads, MAX_BYTES, 1.0, 12, encryption=encryption),
        sessions,
        recorder,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )


@fixture
def encrypted(signed: SignedDrops) -> EncryptedDrops:
    return EncryptedDrops(signed)


def cookie(sessions: Sessions, person: PersonKey, username: str) -> dict[str, str]:
    """The ``Cookie`` header of a browser ``person`` is signed in on."""
    started = sessions.start(Session(Username.create(username), person))
    return {"Cookie": started.partition(";")[0]}


def opening(content_id: ContentId, headers: Mapping[str, str] | None = None) -> Request:
    """``GET /data/encrypted/{algorithm}/{hash}`` for ``content_id``, sending ``headers``."""
    return Request(
        "GET",
        f"{ENCRYPTED_PATH}/{content_id}",
        params=content_id.fields(),
        headers=dict(headers or {}),
        body=RequestBody.of(b""),
        client_address="127.0.0.1",
    )


def opening_by_password(
    content_id: ContentId, value: object, content_type: str = JSON_CONTENT_TYPE
) -> Request:
    """``POST /data/encrypted/{algorithm}/{hash}`` for ``content_id``, carrying ``value``."""
    return Request(
        "POST",
        f"{ENCRYPTED_PATH}/{content_id}",
        params=content_id.fields(),
        headers={"Content-Type": content_type},
        body=RequestBody.of(dumps(value).encode("utf-8")),
        client_address="127.0.0.1",
    )


def held_drop(uploads: OwnUploads, content: bytes) -> ContentId:
    """``content`` made a drop and stored among this node's uploads."""
    return store_object(DropTarget.of("mail:anyone").placed(content, 0, 0).data, uploads, MAX_BYTES)


def for_person(person: PersonKey, plaintext: bytes) -> bytes:
    """``plaintext`` encrypted for ``person``."""
    return EncryptedForPerson.encrypted(plaintext, person.private_key.public_key()).encoded()


def opened(response: Response) -> dict[str, Any]:
    """What ``response`` says was opened, checking that no cache keeps it."""
    assert response.status == 200, response.body
    assert response.headers["Cache-Control"] == NOT_STORED
    value = loads(response.body)
    assert isinstance(value, dict)
    return value


def test_no_one_signed_in_is_401_and_nothing_is_read(
    encrypted: EncryptedDrops, uploads: OwnUploads, alice: PersonKey, recorder: Recorder
) -> None:
    content_id = held_drop(uploads, for_person(alice, TEXT.encode()))

    response = encrypted.read(opening(content_id))

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert response.headers["Cache-Control"] == NOT_STORED
    assert not recorder.payloads(EventType.DATA_REQUESTED)


def test_a_block_encrypted_for_who_is_signed_in_opens_to_its_text(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    recorder: Recorder,
) -> None:
    content_id = held_drop(uploads, for_person(alice, TEXT.encode()))

    response = encrypted.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert opened(response) == {"id": str(content_id), "text": TEXT}
    assert recorder.payloads(EventType.DATA_REQUESTED) == [
        {**content_id.fields(), "external": False}
    ]


def test_what_is_not_text_opens_as_base64(
    encrypted: EncryptedDrops, uploads: OwnUploads, sessions: Sessions, alice: PersonKey
) -> None:
    data = bytes(range(256))
    content_id = held_drop(uploads, for_person(alice, data))

    value = opened(encrypted.read(opening(content_id, cookie(sessions, alice, "alice"))))

    assert set(value) == {"id", "base64"}
    assert b64decode(value["base64"]) == data


def test_a_block_encrypted_for_someone_else_is_403_wrong_key(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    bob: PersonKey,
) -> None:
    content_id = held_drop(uploads, for_person(bob, TEXT.encode()))

    response = encrypted.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert response.status == 403
    assert problem_type(response) == WRONG_KEY
    assert response.headers["Cache-Control"] == NOT_STORED


def test_what_one_signs_for_another_opens_for_them_alone_signed(
    signed: SignedDrops,
    encrypted: EncryptedDrops,
    sessions: Sessions,
    uploads: OwnUploads,
    alice: PersonKey,
    bob: PersonKey,
) -> None:
    store_object(bob.public_key, uploads, MAX_BYTES)
    alice_cookie = cookie(sessions, alice, "alice")
    value = {"target": f"mail:{bob.person_id}", "text": TEXT, "seconds": 0}
    value["encrypt"] = {"for": str(bob.person_id)}
    made = signed.make(
        Request(
            "POST",
            SIGNED_PATH,
            headers={"Content-Type": JSON_CONTENT_TYPE, **alice_cookie},
            body=RequestBody.of(dumps(value).encode("utf-8")),
        )
    )
    assert made.status == 201, made.body
    drop_id = ContentId.parse(loads(made.body)["id"])

    for_bob = encrypted.read(opening(drop_id, cookie(sessions, bob, "bob")))
    for_alice = encrypted.read(opening(drop_id, alice_cookie))
    checked = signed.read(opening(drop_id))

    assert opened(for_bob) == {
        "id": str(drop_id),
        "signer": str(alice.person_id),
        "contents": TEXT,
    }
    assert for_alice.status == 403
    # Who signed it is hidden from anyone else.
    assert checked.status == 400
    assert problem_type(checked) == UNUSABLE_BUNDLE


def test_a_forged_signature_inside_does_not_check(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    bob: PersonKey,
) -> None:
    store_object(alice.public_key, uploads, MAX_BYTES)
    forged = SignedBundle.signed(TEXT, bob.private_key, alice.person_id)
    content_id = held_drop(uploads, for_person(bob, forged.encoded()))

    response = encrypted.read(opening(content_id, cookie(sessions, bob, "bob")))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE
    assert response.headers["Cache-Control"] == NOT_STORED


def test_a_signers_key_not_held_is_asked_for_and_503(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    bob: PersonKey,
    recorder: Recorder,
) -> None:
    content_id = held_drop(uploads, for_person(bob, alice.signed(TEXT).encoded()))

    response = encrypted.read(opening(content_id, cookie(sessions, bob, "bob")))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [alice.person_id.fields()]


def test_a_block_not_held_is_asked_for_and_503(
    encrypted: EncryptedDrops, sessions: Sessions, alice: PersonKey, recorder: Recorder
) -> None:
    content_id = ContentId.for_data(b"not held", "sha256")

    response = encrypted.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert response.status == 503
    assert response.headers["Cache-Control"] == NOT_STORED
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [content_id.fields()]


def test_a_blocked_block_is_404_and_not_asked_for(
    storage: StorageConfig,
    signed: SignedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    recorder: Recorder,
) -> None:
    content_id = held_drop(uploads, for_person(alice, TEXT.encode()))
    write_atomically(storage.blocked_list_path, BlockedContent.body([content_id]))
    blocking = EncryptedDrops(
        SignedDrops(
            uploads,
            signed.drops,
            sessions,
            recorder,
            retry_after_seconds=RETRY_AFTER_SECONDS,
            blocked=BlockedContent.of(storage),
        )
    )

    response = blocking.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert response.status == 404
    assert not recorder.payloads(EventType.DATA_NOT_FOUND)


@mark.parametrize(
    "data",
    [
        TEXT.encode(),
        b"just some content\x00nonce",
        PasswordKey.of_password(b"secret").protect(TEXT.encode()),
    ],
)
def test_what_is_not_encrypted_for_a_person_is_unusable(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    caplog: LogCaptureFixture,
    data: bytes,
) -> None:
    content_id = held_drop(uploads, data)

    with caplog.at_level(WARNING):
        response = encrypted.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert not caplog.records


def test_a_scheme_this_node_lacks_is_unusable_and_a_warning(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    sessions: Sessions,
    alice: PersonKey,
    caplog: LogCaptureFixture,
) -> None:
    content_id = held_drop(uploads, bytes(16) + b"\x00PK-RSAOAEP512-AES256-CBC-KEY:abcd")

    with caplog.at_level(WARNING):
        response = encrypted.read(opening(content_id, cookie(sessions, alice, "alice")))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert "PK-RSAOAEP512-AES256-CBC" in caplog.text


def test_a_block_encrypted_by_a_password_opens_with_it(
    encrypted: EncryptedDrops, uploads: OwnUploads, alice_key: PasswordKey
) -> None:
    content_id = held_drop(uploads, alice_key.protect(TEXT.encode(), iv=bytes(range(16))))

    response = encrypted.read_by_password(
        opening_by_password(content_id, {"username": "ALICE", "password": PASSWORD})
    )

    assert opened(response) == {"id": str(content_id), "text": TEXT}


@mark.parametrize("username, password", [("alice", "another password"), ("bob", PASSWORD)])
def test_another_username_or_password_is_403_wrong_key(
    encrypted: EncryptedDrops,
    uploads: OwnUploads,
    alice_key: PasswordKey,
    username: str,
    password: str,
) -> None:
    content_id = held_drop(uploads, alice_key.protect(TEXT.encode()))

    response = encrypted.read_by_password(
        opening_by_password(content_id, {"username": username, "password": password})
    )

    assert response.status == 403
    assert problem_type(response) == WRONG_KEY
    assert response.headers["Cache-Control"] == NOT_STORED


def test_a_block_for_a_person_is_not_opened_by_a_password(
    encrypted: EncryptedDrops, uploads: OwnUploads, alice: PersonKey
) -> None:
    content_id = held_drop(uploads, for_person(alice, TEXT.encode()))

    response = encrypted.read_by_password(
        opening_by_password(content_id, {"username": "alice", "password": PASSWORD})
    )

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE


@mark.parametrize("value", [[], {"username": "alice"}, {"username": "alice", "password": ""}])
def test_a_body_that_is_no_username_and_password_is_400(
    encrypted: EncryptedDrops, uploads: OwnUploads, alice_key: PasswordKey, value: object
) -> None:
    content_id = held_drop(uploads, alice_key.protect(TEXT.encode()))

    response = encrypted.read_by_password(opening_by_password(content_id, value))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST


def test_a_body_that_does_not_say_it_is_json_is_415(
    encrypted: EncryptedDrops, uploads: OwnUploads, alice_key: PasswordKey
) -> None:
    content_id = held_drop(uploads, alice_key.protect(TEXT.encode()))
    value = {"username": "alice", "password": PASSWORD}

    response = encrypted.read_by_password(opening_by_password(content_id, value, "text/plain"))

    assert response.status == 415


def test_opening_by_a_password_takes_its_turn(
    uploads: OwnUploads, signed: SignedDrops, alice_key: PasswordKey
) -> None:
    content_id = held_drop(uploads, alice_key.protect(TEXT.encode()))
    encrypted = EncryptedDrops(signed)
    request = opening_by_password(content_id, {"username": "alice", "password": PASSWORD})
    answers: list[Response] = []
    waiting = Thread(target=lambda: answers.append(encrypted.read_by_password(request)))

    with encrypted.derivations.turn():
        waiting.start()
        waiting.join(timeout=0.2)

        assert waiting.is_alive()
        assert answers == []

    waiting.join(timeout=10)
    assert not waiting.is_alive()
    assert opened(answers[0])["text"] == TEXT


@mark.parametrize("method", ["GET", "POST"])
def test_a_path_naming_no_content_is_400(
    encrypted: EncryptedDrops, sessions: Sessions, alice: PersonKey, method: str
) -> None:
    request = Request(
        method,
        f"{ENCRYPTED_PATH}/sha256/xyz",
        params={"algorithm": "sha256", "hash": "xyz"},
        headers={"Content-Type": JSON_CONTENT_TYPE, **cookie(sessions, alice, "alice")},
        body=RequestBody.of(b'{"username": "alice", "password": "correct horse"}'),
    )

    response = (encrypted.read if method == "GET" else encrypted.read_by_password)(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_CONTENT_ADDRESS


def test_a_block_not_held_is_asked_for_before_any_key_is_derived(
    encrypted: EncryptedDrops, recorder: Recorder
) -> None:
    content_id = ContentId.for_data(b"not held", "sha256")

    with encrypted.derivations.turn():
        # Were a key derived, it would wait for this turn, and never end.
        response = encrypted.read_by_password(
            opening_by_password(content_id, {"username": "alice", "password": PASSWORD})
        )

    assert response.status == 503
    assert response.headers["Cache-Control"] == NOT_STORED
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [content_id.fields()]
