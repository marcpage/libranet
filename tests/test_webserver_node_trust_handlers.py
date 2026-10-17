"""Tests for reading and changing this node's own trust list, for ``/config`` (HttpApi §11.7)."""

from __future__ import annotations
from json import dumps, loads
from threading import Thread
from typing import Any, Mapping

from pytest import fixture, mark, raises

from libranet.bundle.content import content_chunks
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.trust import (
    MIN_TRUST_LIST_BITS,
    HeldTrustList,
    TrustLimits,
    TrustList,
    TrustScore,
)
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_UNAVAILABLE, INVALID_CONTENT_ADDRESS, INVALID_REQUEST
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.config_handlers import CONFIG_TRUST_PATH
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.node_trust_handlers import OwnTrust
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.trust_builds import TrustBuilds
from libranet.webserver.trust_lists import TrustLists

from tests.helpers import new_identity, problem_type

BOB = ContentId.for_data(b"Bob's node's public key", "sha256")
DAVE = ContentId.for_data(b"Dave's node's public key", "sha256")
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
def node() -> NodeIdentity:
    return new_identity()


@fixture
def recorder() -> Recorder:
    return Recorder()


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return CasStore.source_of_truth(storage)


@fixture
def uploads(
    storage: StorageConfig, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> OwnUploads:
    return OwnUploads.of(storage, truth, node.node_id, recorder)


@fixture
def search(storage: StorageConfig, truth: CasStore, recorder: Recorder) -> SearchHandler:
    return SearchHandler(
        LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
    )


@fixture
def trust(
    search: SearchHandler, uploads: OwnUploads, node: NodeIdentity, recorder: Recorder
) -> OwnTrust:
    """The node's trust endpoints over what is held, as ``/config``'s port makes them."""
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)
    return OwnTrust.of(
        node, search, drops, recorder, content=uploads, retry_after_seconds=RETRY_AFTER_SECONDS
    )


def reading() -> Request:
    """``GET /config/api/trust``."""
    return Request("GET", CONFIG_TRUST_PATH, body=RequestBody.of(b""))


def changing(identity: ContentId, trusted: bool | None = True) -> Request:
    """``PUT`` trusting or distrusting ``identity``, as ``trusted`` says; ``DELETE`` if ``None``."""
    if trusted is None:
        return Request(
            "DELETE",
            f"{CONFIG_TRUST_PATH}/{identity}",
            params=identity.fields(),
            body=RequestBody.of(b""),
        )

    return sending(identity, dumps({"trust": trusted}).encode("utf-8"))


def sending(identity: ContentId, body: bytes) -> Request:
    """``PUT`` of ``body`` to the entry for ``identity``."""
    return Request(
        "PUT",
        f"{CONFIG_TRUST_PATH}/{identity}",
        headers={"Content-Type": JSON_CONTENT_TYPE},
        params=identity.fields(),
        body=RequestBody.of(body),
    )


def version_in(uploads: OwnUploads, response: Response, node: NodeIdentity) -> TrustList:
    """The version of ``node``'s list the drop ``response`` names holds, checked as its."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    bundle = SignedBundle.decoded(strip_targeting(b"".join(content_chunks(uploads, drop_id))))
    bundle.verified(node.public_key)
    assert bundle.signer == node.node_id
    return TrustList.from_value(loads(bundle.contents))


def stored(truth: CasStore, uploads: OwnUploads, response: Response) -> ContentId:
    """The drop ``response`` names, stored in ``truth`` as the validator stores an upload."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    truth.write(drop_id, uploads.read(drop_id))
    return drop_id


def held_version(truth: CasStore, node: NodeIdentity, trust_list: TrustList) -> ContentId:
    """``trust_list``, signed by ``node``, stored in ``truth`` as a drop at its list's."""
    drop = TrustList.drop_target(node.node_id).placed(
        node.signed(dumps(trust_list.value())).encoded(), 0, minimum_bits=MIN_TRUST_LIST_BITS
    )
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def blocked(recorder: Recorder) -> list[dict[str, Any]]:
    """What this node was asked to block."""
    return recorder.payloads(EventType.DATA_BLOCKED)


def drops_placed(recorder: Recorder, node: NodeIdentity) -> list[dict[str, Any]]:
    """The drops this node uploaded, as the validator is told of them: all but its key."""
    return [
        placed
        for placed in recorder.payloads(EventType.PUT_COMPLETED)
        if placed["hash"] != node.node_id.hash
    ]


def test_a_node_that_trusts_no_one_reads_as_an_empty_list(trust: OwnTrust) -> None:
    response = trust.read(reading())

    assert response.status == 200
    assert loads(response.body) == {"id": None, "entries": {}, "version": None}


def test_trusting_places_the_first_version_signed_by_the_node(
    trust: OwnTrust, uploads: OwnUploads, node: NodeIdentity, recorder: Recorder
) -> None:
    response = trust.trust(changing(BOB))

    assert response.status == 201, response.body
    answer = loads(response.body)
    drop_id = ContentId.parse(answer["id"])
    assert answer == {
        "id": str(drop_id),
        "target": TrustList.drop_target(node.node_id).hex,
        "matching_bits": answer["matching_bits"],
        "signer": str(node.node_id),
        "version": 1,
    }
    assert answer["matching_bits"] >= MIN_TRUST_LIST_BITS
    # This port serves nothing beneath /data for one to name.
    assert "Location" not in response.headers
    assert version_in(uploads, response, node) == TrustList({BOB: True}, 1)
    assert b"".join(content_chunks(uploads, node.node_id)) == node.public_key
    assert {**drop_id.fields(), "node_id": str(node.node_id)} in drops_placed(recorder, node)


def test_what_a_change_placed_is_read_before_the_validator_stores_it(trust: OwnTrust) -> None:
    placed = loads(trust.trust(changing(BOB)).body)

    response = trust.read(reading())

    assert response.status == 200
    assert loads(response.body) == {
        "id": placed["id"],
        "entries": {str(BOB): True},
        "version": 1,
    }


def test_the_newest_version_the_node_signed_is_read_with_the_drop_holding_it(
    trust: OwnTrust, truth: CasStore, node: NodeIdentity
) -> None:
    first_id = held_version(truth, node, TrustList({BOB: True}, 1))
    second_id = held_version(truth, node, TrustList({BOB: True, DAVE: False}, 2, (first_id,)))

    response = trust.read(reading())

    assert loads(response.body) == {
        "id": str(second_id),
        "entries": {str(BOB): True, str(DAVE): False},
        "version": 2,
        "versions": [str(first_id)],
    }


def test_a_list_the_node_placed_is_read_as_its_own_by_any_reader(
    trust: OwnTrust, search: SearchHandler, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    stored(truth, uploads, trust.trust(changing(BOB)))
    truth.write(node.node_id, node.public_key)

    # As a person's build reads it, who trusts this node (Trust Network §4.1).
    held = TrustLists(search, truth).read(node.node_id)

    assert held.entries == {BOB: True}
    assert not held.missing


def test_distrusting_and_clearing_each_place_the_next_version(
    trust: OwnTrust, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    first_id = stored(truth, uploads, trust.trust(changing(BOB)))
    second = trust.trust(changing(DAVE, False))
    second_id = stored(truth, uploads, second)

    third = trust.clear(changing(BOB, None))

    assert third.status == 201, third.body
    assert version_in(uploads, second, node) == TrustList({BOB: True, DAVE: False}, 2, (first_id,))
    assert version_in(uploads, third, node) == TrustList({DAVE: False}, 3, (second_id,))


@mark.parametrize("trusted", [True, None])
def test_a_change_that_changes_nothing_is_204_and_places_nothing(
    trust: OwnTrust, node: NodeIdentity, recorder: Recorder, trusted: bool | None
) -> None:
    trust.trust(changing(BOB))
    placed = len(drops_placed(recorder, node))
    named = BOB if trusted else DAVE

    response = trust.trust(changing(named)) if trusted else trust.clear(changing(named, None))

    assert response.status == 204
    assert response.body == b""
    assert len(drops_placed(recorder, node)) == placed


def test_a_change_naming_the_node_itself_is_400(
    trust: OwnTrust, node: NodeIdentity, recorder: Recorder
) -> None:
    response = trust.trust(changing(node.node_id))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert not drops_placed(recorder, node)


@mark.parametrize("body", [b"{}", b'{"trust": 1}', b'{"trust": null}', b"[true]"])
def test_a_body_that_neither_trusts_nor_distrusts_is_400(
    trust: OwnTrust, node: NodeIdentity, recorder: Recorder, body: bytes
) -> None:
    response = trust.trust(sending(BOB, body))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert not drops_placed(recorder, node)


def test_an_identifier_that_is_not_valid_is_400(trust: OwnTrust) -> None:
    request = Request(
        "DELETE",
        f"{CONFIG_TRUST_PATH}/sha256/not-a-hash",
        params={"algorithm": "sha256", "hash": "not-a-hash"},
        body=RequestBody.of(b""),
    )

    response = trust.clear(request)

    assert response.status == 400
    assert problem_type(response) == INVALID_CONTENT_ADDRESS


@mark.parametrize("method", ["read", "trust"])
def test_while_a_version_found_is_not_held_both_are_503_and_it_is_asked_for(
    storage: StorageConfig, trust: OwnTrust, node: NodeIdentity, recorder: Recorder, method: str
) -> None:
    target = TrustList.drop_target(node.node_id).hex
    elsewhere = ContentId("sha256", f"{int(target, 16) ^ 1:064x}")
    SearchCache.of(storage).save_results(target, [elsewhere])

    response = trust.read(reading()) if method == "read" else trust.trust(changing(BOB))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert "this node's trust list" in loads(response.body)["detail"]
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [elsewhere.fields()]
    assert not drops_placed(recorder, node)
    assert not blocked(recorder)


def test_the_nodes_own_key_is_held_again_should_it_have_been_evicted(
    trust: OwnTrust, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    held_version(truth, node, TrustList({BOB: True}, 1))

    response = trust.read(reading())

    # Not held beforehand, and checked under once it is.
    assert loads(response.body)["entries"] == {str(BOB): True}
    assert b"".join(content_chunks(uploads, node.node_id)) == node.public_key


def test_a_version_placed_over_another_has_it_blocked(trust: OwnTrust, recorder: Recorder) -> None:
    first = trust.trust(changing(BOB))
    assert not blocked(recorder)

    trust.trust(changing(DAVE, False))

    assert blocked(recorder) == [ContentId.parse(loads(first.body)["id"]).fields()]


def test_reading_blocks_nothing(
    trust: OwnTrust, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> None:
    first_id = held_version(truth, node, TrustList({BOB: True}, 1))
    held_version(truth, node, TrustList({DAVE: False}, 2, (first_id,)))

    trust.read(reading())

    assert not blocked(recorder)


def test_changes_are_made_one_at_a_time(
    trust: OwnTrust, uploads: OwnUploads, node: NodeIdentity
) -> None:
    named = [ContentId.for_data(b"Public key %d" % index, "sha256") for index in range(4)]
    answers: list[Response] = []

    def trusting(identity: ContentId) -> None:
        answers.append(trust.trust(changing(identity)))

    threads = [Thread(target=trusting, args=(identity,)) for identity in named]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert sorted(loads(answer.body)["version"] for answer in answers) == [1, 2, 3, 4]
    (last,) = [answer for answer in answers if loads(answer.body)["version"] == 4]
    assert version_in(uploads, last, node).entries == dict.fromkeys(named, True)


def test_a_negative_retry_after_is_refused(
    search: SearchHandler, uploads: OwnUploads, node: NodeIdentity, recorder: Recorder
) -> None:
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)

    with raises(ValueError, match="retry_after_seconds"):
        OwnTrust.of(node, search, drops, recorder, content=uploads, retry_after_seconds=-1)


class NoLists:
    """Stands in for the trust list reader of the node's own build, which holds no list."""

    def read(self, owner: ContentId) -> HeldTrustList:
        return HeldTrustList(owner)


@fixture
def builds(recorder: Recorder) -> TrustBuilds:
    """A build of the node's own scores, which reads nothing unless a test runs it."""
    return TrustBuilds(
        NoLists(),
        recorder,
        TrustLimits(4, 1000),
        wait_seconds=0,
        ask_again_seconds=RETRY_AFTER_SECONDS,
        in_background=lambda job: None,
    )


def test_each_version_placed_is_handed_to_the_node_s_own_build(
    search: SearchHandler,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
    builds: TrustBuilds,
) -> None:
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)
    trust = OwnTrust.of(
        node,
        search,
        drops,
        recorder,
        content=uploads,
        retry_after_seconds=RETRY_AFTER_SECONDS,
        builds=builds,
    )
    builds.session_started(node.node_id)

    assert trust.trust(changing(BOB)).status == 201
    assert trust.trust(changing(BOB)).status == 204
    assert trust.trust(changing(node.node_id)).status == 400
    scores = builds.scores(node.node_id)

    assert scores is not None
    assert scores.scores == {BOB: TrustScore(1, 1)}
