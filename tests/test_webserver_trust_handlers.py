"""Tests for changing the trust list of whoever is signed in (HttpApi §11.7)."""

from __future__ import annotations
from json import dumps, loads
from threading import Thread
from typing import Any, Callable, Mapping

from pytest import fixture, mark, raises

from libranet.bundle.content import content_chunks
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.people import NewIdentity, PersonKey, Username
from libranet.identity.trust import MIN_TRUST_LIST_BITS, TrustLimits, TrustList
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import (
    CONTENT_TOO_LARGE,
    CONTENT_UNAVAILABLE,
    INVALID_CONTENT_ADDRESS,
    INVALID_REQUEST,
    SESSION_REQUIRED,
)
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.sessions import Session, Sessions
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_handlers import TRUST_PATH, TrustEdits, TrustReads
from libranet.webserver.trust_lists import TrustLists

from tests.helpers import problem_type

NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
BOB = ContentId.for_data(b"Bob's public key", "sha256")
CAROL = ContentId.for_data(b"Carol's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's public key", "sha256")
MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 20
RETRY_AFTER_SECONDS = 3


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


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def uploads(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, truth, NODE_ID, recorder)


class Jobs:
    """Stands in for running each build on a thread of its own: keeps each until a test runs it."""

    def __init__(self) -> None:
        self.waiting: list[Callable[[], None]] = []

    def __call__(self, job: Callable[[], None]) -> None:
        self.waiting.append(job)

    def run(self) -> None:
        while self.waiting:
            self.waiting.pop(0)()


def trust_lists(
    storage: StorageConfig, truth: CasStore, uploads: OwnUploads, recorder: Recorder
) -> TrustLists:
    """The trust lists held in ``truth``, read as the web server reads them."""
    search = SearchHandler(
        LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
    )
    return TrustLists(search, uploads)


@fixture
def jobs() -> Jobs:
    return Jobs()


@fixture
def builds(
    storage: StorageConfig, truth: CasStore, uploads: OwnUploads, recorder: Recorder, jobs: Jobs
) -> TrustBuilds:
    """Each person's scores, from the lists held, built only as a test runs ``jobs``."""
    return TrustBuilds(
        trust_lists(storage, truth, uploads, recorder),
        recorder,
        TrustLimits(4, 1000),
        wait_seconds=0.0,
        ask_again_seconds=0.0,
        in_background=jobs,
    )


@fixture
def sessions(builds: TrustBuilds) -> Sessions:
    return Sessions(60.0, listener=builds)


def trust_edits(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    sessions: Sessions,
    recorder: Recorder,
    max_object_bytes: int = MAX_BYTES,
    *,
    builds: TrustBuilds | None = None,
) -> TrustEdits:
    """The trust endpoints over ``truth``, as the web server makes them.

    Each drop is stored in an object no larger than ``max_object_bytes``,
    and each version placed handed to ``builds``, if given.
    """
    drops = DropHandler(uploads, max_object_bytes, MAX_SECONDS, MAX_MINIMUM_BITS)
    return TrustEdits.of(
        trust_lists(storage, truth, uploads, recorder),
        drops,
        sessions,
        recorder,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        builds=builds,
    )


@fixture
def edits(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    sessions: Sessions,
    recorder: Recorder,
    builds: TrustBuilds,
) -> TrustEdits:
    return trust_edits(storage, truth, uploads, sessions, recorder, builds=builds)


@fixture
def reads(builds: TrustBuilds, sessions: Sessions) -> TrustReads:
    return TrustReads(builds, sessions)


@fixture
def cookie(sessions: Sessions, person: PersonKey) -> dict[str, str]:
    """The ``Cookie`` header of a browser Alice is signed in on."""
    started = sessions.start(Session(Username.create("alice"), person))
    return {"Cookie": started.partition(";")[0]}


def changing(
    identity: ContentId, cookie: Mapping[str, str], trusted: bool | None = True
) -> Request:
    """``PUT`` trusting or distrusting ``identity``, as ``trusted`` says; ``DELETE`` if ``None``."""
    if trusted is None:
        return Request(
            "DELETE",
            f"{TRUST_PATH}/{identity}",
            headers=dict(cookie),
            params=identity.fields(),
            body=RequestBody.of(b""),
        )

    return sending(identity, cookie, dumps({"trust": trusted}).encode("utf-8"))


def sending(identity: ContentId, cookie: Mapping[str, str], body: bytes) -> Request:
    """``PUT`` of ``body`` to the entry for ``identity``."""
    return Request(
        "PUT",
        f"{TRUST_PATH}/{identity}",
        headers={"Content-Type": JSON_CONTENT_TYPE, **cookie},
        params=identity.fields(),
        body=RequestBody.of(body),
    )


def version_in(uploads: OwnUploads, response: Response, person: PersonKey) -> TrustList:
    """The version of ``person``'s list the drop ``response`` names holds, checked as theirs."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    bundle = SignedBundle.decoded(strip_targeting(b"".join(content_chunks(uploads, drop_id))))
    bundle.verified(person.public_key)
    assert bundle.signer == person.person_id
    return TrustList.from_value(loads(bundle.contents))


def stored(truth: CasStore, uploads: OwnUploads, response: Response) -> ContentId:
    """The drop ``response`` names, stored in ``truth`` as the validator stores an upload."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    truth.write(drop_id, uploads.read(drop_id))
    return drop_id


def held_version(truth: CasStore, person: PersonKey, trust_list: TrustList) -> ContentId:
    """``trust_list``, signed by ``person``, stored in ``truth`` as a drop at their list's."""
    drop = TrustList.drop_target(person.person_id).placed(
        person.signed(dumps(trust_list.value())).encoded(), 0, minimum_bits=MIN_TRUST_LIST_BITS
    )
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def blocked(recorder: Recorder) -> list[dict[str, Any]]:
    """What this node was asked to block."""
    return recorder.payloads(EventType.DATA_BLOCKED)


def reading(
    reads: TrustReads, cookie: Mapping[str, str], identity: ContentId | None = None
) -> Response:
    """What ``GET /data/trust`` answers; or, for ``identity``, ``GET /data/trust/{id}``."""
    if identity is None:
        return reads.scores(Request("GET", TRUST_PATH, headers=dict(cookie)))

    return reads.score(
        Request("GET", f"{TRUST_PATH}/{identity}", headers=dict(cookie), params=identity.fields())
    )


def hold_list(truth: CasStore, person: PersonKey, entries: Mapping[ContentId, bool]) -> None:
    """Hold ``person``'s key, and a first version of their list saying ``entries``."""
    truth.write(person.person_id, person.public_key)
    held_version(truth, person, TrustList(entries, 1))


def searched(recorder: Recorder) -> list[str]:
    """Each prefix searched for, in order: each list read is its drop's target."""
    return [payload["prefix"] for payload in recorder.payloads(EventType.SEARCH_REQUESTED)]


def drops_placed(recorder: Recorder, person: PersonKey) -> list[dict[str, Any]]:
    """The drops this node uploaded, as the validator is told of them: all but ``person``'s key."""
    return [
        placed
        for placed in recorder.payloads(EventType.PUT_COMPLETED)
        if placed["hash"] != person.person_id.hash
    ]


@mark.parametrize("trusted", [True, None])
def test_no_one_signed_in_is_401_and_places_nothing(
    edits: TrustEdits, person: PersonKey, recorder: Recorder, trusted: bool | None
) -> None:
    response = (edits.trust if trusted else edits.clear)(changing(BOB, {}, trusted))

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert response.headers["Cache-Control"] == "no-store"
    assert not drops_placed(recorder, person)


def test_a_fallback_that_has_not_taken_over_changes_nothing(
    edits: TrustEdits, sessions: Sessions, person: PersonKey, recorder: Recorder
) -> None:
    fallback = NewIdentity.generate(2048).fallback
    started = sessions.start(Session(Username.create("alice"), fallback))

    response = edits.trust(changing(BOB, {"Cookie": started.partition(";")[0]}))

    assert response.status == 403
    assert response.headers["Cache-Control"] == "no-store"
    assert not drops_placed(recorder, person)


def test_trusting_places_the_first_version_signed_by_who_is_signed_in(
    edits: TrustEdits,
    uploads: OwnUploads,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
) -> None:
    response = edits.trust(changing(BOB, cookie))

    assert response.status == 201, response.body
    answer = loads(response.body)
    drop_id = ContentId.parse(answer["id"])
    assert answer == {
        "id": str(drop_id),
        "target": TrustList.drop_target(person.person_id).hex,
        "matching_bits": answer["matching_bits"],
        "signer": str(person.person_id),
        "version": 1,
    }
    assert answer["matching_bits"] >= MIN_TRUST_LIST_BITS
    assert response.headers["Location"] == f"/data/{drop_id}"
    assert response.headers["Cache-Control"] == "no-store"
    assert version_in(uploads, response, person) == TrustList({BOB: True}, 1)
    assert b"".join(content_chunks(uploads, person.person_id)) == person.public_key
    assert {**drop_id.fields(), "node_id": str(NODE_ID)} in drops_placed(recorder, person)


def test_a_change_places_the_next_version_over_the_newest_held(
    edits: TrustEdits,
    truth: CasStore,
    uploads: OwnUploads,
    person: PersonKey,
    cookie: Mapping[str, str],
) -> None:
    first_id = held_version(truth, person, TrustList({BOB: True, CAROL: True}, 1))

    response = edits.trust(changing(DAVE, cookie, False))

    assert response.status == 201, response.body
    assert loads(response.body)["version"] == 2
    assert version_in(uploads, response, person) == TrustList(
        {BOB: True, CAROL: True, DAVE: False}, 2, (first_id,)
    )


def test_two_changes_in_a_row_are_both_kept_before_the_first_is_stored(
    edits: TrustEdits, uploads: OwnUploads, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    first = edits.trust(changing(BOB, cookie))

    second = edits.trust(changing(DAVE, cookie, False))

    assert second.status == 201, second.body
    first_id = ContentId.parse(loads(first.body)["id"])
    assert version_in(uploads, second, person) == TrustList(
        {BOB: True, DAVE: False}, 2, (first_id,)
    )


def test_a_change_is_made_over_the_last_once_the_validator_stores_it(
    edits: TrustEdits,
    truth: CasStore,
    uploads: OwnUploads,
    person: PersonKey,
    cookie: Mapping[str, str],
) -> None:
    first_id = stored(truth, uploads, edits.trust(changing(BOB, cookie)))
    second = edits.trust(changing(DAVE, cookie, False))
    second_id = stored(truth, uploads, second)

    third = edits.clear(changing(BOB, cookie, None))

    assert version_in(uploads, second, person) == TrustList(
        {BOB: True, DAVE: False}, 2, (first_id,)
    )
    assert version_in(uploads, third, person) == TrustList({DAVE: False}, 3, (second_id,))


def test_one_persons_changes_are_made_one_at_a_time(
    edits: TrustEdits, uploads: OwnUploads, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    named = [ContentId.for_data(b"Public key %d" % index, "sha256") for index in range(4)]
    answers: list[Response] = []

    def trust(identity: ContentId) -> None:
        answers.append(edits.trust(changing(identity, cookie)))

    threads = [Thread(target=trust, args=(identity,)) for identity in named]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert sorted(loads(answer.body)["version"] for answer in answers) == [1, 2, 3, 4]
    (last,) = [answer for answer in answers if loads(answer.body)["version"] == 4]
    assert version_in(uploads, last, person).entries == dict.fromkeys(named, True)


def test_clearing_an_entry_places_a_version_without_it(
    edits: TrustEdits, uploads: OwnUploads, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    edits.trust(changing(BOB, cookie, False))

    response = edits.clear(changing(BOB, cookie, None))

    assert response.status == 201, response.body
    assert version_in(uploads, response, person).entries == {}


@mark.parametrize("trusted", [True, None])
def test_a_change_that_changes_nothing_is_204_and_places_nothing(
    edits: TrustEdits,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
    trusted: bool | None,
) -> None:
    if trusted:
        edits.trust(changing(BOB, cookie))

    placed = len(drops_placed(recorder, person))

    response = (edits.trust if trusted else edits.clear)(changing(BOB, cookie, trusted))

    assert response.status == 204
    assert response.body == b""
    assert response.headers["Cache-Control"] == "no-store"
    assert len(drops_placed(recorder, person)) == placed


def test_a_change_naming_the_person_is_400(
    edits: TrustEdits, person: PersonKey, cookie: Mapping[str, str], recorder: Recorder
) -> None:
    response = edits.trust(changing(person.person_id, cookie))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert response.headers["Cache-Control"] == "no-store"
    assert not drops_placed(recorder, person)


@mark.parametrize("body", [b"{}", b'{"trust": 1}', b'{"trust": null}', b"[true]"])
def test_a_body_that_neither_trusts_nor_distrusts_is_400(
    edits: TrustEdits, person: PersonKey, cookie: Mapping[str, str], recorder: Recorder, body: bytes
) -> None:
    response = edits.trust(sending(BOB, cookie, body))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert not drops_placed(recorder, person)


def test_an_identifier_that_is_not_valid_is_400(
    edits: TrustEdits, cookie: Mapping[str, str]
) -> None:
    request = Request(
        "DELETE",
        f"{TRUST_PATH}/sha256/not-a-hash",
        headers=dict(cookie),
        params={"algorithm": "sha256", "hash": "not-a-hash"},
        body=RequestBody.of(b""),
    )

    response = edits.clear(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_CONTENT_ADDRESS
    assert response.headers["Cache-Control"] == "no-store"


def test_while_a_version_found_is_not_held_a_change_is_503_and_it_is_asked_for(
    storage: StorageConfig,
    edits: TrustEdits,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
) -> None:
    target = TrustList.drop_target(person.person_id).hex
    elsewhere = ContentId("sha256", f"{int(target, 16) ^ 1:064x}")
    SearchCache.of(storage).save_results(target, [elsewhere])

    response = edits.trust(changing(BOB, cookie))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert response.headers["Cache-Control"] == "no-store"
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [elsewhere.fields()]
    assert not drops_placed(recorder, person)
    assert not blocked(recorder)


def test_the_first_version_blocks_nothing(
    edits: TrustEdits, cookie: Mapping[str, str], recorder: Recorder
) -> None:
    edits.trust(changing(BOB, cookie))

    assert not blocked(recorder)


def test_a_version_placed_over_another_has_it_blocked(
    edits: TrustEdits, cookie: Mapping[str, str], recorder: Recorder
) -> None:
    first = edits.trust(changing(BOB, cookie))

    edits.trust(changing(DAVE, cookie, False))

    assert blocked(recorder) == [ContentId.parse(loads(first.body)["id"]).fields()]


def test_every_version_read_is_blocked_once_a_newer_is_placed(
    edits: TrustEdits,
    truth: CasStore,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
) -> None:
    first_id = held_version(truth, person, TrustList({BOB: True}, 1))
    second_id = held_version(truth, person, TrustList({BOB: True, CAROL: True}, 2, (first_id,)))

    response = edits.trust(changing(DAVE, cookie, False))

    assert loads(response.body)["version"] == 3
    assert blocked(recorder) == [first_id.fields(), second_id.fields()]


def test_a_change_that_changes_nothing_blocks_what_the_newest_replaces(
    edits: TrustEdits,
    truth: CasStore,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
) -> None:
    first_id = held_version(truth, person, TrustList({BOB: True}, 1))
    held_version(truth, person, TrustList({BOB: True, CAROL: True}, 2, (first_id,)))

    response = edits.trust(changing(CAROL, cookie))

    assert response.status == 204
    assert blocked(recorder) == [first_id.fields()]


def test_a_list_that_does_not_fit_in_an_object_is_413(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    sessions: Sessions,
    recorder: Recorder,
    cookie: Mapping[str, str],
) -> None:
    edits = trust_edits(storage, truth, uploads, sessions, recorder, max_object_bytes=400)

    response = edits.trust(changing(BOB, cookie))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE
    assert response.headers["Cache-Control"] == "no-store"


def test_a_negative_retry_after_is_refused(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    sessions: Sessions,
    recorder: Recorder,
) -> None:
    search = SearchHandler(
        LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
    )

    with raises(ValueError, match="retry_after_seconds"):
        TrustEdits.of(
            TrustLists(search, uploads),
            DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS),
            sessions,
            recorder,
            retry_after_seconds=-1,
        )


@mark.parametrize("identity", [None, BOB])
def test_with_no_one_signed_in_reading_the_scores_is_401(
    reads: TrustReads, identity: ContentId | None
) -> None:
    response = reading(reads, {}, identity)

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert response.headers["Cache-Control"] == "no-store"


def test_the_scores_read_are_what_the_build_has_read_and_whether_it_is_complete(
    reads: TrustReads, truth: CasStore, person: PersonKey, cookie: Mapping[str, str], jobs: Jobs
) -> None:
    hold_list(truth, person, {BOB: True, CAROL: False})

    before = reading(reads, cookie)
    jobs.run()
    after = reading(reads, cookie)

    assert before.status == 200
    assert before.headers["Cache-Control"] == "no-store"
    assert loads(before.body) == {"complete": False, "scores": {}}
    assert after.status == 200
    assert loads(after.body) == {
        "complete": True,
        "scores": {str(BOB): {"score": 1, "step": 1}, str(CAROL): {"score": -1, "step": 1}},
    }


def test_one_identitys_score_is_read_and_one_not_reached_scores_0_at_no_step(
    reads: TrustReads, truth: CasStore, person: PersonKey, cookie: Mapping[str, str], jobs: Jobs
) -> None:
    hold_list(truth, person, {BOB: True})
    jobs.run()

    bob, dave, own = (reading(reads, cookie, named) for named in (BOB, DAVE, person.person_id))

    assert bob.status == 200
    assert bob.headers["Cache-Control"] == "no-store"
    assert loads(bob.body) == {"id": str(BOB), "score": 1, "step": 1, "complete": True}
    assert loads(dave.body) == {"id": str(DAVE), "score": 0, "step": None, "complete": True}
    assert loads(own.body) == {
        "id": str(person.person_id),
        "score": 0,
        "step": None,
        "complete": True,
    }


def test_reading_the_score_of_an_identifier_that_is_not_valid_is_400(
    reads: TrustReads, cookie: Mapping[str, str]
) -> None:
    request = Request(
        "GET",
        f"{TRUST_PATH}/sha256/not-a-hash",
        headers=dict(cookie),
        params={"algorithm": "sha256", "hash": "not-a-hash"},
    )

    response = reads.score(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_CONTENT_ADDRESS
    assert response.headers["Cache-Control"] == "no-store"


def test_a_fallback_that_has_not_taken_over_reads_its_scores(
    reads: TrustReads, sessions: Sessions, jobs: Jobs
) -> None:
    fallback = NewIdentity.generate(2048).fallback
    started = sessions.start(Session(Username.create("alice"), fallback))
    jobs.run()

    response = reading(reads, {"Cookie": started.partition(";")[0]})

    assert response.status == 200
    assert loads(response.body) == {"complete": True, "scores": {}}


def test_reading_scores_let_go_of_since_the_session_was_found_is_401(
    reads: TrustReads, builds: TrustBuilds, person: PersonKey, cookie: Mapping[str, str]
) -> None:
    builds.last_session_ended(person.person_id)

    response = reading(reads, cookie)

    assert response.status == 401
    assert problem_type(response) == SESSION_REQUIRED
    assert response.headers["Cache-Control"] == "no-store"


def test_a_change_scores_again_without_reading_again_what_the_build_holds(
    edits: TrustEdits,
    reads: TrustReads,
    truth: CasStore,
    person: PersonKey,
    cookie: Mapping[str, str],
    recorder: Recorder,
    jobs: Jobs,
) -> None:
    hold_list(truth, person, {BOB: True})
    jobs.run()
    read_at_sign_in = len(searched(recorder))

    response = edits.trust(changing(CAROL, cookie))
    changed = loads(reading(reads, cookie).body)
    jobs.run()
    built = loads(reading(reads, cookie).body)

    assert response.status == 201
    # The change reads the person's list, and the build only Carol's, newly reached.
    assert searched(recorder)[read_at_sign_in:] == [
        TrustList.drop_target(person.person_id).hex,
        TrustList.drop_target(CAROL).hex,
    ]
    assert changed == {
        "complete": False,
        "scores": {str(BOB): {"score": 1, "step": 1}, str(CAROL): {"score": 1, "step": 1}},
    }
    assert built == {**changed, "complete": True}


def test_a_change_that_changes_nothing_hands_the_build_nothing(
    edits: TrustEdits, truth: CasStore, person: PersonKey, cookie: Mapping[str, str], jobs: Jobs
) -> None:
    hold_list(truth, person, {BOB: True})
    jobs.run()

    response = edits.trust(changing(BOB, cookie))

    assert response.status == 204
    assert jobs.waiting == []
