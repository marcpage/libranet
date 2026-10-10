"""Tests for making people's identities, and signing them in and out."""

from __future__ import annotations
from dataclasses import replace
from json import dumps, loads
from logging import WARNING
from threading import Thread
from typing import Any, Mapping
from zlib import decompress

from pytest import LogCaptureFixture, fixture, mark

from libranet.atomic_file import write_atomically
from libranet.bundle.parts import PartPath
from libranet.bundle.shapes import Bundle
from libranet.cas.blocked import BlockedContent
from libranet.cas.content_id import ContentId
from libranet.cas.drops import DropTarget
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.directory import DIRECTORY_TARGET, FoundDirectory
from libranet.identity.people import PersonKey, Username
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_CONFIG_REQUEST, NO_IDENTITY
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.identity_handlers import SESSION_PATH, USERS_PATH, Identities
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Sessions

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 12
RETRY_AFTER_SECONDS = 3
PASSWORD = "correct horse"
TARGET = DropTarget.of("user:alice")


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}

    def payloads(self, event: EventType) -> list[dict[str, Any]]:
        return [payload for published, payload in self.messages if published == event]

    def ids(self, event: EventType) -> list[ContentId]:
        return [ContentId.from_fields(payload) for payload in self.payloads(event)]


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def identities(storage: StorageConfig, recorder: Recorder) -> Identities:
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
    changes: Mapping[str, object] | None = None,
) -> Request:
    """``POST /data/users`` asking for an identity at the smallest key size, quickly."""
    value = {"username": username, "password": password, "key_bits": 2048, "seconds": 0}
    return asked("POST", USERS_PATH, {**value, **(changes or {})})


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


def uploaded(storage: StorageConfig, content_id: ContentId) -> bytes:
    """What this node uploaded as ``content_id``, decompressed if need be."""
    stored = CasStore.for_node(storage, NODE_ID).read(content_id)
    return stored if content_id.matches(stored) else decompress(stored)


def made(storage: StorageConfig, identities: Identities, request: Request) -> dict[str, Any]:
    """What making the identity ``request`` asks for answers, once the validator has stored it."""
    response = identities.make(request)
    assert response.status == 201, response.body
    answer: dict[str, Any] = loads(response.body)
    validated(storage, ContentId.parse(answer["id"]), ContentId.parse(answer["drop"]))
    return answer


def no_extension(path: PartPath) -> Bundle:
    raise AssertionError(f"No extension is read, but {path} was")


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
    # The user directory is uploaded after them.
    assert recorder.payloads(EventType.PUT_COMPLETED)[:2] == [
        {**drop_id.fields(), "node_id": str(NODE_ID)},
        {**person_id.fields(), "node_id": str(NODE_ID)},
    ]
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie_of(response)))
    assert loads(who.body) == {"id": str(person_id), "username": "alice"}


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
    assert len(recorder.payloads(EventType.PUT_COMPLETED)) == 3
    validated(storage, ContentId.parse(answer["id"]), ContentId.parse(answer["drop"]))
    signed_in = identities.sign_in(signing_in())
    assert signed_in.status == 200, signed_in.body
    assert loads(signed_in.body) == {"id": answer["id"], "username": "alice"}


@mark.parametrize("asked_to", ["make", "make_without_signing_in"])
def test_making_an_identity_adds_the_person_to_the_user_directory(
    identities: Identities, storage: StorageConfig, recorder: Recorder, asked_to: str
) -> None:
    response = getattr(identities, asked_to)(making(changes={"minimum_bits": 4}))

    assert response.status == 201, response.body
    *_, directory = recorder.ids(EventType.PUT_COMPLETED)
    found = FoundDirectory.read(directory, uploaded(storage, directory), no_extension)
    assert found is not None
    assert found.people == {ContentId.parse(loads(response.body)["id"])}
    assert matching_bits(directory.hash, DIRECTORY_TARGET.hex) >= 4


def test_making_without_signing_in_is_refused_as_making_is(
    identities: Identities, storage: StorageConfig
) -> None:
    made(storage, identities, making())

    again = identities.make_without_signing_in(making("ALICE"))
    short = identities.make_without_signing_in(making("bob", "short"))

    assert again.status == 409
    assert short.status == 400
    assert problem_type(short) == INVALID_CONFIG_REQUEST


def test_signing_in_opens_the_identity_at_the_usernames_drop(
    identities: Identities, storage: StorageConfig
) -> None:
    person_id = made(storage, identities, making())["id"]

    response = identities.sign_in(signing_in("ALICE"))

    assert response.status == 200, response.body
    assert loads(response.body) == {"id": person_id, "username": "alice"}
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Set-Cookie"].endswith("; Path=/; HttpOnly; SameSite=Strict")
    who = identities.signed_in(asked("GET", SESSION_PATH, headers=cookie_of(response)))
    assert loads(who.body)["id"] == person_id


def test_a_wrong_password_opens_no_identity(identities: Identities, storage: StorageConfig) -> None:
    made(storage, identities, making())

    response = identities.sign_in(signing_in(password="wrong horse"))

    assert response.status == 403
    assert problem_type(response) == NO_IDENTITY
    assert "Set-Cookie" not in response.headers


def test_signing_in_passes_over_blocks_at_the_drop_that_are_not_the_persons(
    identities: Identities, storage: StorageConfig
) -> None:
    answer = made(storage, identities, making())
    alices = Username.create("alice").password_key(PASSWORD)
    anothers = Username.create("alice").password_key("another person's")
    # Each matches more bits than the identity and its public key do, so is
    # found, and tried, first.
    more_bits = 1 + max(
        matching_bits(ContentId.parse(answer[name]).hash, TARGET.hex) for name in ("id", "drop")
    )
    decoys = {
        placed(storage, b"not even protected", more_bits),
        placed(storage, PersonKey.generate(2048).sealed(anothers), more_bits),
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

    assert response.status == 403
    assert f"Passing over {drop_id}" in caplog.text


def test_an_identity_whose_drop_is_too_large_for_an_object_is_413_and_leaves_nothing(
    identities: Identities, recorder: Recorder
) -> None:
    drops = replace(identities.drops, max_object_bytes=1024)

    response = replace(identities, drops=drops).make(making())

    assert response.status == 413
    assert recorder.payloads(EventType.PUT_COMPLETED) == []


def test_finding_nothing_at_the_drop_is_503_and_searched_for(
    identities: Identities, recorder: Recorder
) -> None:
    response = identities.sign_in(signing_in())

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.SEARCH_REQUESTED) == [{"prefix": TARGET.hex}]


def test_a_block_found_and_not_held_is_asked_for_and_503(
    identities: Identities, storage: StorageConfig, recorder: Recorder
) -> None:
    # Heard of, as the stats module adds to a cached search, but not held.
    elsewhere = ContentId.for_data(b"a block a peer holds", "sha256")
    SearchCache.of(storage).save_results(TARGET.hex, [elsewhere])

    response = identities.sign_in(signing_in())

    assert response.status == 503
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [elsewhere.fields()]


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
    second = made(storage, identities, making(password="another horse"))["id"]

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
    ],
)
def test_an_identity_that_cannot_be_made_is_400_before_any_work(
    identities: Identities, recorder: Recorder, changes: dict[str, object], complaint: str
) -> None:
    response = identities.make(making(changes=changes))

    assert response.status == 400
    assert problem_type(response) == INVALID_CONFIG_REQUEST
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
    assert problem_type(response) == INVALID_CONFIG_REQUEST
    assert recorder.messages == []


@mark.parametrize("path", [USERS_PATH, SESSION_PATH])
def test_a_body_that_does_not_say_it_is_json_is_415(identities: Identities, path: str) -> None:
    request = asked("POST", path, {"username": "alice"}, headers={"Content-Type": "text/plain"})
    handler = identities.make if path == USERS_PATH else identities.sign_in

    assert handler(request).status == 415


def test_no_one_is_signed_in_without_a_session(identities: Identities) -> None:
    response = identities.signed_in(asked("GET", SESSION_PATH))

    assert response.status == 200
    assert loads(response.body) == {"id": None, "username": None}
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
    assert loads(who.body) == {"id": None, "username": None}


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
