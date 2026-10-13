"""Tests for making people's identities, and signing them in and out."""

from __future__ import annotations
from dataclasses import dataclass, field, replace
from json import dumps, loads
from logging import WARNING
from threading import Thread
from typing import Any, Mapping
from zlib import decompress

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.signing import SignedBundle
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_REQUEST
from libranet.protocol.drop_requests import DropRequest
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.identity_handlers import (
    FALLBACK_PATH,
    SESSION_PATH,
    USERS_PATH,
    Identities,
)
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Sessions

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 12
RETRY_AFTER_SECONDS = 3
PASSWORD = "correct horse"
FALLBACK_PASSWORD = "battery staple"
NEW_FALLBACK_PASSWORD = "stapled battery"
TARGET = DropTarget.of("user:alice")
NO_ONE = {"id": None, "username": None, "created": None, "fallback": None, "fallback_for": None}


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]


@dataclass(frozen=True)
class RecordingDrops(DropHandler):
    """A drop handler keeping every drop it is asked to place, in turn."""

    asked: list[DropRequest] = field(default_factory=list)

    def placed(self, request: Request, asked: DropRequest) -> StoredDrop | Response:
        self.asked.append(asked)
        return super().placed(request, asked)


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def waited() -> list[float]:
    """How long each sign-in waited before trying again, in turn."""
    return []


@fixture
def identities(storage: StorageConfig, recorder: Recorder, waited: list[float]) -> Identities:
    truth = CasStore.source_of_truth(storage)
    uploads = OwnUploads.of(storage, truth, NODE_ID, recorder)
    return Identities(
        uploads,
        SearchHandler(
            LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
        ),
        DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS),
        Sessions(60.0),
        recorder,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        wait=waited.append,
    )


def asked(
    method: str, path: str, value: object = None, *, headers: Mapping[str, str] | None = None
) -> Request:
    """A request to ``path``, carrying ``value`` as JSON unless it is ``None``."""
    body = b"" if value is None else dumps(value).encode("utf-8")
    sent = {"Content-Type": JSON_CONTENT_TYPE} if body else {}
    return Request(method, path, headers={**sent, **(headers or {})}, body=RequestBody.of(body))


def making(
    username: str = "alice",
    password: str = PASSWORD,
    *,
    fallback_password: str = FALLBACK_PASSWORD,
    changes: Mapping[str, object] | None = None,
) -> Request:
    """``POST /data/users`` asking for an identity at the smallest key size, quickly."""
    value = {
        "username": username,
        "password": password,
        "fallback_password": fallback_password,
        "key_bits": 2048,
        "seconds": 0,
    }
    return asked("POST", USERS_PATH, {**value, **(changes or {})})


def taking_over(
    password: str = FALLBACK_PASSWORD, fallback_password: str = NEW_FALLBACK_PASSWORD
) -> Request:
    """``POST /data/fallback`` asking Alice's fallback to take over, quickly."""
    return asked(
        "POST",
        FALLBACK_PATH,
        {
            "username": "alice",
            "password": password,
            "fallback_password": fallback_password,
            "seconds": 0,
        },
    )


def signing_in(username: str = "alice", password: str = PASSWORD) -> Request:
    return asked("POST", SESSION_PATH, {"username": username, "password": password})


def cookie_of(response: Response) -> dict[str, str]:
    """The ``Cookie`` header a browser sends back after ``response``."""
    return {"Cookie": response.headers["Set-Cookie"].partition(";")[0]}


def validated(storage: StorageConfig, *content_ids: ContentId) -> None:
    """Store this node's uploads of ``content_ids`` as truth, as the validator does."""
    incoming = CasStore.for_node(storage, NODE_ID)

    for content_id in content_ids:
        CasStore.source_of_truth(storage).write(content_id, incoming.read(content_id))


def validated_all(storage: StorageConfig) -> None:
    """Store every upload of this node as truth, as the validator does."""
    held = CasStore.for_node(storage, NODE_ID).held_objects()
    validated(storage, *(stored.content_id for stored in held))


def uploaded(storage: StorageConfig, content_id: ContentId) -> bytes:
    """What this node uploaded as ``content_id``, decompressed if need be."""
    stored = CasStore.for_node(storage, NODE_ID).read(content_id)
    return stored if content_id.matches(stored) else decompress(stored)


def made(storage: StorageConfig, identities: Identities, request: Request) -> dict[str, Any]:
    """What making the identity ``request`` asks for answers, once the validator has stored it."""
    response = identities.make(request)
    assert response.status == 201, response.body
    answer: dict[str, Any] = loads(response.body)
    validated_all(storage)
    return answer


def placed(storage: StorageConfig, content: bytes, minimum_bits: int) -> ContentId:
    """``content`` stored in the source of truth as a drop at Alice's, matching ``minimum_bits``."""
    drop = TARGET.placed(content, 0, minimum_bits)
    content_id = ContentId.for_data(drop.data, "sha256")
    CasStore.source_of_truth(storage).write(content_id, drop.data)
    return content_id


def test_making_an_identity_keeps_its_keys_and_signs_it_in(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    response = identities.make(making(" Alice ", changes={"minimum_bits": 4}))

    assert response.status == 201, response.body
    answer = loads(response.body)
    person_id = ContentId.parse(answer["id"])
    drop_id = ContentId.parse(answer["drop"])
    assert answer["username"] == "alice"
    assert answer["target"] == TARGET.hex
    assert answer["matching_bits"] >= 4
    assert response.headers["Location"] == f"/data/{person_id}"
    assert response.headers["Cache-Control"] == "no-store"
    assert person_id.matches(uploaded(storage, person_id))
    key = PersonKey.opened(
        uploaded(storage, drop_id), Username.create("alice").password_key(PASSWORD)
    )
    assert key.person_id == person_id
    assert answer["created"] == key.created_text
    assert answer["fallback"] == str(key.fallback)
    assert answer["fallback_for"] is None
    put = [ContentId.from_fields(payload) for payload in recorder.payloads(EventType.PUT_COMPLETED)]
    assert len(put) == 5
    assert put[2:] == [drop_id, person_id, key.fallback]
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie_of(response)))
    assert loads(who.body) == {
        "id": str(person_id),
        "username": "alice",
        "created": answer["created"],
        "fallback": answer["fallback"],
        "fallback_for": None,
    }


def test_making_an_identity_makes_its_fallback_and_names_it_first(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    answer = made(storage, identities, making(changes={"minimum_bits": 4}))
    person_id = ContentId.parse(answer["id"])
    fallback_id = ContentId.parse(answer["fallback"])
    fallback_drop, first_drop, _, _, fallback_key = (
        ContentId.from_fields(payload) for payload in recorder.payloads(EventType.PUT_COMPLETED)
    )

    fallback = PersonKey.opened(
        uploaded(storage, fallback_drop),
        Username.create("alice").password_key(FALLBACK_PASSWORD),
    )
    assert fallback.person_id == fallback_id == fallback_key
    assert fallback.fallback_for == person_id
    assert fallback.created_text == answer["created"]
    assert matching_bits(fallback_drop.hash, TARGET.hex) >= 4
    details = DropTarget.of(f"user:{person_id}")
    assert matching_bits(first_drop.hash, details.hex) >= 4
    drop = uploaded(storage, first_drop)
    first = SignedBundle.decoded(drop[: drop.rindex(b"\x00")])
    first.verified(uploaded(storage, person_id))
    assert first.signer == person_id
    assert loads(first.contents) == {
        "created": answer["created"],
        "fallback": str(fallback_id),
        "fields": {},
        "version": 1,
    }


def test_the_three_drops_share_the_search_asked_for(
    identities: Identities, storage: StorageConfig
) -> None:
    drops = RecordingDrops(
        identities.uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS, turns=identities.drops.turns
    )

    answer = made(storage, replace(identities, drops=drops), making(changes={"seconds": 0.03}))

    assert [placed.target for placed in drops.asked] == [
        TARGET,
        DropTarget.of(f"user:{answer['id']}"),
        TARGET,
    ]
    assert [placed.seconds for placed in drops.asked] == [0.01] * 3
    assert [placed.minimum_bits for placed in drops.asked] == [0] * 3


def test_the_fallback_password_signs_in_as_the_fallback(
    identities: Identities, storage: StorageConfig
) -> None:
    answer = made(storage, identities, making())

    response = identities.sign_in(signing_in(password=FALLBACK_PASSWORD))

    assert response.status == 200, response.body
    assert loads(response.body) == {
        "id": answer["fallback"],
        "username": "alice",
        "created": answer["created"],
        "fallback": None,
        "fallback_for": answer["id"],
    }


def test_a_fallback_password_that_opens_an_identity_here_makes_none(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    made(storage, identities, making())
    uploads_before = len(recorder.payloads(EventType.PUT_COMPLETED))

    reused = identities.make(making(password="another horse", fallback_password=PASSWORD))

    assert reused.status == 409
    assert "fallback password" in loads(reused.body)["detail"]
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == uploads_before


def test_an_identity_made_without_signing_in_starts_no_session(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    response = identities.make_without_signing_in(making(" Alice ", changes={"minimum_bits": 4}))

    assert response.status == 201, response.body
    answer = loads(response.body)
    assert answer["username"] == "alice"
    assert answer["target"] == TARGET.hex
    assert answer["matching_bits"] >= 4
    assert "Set-Cookie" not in response.headers
    assert "Location" not in response.headers
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == 5
    validated_all(storage)
    signed_in = identities.sign_in(signing_in())
    assert signed_in.status == 200, signed_in.body
    assert loads(signed_in.body) == {
        "id": answer["id"],
        "username": "alice",
        "created": answer["created"],
        "fallback": answer["fallback"],
        "fallback_for": None,
    }


def test_a_fallback_takes_over_with_a_fallback_of_its_own(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    answer = made(storage, identities, making())
    put_before = len(recorder.payloads(EventType.PUT_COMPLETED))

    response = identities.take_over(taking_over())

    assert response.status == 200, response.body
    taken = loads(response.body)
    fallback_id = ContentId.parse(taken["fallback"])
    assert taken["id"] == answer["fallback"]
    assert taken["fallback_for"] == answer["id"]
    assert taken["created"] == answer["created"]
    assert taken["target"] == TARGET.hex
    assert response.headers["Cache-Control"] == "no-store"
    # The fallback's public key is held already, so storing it uploads nothing.
    own_drop, first_drop, block_drop, fallback_key = (
        ContentId.from_fields(payload)
        for payload in recorder.payloads(EventType.PUT_COMPLETED)[put_before:]
    )
    assert block_drop == ContentId.parse(taken["drop"])
    assert fallback_key == fallback_id
    own = PersonKey.opened(
        uploaded(storage, own_drop),
        Username.create("alice").password_key(NEW_FALLBACK_PASSWORD),
    )
    assert own.person_id == fallback_id
    assert own.fallback_for == ContentId.parse(taken["id"])
    first = SignedBundle.decoded(uploaded(storage, first_drop).rpartition(b"\x00")[0])
    assert first.signer == ContentId.parse(taken["id"])
    assert loads(first.contents) == {
        "created": answer["created"],
        "fallback": str(fallback_id),
        "fields": {},
        "version": 1,
    }
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie_of(response)))
    assert loads(who.body)["fallback"] == str(fallback_id)


def test_once_taken_over_the_fallback_password_signs_in_with_its_own_fallback(
    identities: Identities, storage: StorageConfig
) -> None:
    made(storage, identities, making())
    taken = loads(identities.take_over(taking_over()).body)
    validated_all(storage)
    key = Username.create("alice").password_key(FALLBACK_PASSWORD)
    # A block as it was before it took over, placed to be found before the
    # one sealed as it took over.
    more_bits = 1 + max(
        matching_bits(stored.content_id.hash, TARGET.hex)
        for stored in CasStore.source_of_truth(storage).held_objects()
    )
    current = PersonKey.opened(uploaded(storage, ContentId.parse(taken["drop"])), key)
    placed(storage, replace(current, fallback=None).sealed(key), more_bits)

    response = identities.sign_in(signing_in(password=FALLBACK_PASSWORD))

    assert response.status == 200, response.body
    assert loads(response.body)["fallback"] == taken["fallback"]
    signed_in = identities.sign_in(signing_in(password=NEW_FALLBACK_PASSWORD))
    assert loads(signed_in.body)["fallback_for"] == taken["id"]


def test_only_a_fallback_without_one_of_its_own_takes_over(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    made(storage, identities, making())
    identities.take_over(taking_over())
    validated_all(storage)
    put_before = len(recorder.payloads(EventType.PUT_COMPLETED))

    the_identity = identities.take_over(taking_over(password=PASSWORD))
    again = identities.take_over(taking_over(fallback_password="another staple"))

    assert the_identity.status == again.status == 409
    assert "only a fallback that has none takes over" in loads(again.body)["detail"]
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == put_before


def test_a_takeover_whose_drop_is_too_large_for_an_object_is_413(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    made(storage, identities, making())
    put_before = len(recorder.payloads(EventType.PUT_COMPLETED))
    drops = replace(identities.drops, max_object_bytes=1024)

    response = replace(identities, drops=drops).take_over(taking_over())

    assert response.status == 413
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == put_before


def test_a_new_fallback_password_that_opens_an_identity_here_is_refused(
    identities: Identities, storage: StorageConfig
) -> None:
    made(storage, identities, making())

    response = identities.take_over(taking_over(fallback_password=PASSWORD))

    assert response.status == 409
    assert "fallback password already open" in loads(response.body)["detail"]


def test_a_fallback_not_found_is_503_after_a_pass(
    identities: Identities, waited: list[float]
) -> None:
    response = identities.take_over(taking_over())

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert waited == [RETRY_AFTER_SECONDS]


@mark.parametrize(
    "request_value, complaint",
    [
        (
            {"username": "alice", "password": FALLBACK_PASSWORD, "seconds": 0},
            '"fallback_password" must be a string',
        ),
        (
            {
                "username": "alice",
                "password": FALLBACK_PASSWORD,
                "fallback_password": NEW_FALLBACK_PASSWORD,
                "seconds": MAX_SECONDS + 1,
            },
            '"seconds" may be at most 1.0 here',
        ),
    ],
)
def test_a_takeover_that_cannot_be_made_is_400_before_any_work(
    identities: Identities, recorder: Recorder, request_value: object, complaint: str
) -> None:
    response = identities.take_over(asked("POST", FALLBACK_PATH, request_value))

    assert response.status == 400
    assert complaint in loads(response.body)["detail"]
    assert recorder.messages == []


def test_making_without_signing_in_is_refused_as_making_is(
    identities: Identities, storage: StorageConfig
) -> None:
    made(storage, identities, making())

    again = identities.make_without_signing_in(making("ALICE"))
    short = identities.make_without_signing_in(making("bob", "short"))

    assert again.status == 409
    assert short.status == 400
    assert problem_type(short) == INVALID_REQUEST


def test_signing_in_opens_the_identity_at_the_usernames_drop(
    identities: Identities, storage: StorageConfig, waited: list[float]
) -> None:
    answer = made(storage, identities, making())
    person_id = answer["id"]

    response = identities.sign_in(signing_in("ALICE"))

    assert response.status == 200, response.body
    assert loads(response.body) == {
        "id": person_id,
        "username": "alice",
        "created": answer["created"],
        "fallback": answer["fallback"],
        "fallback_for": None,
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Set-Cookie"].endswith("; Path=/; HttpOnly; SameSite=Strict")
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie_of(response)))
    assert loads(who.body)["id"] == person_id
    assert waited == []


def test_a_wrong_password_is_tried_again_after_a_pass_then_503(
    identities: Identities, storage: StorageConfig, recorder: Recorder, waited: list[float]
) -> None:
    made(storage, identities, making())
    searched = len(recorder.payloads(EventType.SEARCH_REQUESTED))

    response = identities.sign_in(signing_in(password="wrong horse"))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert "Set-Cookie" not in response.headers
    assert waited == [RETRY_AFTER_SECONDS]
    assert len(recorder.payloads(EventType.SEARCH_REQUESTED)) == searched + 2


def test_an_identity_arriving_while_a_sign_in_waits_signs_it_in(
    identities: Identities, storage: StorageConfig
) -> None:
    # Made, then taken away, to arrive as a peer's answer would during the wait.
    answer = loads(identities.make(making()).body)
    incoming = CasStore.for_node(storage, NODE_ID)
    held = {
        content_id: incoming.read(content_id)
        for content_id in (ContentId.parse(answer["id"]), ContentId.parse(answer["drop"]))
    }

    for content_id in held:
        incoming.delete(content_id)

    def arriving(_seconds: float) -> None:
        for content_id, data in held.items():
            CasStore.source_of_truth(storage).write(content_id, data)

    response = replace(identities, wait=arriving).sign_in(signing_in())

    assert response.status == 200, response.body
    assert loads(response.body)["id"] == answer["id"]


def test_signing_in_passes_over_blocks_at_the_drop_that_are_not_the_persons(
    identities: Identities, storage: StorageConfig
) -> None:
    answer = made(storage, identities, making())
    alices = Username.create("alice").password_key(PASSWORD)
    anothers = Username.create("alice").password_key("another person's")
    # Each matches more bits than anything making the identity stored does,
    # so is found, and tried, first.
    more_bits = 1 + max(
        matching_bits(stored.content_id.hash, TARGET.hex)
        for stored in CasStore.source_of_truth(storage).held_objects()
    )
    decoys = {
        placed(storage, b"not even protected", more_bits),
        placed(storage, NewIdentity.generate(2048).key.sealed(anothers), more_bits),
        placed(storage, alices.protect(b'{"not": "an identity"}'), more_bits),
        placed(storage, b"x" * (70 * 1024), more_bits),
    }

    response = identities.sign_in(signing_in())

    assert set(identities.search.fresh_results(TARGET.hex)[: len(decoys)]) == decoys
    assert response.status == 200, response.body
    assert loads(response.body)["id"] == answer["id"]


def test_a_block_held_that_is_not_what_its_id_names_is_passed_over(
    identities: Identities, storage: StorageConfig, caplog: LogCaptureFixture
) -> None:
    answer = made(storage, identities, making())
    drop_id = ContentId.parse(answer["drop"])
    CasStore.source_of_truth(storage).write(drop_id, b"damaged on disk")
    CasStore.for_node(storage, NODE_ID).delete(drop_id)

    with caplog.at_level(WARNING):
        response = identities.sign_in(signing_in())

    assert response.status == 503
    assert f"Passing over {drop_id}" in caplog.text


def test_an_identity_whose_drop_is_too_large_for_an_object_is_413_and_leaves_nothing(
    identities: Identities, recorder: Recorder
) -> None:
    drops = replace(identities.drops, max_object_bytes=1024)

    response = replace(identities, drops=drops).make(making())

    assert response.status == 413
    assert recorder.payloads(EventType.PUT_COMPLETED) == []


def test_finding_nothing_at_the_drop_is_503_and_searched_for_twice(
    identities: Identities, recorder: Recorder, waited: list[float]
) -> None:
    response = identities.sign_in(signing_in())

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.SEARCH_REQUESTED) == [{"prefix": TARGET.hex}] * 2
    assert waited == [RETRY_AFTER_SECONDS]


def test_a_block_found_and_not_held_is_asked_for_and_503(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    # Heard of, as the stats module adds to a cached search, but not held.
    elsewhere = ContentId.for_data(b"a block a peer holds", "sha256")
    SearchCache.of(storage).save_results(TARGET.hex, [elsewhere])

    response = identities.sign_in(signing_in())

    assert response.status == 503
    # Once by each try.
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [elsewhere.fields()] * 2


def test_an_identity_stored_since_a_search_was_cached_is_found_at_once(
    identities: Identities, storage: StorageConfig
) -> None:
    assert identities.sign_in(signing_in()).status == 503
    person_id = made(storage, identities, making())["id"]

    response = identities.sign_in(signing_in())

    assert response.status == 200, response.body
    assert loads(response.body)["id"] == person_id


def test_a_search_of_a_drop_leaves_out_what_this_node_has_blocked(
    storage: StorageConfig, recorder: Recorder
) -> None:
    truth = CasStore.source_of_truth(storage)
    kept, blocked = (ContentId.for_data(data, "sha256") for data in (b"kept", b"blocked"))
    truth.write(kept, b"kept")
    truth.write(blocked, b"blocked")
    write_atomically(storage.blocked_list_path, BlockedContent.body([blocked]))
    cache = SearchCache.of(storage)
    search = SearchHandler(LocalSearch(truth, 8), cache, recorder, BlockedContent.of(storage))

    found = search.fresh_results(blocked.hash)

    assert found == [kept]
    assert cache.load_results(blocked.hash) == [kept]


def test_the_same_username_and_password_make_no_second_identity(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    made(storage, identities, making())
    uploads_before = len(recorder.payloads(EventType.PUT_COMPLETED))

    response = identities.make(making("ALICE"))

    assert response.status == 409
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == uploads_before


def test_people_may_share_a_username_with_passwords_of_their_own(
    identities: Identities, storage: StorageConfig
) -> None:
    first = made(storage, identities, making())["id"]
    second = made(
        storage,
        identities,
        making(password="another horse", fallback_password="another staple"),
    )["id"]

    signed_in_first = identities.sign_in(signing_in())
    signed_in_second = identities.sign_in(signing_in(password="another horse"))

    assert first != second
    assert loads(signed_in_first.body)["id"] == first
    assert loads(signed_in_second.body)["id"] == second


def test_signing_in_stores_the_public_key_again_if_it_is_gone(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    answer = made(storage, identities, making())
    person_id = ContentId.parse(answer["id"])
    CasStore.source_of_truth(storage).delete(person_id)
    CasStore.for_node(storage, NODE_ID).delete(person_id)

    response = identities.sign_in(signing_in())

    assert response.status == 200, response.body
    assert person_id.matches(uploaded(storage, person_id))
    assert recorder.payloads(EventType.PUT_COMPLETED)[-1] == {
        **person_id.fields(),
        "node_id": str(NODE_ID),
    }


@mark.parametrize(
    "changes, complaint",
    [
        ({"seconds": MAX_SECONDS + 1}, '"seconds" may be at most 1.0 here'),
        ({"minimum_bits": MAX_MINIMUM_BITS + 1}, '"minimum_bits" may be at most 12 here'),
        ({"key_bits": 1024}, '"key_bits" must be at least 2048, got 1024'),
        ({"key_bits": 8192}, '"key_bits" must be one of 2048, 3072, 4096 here, got 8192'),
        ({"password": "short"}, "at least 8 characters"),
        ({"fallback_password": "short"}, '"fallback_password" must be at least 8'),
        ({"fallback_password": PASSWORD}, '"fallback_password" must not be the password'),
    ],
)
def test_an_identity_that_cannot_be_made_is_400_before_any_work(
    identities: Identities, recorder: Recorder, changes: dict[str, object], complaint: str
) -> None:
    response = identities.make(making(changes=changes))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert complaint in loads(response.body)["detail"]
    assert recorder.messages == []


def test_a_key_is_made_only_at_the_sizes_this_node_makes(
    identities: Identities, recorder: Recorder
) -> None:
    larger_only = replace(identities, key_bits=(3072,))

    response = larger_only.make(making())

    assert response.status == 400
    assert loads(response.body)["detail"] == '"key_bits" must be one of 3072 here, got 2048'
    assert recorder.messages == []


def test_a_sign_in_that_is_not_one_is_400(identities: Identities, recorder: Recorder) -> None:
    response = identities.sign_in(asked("POST", SESSION_PATH, {"username": "alice"}))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert recorder.messages == []


@mark.parametrize("path", [USERS_PATH, FALLBACK_PATH, SESSION_PATH])
def test_a_body_that_does_not_say_it_is_json_is_415(identities: Identities, path: str) -> None:
    request = asked("POST", path, {"username": "alice"}, headers={"Content-Type": "text/plain"})
    handler = {
        USERS_PATH: identities.make,
        FALLBACK_PATH: identities.take_over,
        SESSION_PATH: identities.sign_in,
    }[path]

    assert handler(request).status == 415


def test_no_one_is_signed_in_without_a_session(identities: Identities) -> None:
    response = identities.signed_in(asked("GET", SESSION_PATH))

    assert response.status == 200
    assert loads(response.body) == NO_ONE
    assert response.headers["Cache-Control"] == "no-store"


def test_signing_out_ends_the_session_and_removes_its_cookie(
    identities: Identities, storage: StorageConfig
) -> None:
    made(storage, identities, making())
    cookie = cookie_of(identities.sign_in(signing_in()))

    response = identities.sign_out(asked("DELETE", SESSION_PATH, headers=cookie))

    assert response.status == 204
    assert response.headers["Set-Cookie"].endswith("Max-Age=0")
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie))
    assert loads(who.body) == NO_ONE


def test_a_sign_in_waits_while_another_key_is_derived(identities: Identities) -> None:
    answers: list[Response] = []
    waiting = Thread(target=lambda: answers.append(identities.sign_in(signing_in())))

    with identities.derivations.turn():
        waiting.start()
        waiting.join(timeout=0.3)

        assert waiting.is_alive()
        assert answers == []

    waiting.join(timeout=10)
    assert not waiting.is_alive()
    assert answers[0].status == 503
