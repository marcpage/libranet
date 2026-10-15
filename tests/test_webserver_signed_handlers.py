"""Tests for making drops signed as a person, and checking what a person or a node signed."""

from __future__ import annotations
from base64 import b64encode
from dataclasses import dataclass, replace
from json import dumps, loads
from logging import ERROR, WARNING
from os import urandom
from typing import Any, Mapping

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleTooLargeError
from libranet.bundle.content import content_chunks
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.bundle.storing import store_object
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    INVALID_BUNDLE_SIGNATURE,
    INVALID_REQUEST,
    INVALID_CONTENT_ADDRESS,
    SESSION_REQUIRED,
    UNUSABLE_BUNDLE,
)
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import IMMUTABLE_CACHE_CONTROL, Request, RequestBody
from libranet.webserver.sessions import Session, Sessions
from libranet.webserver.signed_handlers import SIGNED_PATH, SignedDrops

from tests.helpers import new_identity, problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 12
RETRY_AFTER_SECONDS = 3
TEXT = '{"handle":"alice","version":1}'


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
def person() -> PersonKey:
    return NewIdentity.generate(2048).key


@fixture(scope="module")
def node() -> NodeIdentity:
    return new_identity()


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
    storage: StorageConfig, uploads: OwnUploads, sessions: Sessions, recorder: Recorder
) -> SignedDrops:
    return SignedDrops(
        uploads,
        DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS),
        sessions,
        recorder,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )


@fixture
def cookie(sessions: Sessions, person: PersonKey) -> dict[str, str]:
    """The ``Cookie`` header of a browser Alice is signed in on."""
    started = sessions.start(Session(Username.create("alice"), person))
    return {"Cookie": started.partition(";")[0]}


def signing(
    person: PersonKey, cookie: Mapping[str, str], changes: Mapping[str, object] | None = None
) -> Request:
    """``POST /data/signed`` asking for ``TEXT`` signed at the person's drop, quickly.

    A change to ``None`` removes that member.
    """
    value: dict[str, object] = {"target": f"user:{person.person_id}", "text": TEXT, "seconds": 0}

    for name, change in (changes or {}).items():
        if change is None:
            del value[name]

        else:
            value[name] = change

    body = dumps(value).encode("utf-8")
    return Request(
        "POST",
        SIGNED_PATH,
        headers={"Content-Type": JSON_CONTENT_TYPE, **cookie},
        body=RequestBody.of(body),
    )


def reading(content_id: ContentId, client_address: str = "127.0.0.1") -> Request:
    """``GET /data/signed/{algorithm}/{hash}`` for ``content_id``, from ``client_address``."""
    return Request(
        "GET",
        f"{SIGNED_PATH}/{content_id}",
        params=content_id.fields(),
        body=RequestBody.of(b""),
        client_address=client_address,
    )


def held(uploads: OwnUploads, data: bytes) -> ContentId:
    """``data`` stored among this node's uploads."""
    return store_object(data, uploads, MAX_BYTES)


def dropped(bundle: SignedBundle) -> bytes:
    """``bundle`` as a drop's content, with the bytes that place it."""
    return DropTarget.of("user:anyone").placed(bundle.encoded(), 0).data


def test_no_one_signed_in_is_401_and_signs_nothing(
    signed: SignedDrops, person: PersonKey, recorder: Recorder
) -> None:
    response = signed.make(signing(person, {}))

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert not recorder.payloads(EventType.PUT_COMPLETED)


def test_a_signed_drop_is_signed_by_who_is_signed_in(
    signed: SignedDrops,
    person: PersonKey,
    cookie: Mapping[str, str],
    uploads: OwnUploads,
    recorder: Recorder,
) -> None:
    response = signed.make(signing(person, cookie, {"minimum_bits": 4}))

    assert response.status == 201, response.body
    answer = loads(response.body)
    drop_id = ContentId.parse(answer["id"])
    assert answer["signer"] == str(person.person_id)
    assert answer["target"] == DropTarget.of(f"user:{person.person_id}").hex
    assert answer["matching_bits"] >= 4
    assert response.headers["Location"] == f"/data/{drop_id}"
    bundle = SignedBundle.decoded(strip_targeting(b"".join(content_chunks(uploads, drop_id))))
    assert bundle.signer == person.person_id
    assert bundle.contents == TEXT
    bundle.verified(person.public_key)
    assert b"".join(content_chunks(uploads, person.person_id)) == person.public_key
    assert recorder.payloads(EventType.PUT_COMPLETED) == [
        {**drop_id.fields(), "node_id": str(NODE_ID)},
        {**person.person_id.fields(), "node_id": str(NODE_ID)},
    ]


def test_a_signed_drop_reads_back_as_what_was_signed(
    signed: SignedDrops, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    drop_id = ContentId.parse(loads(signed.make(signing(person, cookie)).body)["id"])

    response = signed.read(reading(drop_id))

    assert response.status == 200, response.body
    assert loads(response.body) == {
        "id": str(drop_id),
        "signer": str(person.person_id),
        "contents": TEXT,
    }
    assert response.headers["Cache-Control"] == IMMUTABLE_CACHE_CONTROL


@mark.parametrize(
    "changes",
    [
        {"text": "not json"},
        {"text": "[1, 2]"},
        {"text": None, "base64": b64encode(TEXT.encode()).decode("ascii")},
        {"target": ""},
        {"seconds": "soon"},
        {"seconds": MAX_SECONDS + 1},
    ],
)
def test_a_body_asking_for_no_signed_drop_is_400(
    signed: SignedDrops,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
    changes: Mapping[str, object],
) -> None:
    response = signed.make(signing(person, cookie, changes))

    assert response.status == 400, response.body
    assert problem_type(response) == INVALID_REQUEST
    assert not recorder.payloads(EventType.PUT_COMPLETED)


def test_a_body_that_does_not_say_it_is_json_is_415(
    signed: SignedDrops, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    request = signing(person, cookie)
    request = Request(
        "POST",
        SIGNED_PATH,
        headers={**request.headers, "Content-Type": "text/plain"},
        body=RequestBody.of(request.body.read()),
    )

    response = signed.make(request)

    assert response.status == 415


def test_a_signed_drop_too_large_for_an_object_is_413(
    signed: SignedDrops, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    text = dumps({"noise": b64encode(urandom(MAX_BYTES * 2)).decode("ascii")})

    response = signed.make(signing(person, cookie, {"text": text}))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE


@mark.parametrize("client_address, external", [("127.0.0.1", False), ("203.0.113.42", True)])
def test_each_check_is_announced_as_a_read_of_the_drop(
    signed: SignedDrops,
    person: PersonKey,
    uploads: OwnUploads,
    recorder: Recorder,
    client_address: str,
    external: bool,
) -> None:
    held(uploads, person.public_key)
    content_id = held(uploads, dropped(person.signed(TEXT)))

    response = signed.read(reading(content_id, client_address))

    assert response.status == 200, response.body
    assert recorder.payloads(EventType.DATA_REQUESTED) == [
        {**content_id.fields(), "external": external}
    ]


def test_a_signed_bundle_held_without_drop_bytes_is_read_too(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads
) -> None:
    held(uploads, person.public_key)
    content_id = held(uploads, person.signed(TEXT).encoded())

    response = signed.read(reading(content_id))

    assert response.status == 200, response.body
    assert loads(response.body)["contents"] == TEXT


def test_what_a_node_signed_is_checked_and_read(
    signed: SignedDrops, node: NodeIdentity, uploads: OwnUploads
) -> None:
    held(uploads, node.public_key)
    drop_id = held(uploads, dropped(node.signed(TEXT)))

    response = signed.read(reading(drop_id))

    assert response.status == 200, response.body
    assert loads(response.body) == {
        "id": str(drop_id),
        "signer": str(node.node_id),
        "contents": TEXT,
    }


def test_a_signature_by_another_node_does_not_check(
    signed: SignedDrops, node: NodeIdentity, uploads: OwnUploads
) -> None:
    forged = SignedBundle.signed(TEXT, Ed25519PrivateKey.generate(), node.node_id)
    held(uploads, node.public_key)

    response = signed.read(reading(held(uploads, dropped(forged))))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE


def test_a_signer_naming_a_key_of_small_order_does_not_check(
    signed: SignedDrops, uploads: OwnUploads
) -> None:
    # The point of order 1, under which this signature verifies for anything.
    identity_point = bytes([1]) + bytes(31)
    small = Ed25519PublicKey.from_public_bytes(identity_point).public_bytes(
        Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
    )
    forged = SignedBundle(held(uploads, small), TEXT, identity_point + bytes(32))

    response = signed.read(reading(held(uploads, dropped(forged))))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE
    assert "small order" in loads(response.body)["detail"]


def test_a_drop_not_held_is_asked_for_and_503(signed: SignedDrops, recorder: Recorder) -> None:
    content_id = ContentId.for_data(b"not held", "sha256")

    response = signed.read(reading(content_id))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [content_id.fields()]


def test_a_signers_key_not_held_is_asked_for_and_503(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads, recorder: Recorder
) -> None:
    content_id = held(uploads, dropped(person.signed(TEXT)))

    response = signed.read(reading(content_id))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [person.person_id.fields()]


@mark.parametrize("blocking", ["drop", "signer"])
def test_a_blocked_drop_or_signers_key_is_404_and_not_asked_for(
    storage: StorageConfig,
    uploads: OwnUploads,
    recorder: Recorder,
    sessions: Sessions,
    person: PersonKey,
    blocking: str,
) -> None:
    content_id = held(uploads, dropped(person.signed(TEXT)))
    blocked = content_id if blocking == "drop" else person.person_id
    write_atomically(storage.blocked_list_path, BlockedContent.body([blocked]))
    signed = SignedDrops(
        uploads,
        DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS),
        sessions,
        recorder,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        blocked=BlockedContent.of(storage),
    )

    response = signed.read(reading(content_id))

    assert response.status == 404
    assert not recorder.payloads(EventType.DATA_NOT_FOUND)


@mark.parametrize("data", [b"just some content\x00nonce", b'{"contents": {}}', b"\xff\x00"])
def test_content_that_is_no_signed_bundle_is_unusable(
    signed: SignedDrops, uploads: OwnUploads, data: bytes
) -> None:
    response = signed.read(reading(held(uploads, data)))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE


def test_a_signed_bundle_hashed_with_another_algorithm_is_unusable_and_a_warning(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads, caplog: LogCaptureFixture
) -> None:
    value = loads(person.signed(TEXT).encoded())
    value["algorithm"] = "sha512"

    with caplog.at_level(WARNING):
        response = signed.read(reading(held(uploads, dumps(value).encode("utf-8"))))

    assert response.status == 400
    assert problem_type(response) == UNUSABLE_BUNDLE
    assert "'sha512' cannot be checked here" in caplog.text


def test_content_that_is_not_signed_is_no_warning(
    signed: SignedDrops, uploads: OwnUploads, caplog: LogCaptureFixture
) -> None:
    with caplog.at_level(WARNING):
        response = signed.read(reading(held(uploads, b"just some content\x00nonce")))

    assert response.status == 400
    assert not caplog.records


def test_a_hash_that_is_not_the_contents_hash_does_not_check(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads
) -> None:
    value = loads(person.signed(TEXT).encoded())
    value["hash"] = "0" * 64

    response = signed.read(reading(held(uploads, dumps(value).encode("utf-8"))))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE


def test_a_fallback_that_has_not_taken_over_signs_nothing(
    signed: SignedDrops, sessions: Sessions, recorder: Recorder
) -> None:
    fallback = NewIdentity.generate(2048).fallback
    started = sessions.start(Session(Username.create("alice"), fallback))
    cookie = {"Cookie": started.partition(";")[0]}

    response = signed.make(signing(fallback, cookie))

    assert response.status == 403
    assert "until it takes over" in loads(response.body)["detail"]
    assert recorder.messages == []


def test_a_signature_by_another_key_does_not_check_and_is_a_warning(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads, caplog: LogCaptureFixture
) -> None:
    other = PersonKey.generate(2048)
    forged = SignedBundle.signed(TEXT, other.private_key, person.person_id)
    held(uploads, person.public_key)

    with caplog.at_level(WARNING):
        response = signed.read(reading(held(uploads, dropped(forged))))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE
    assert "signature does not check" in caplog.text


def test_a_signer_naming_no_public_key_does_not_check(
    signed: SignedDrops, person: PersonKey, uploads: OwnUploads
) -> None:
    not_a_key = held(uploads, b"not a key")
    named = SignedBundle(not_a_key, TEXT, person.signed(TEXT).signature)

    response = signed.read(reading(held(uploads, dropped(named))))

    assert response.status == 400
    assert problem_type(response) == INVALID_BUNDLE_SIGNATURE


def test_what_is_held_that_is_not_what_its_id_names_is_500_and_a_warning(
    storage: StorageConfig, signed: SignedDrops, caplog: LogCaptureFixture
) -> None:
    content_id = ContentId.for_data(b"what it should be", "sha256")
    CasStore.source_of_truth(storage).write(content_id, b"damaged on disk")

    with caplog.at_level(WARNING):
        response = signed.read(reading(content_id))

    assert response.status == 500
    assert f"What is held for {content_id} is not it" in caplog.text


@dataclass(frozen=True)
class Unreadable:
    """Holds ``held``, which no read can give, for a reason no check expects."""

    held: ContentId

    def exists(self, content_id: ContentId) -> bool:
        return content_id == self.held

    def read(self, content_id: ContentId) -> bytes:
        raise BundleTooLargeError(f"{content_id} is too large")


def test_an_error_no_check_expects_is_500_and_an_error(
    signed: SignedDrops, caplog: LogCaptureFixture
) -> None:
    content_id = ContentId.for_data(b"anything", "sha256")

    with caplog.at_level(ERROR):
        response = replace(signed, content=Unreadable(content_id)).read(reading(content_id))

    assert response.status == 500
    assert f"Checking {content_id} failed unexpectedly" in caplog.text


def test_a_path_naming_no_content_is_400(signed: SignedDrops) -> None:
    request = Request(
        "GET",
        f"{SIGNED_PATH}/sha256/xyz",
        params={"algorithm": "sha256", "hash": "xyz"},
        body=RequestBody.of(b""),
    )

    response = signed.read(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_CONTENT_ADDRESS


def test_a_negative_retry_after_is_refused(signed: SignedDrops) -> None:
    with raises(ValueError, match="retry_after_seconds"):
        replace(signed, retry_after_seconds=-1)
