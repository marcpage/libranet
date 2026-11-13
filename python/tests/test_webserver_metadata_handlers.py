"""Tests for reading and changing what this node says of itself, for ``/config`` (HttpApi §11.8)."""

from __future__ import annotations
from json import dumps, loads
from logging import DEBUG
from secrets import token_hex
from threading import Thread
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture, mark, raises

from libranet.bundle.content import content_chunks
from libranet.bundle.protection import strip_targeting
from libranet.bundle.signing import SignedBundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.node_metadata import NodeMetadata
from libranet.identity.people import NewIdentity
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_TOO_LARGE, CONTENT_UNAVAILABLE, INVALID_REQUEST
from libranet.protocol.http_syntax import JSON_CONTENT_TYPE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.config_handlers import METADATA_PATH
from libranet.webserver.drop_handler import DropHandler
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.metadata_handlers import OwnMetadata
from libranet.webserver.search_handler import SearchHandler

from tests.helpers import new_identity, problem_type

MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 20
# The fewest bits the search returns, fewer than a node's, to be quick to match.
MINIMUM_BITS = 16
RETRY_AFTER_SECONDS = 3
ATTIC = {"name": "Attic", "city": "Springfield"}
SIGNED_VERSIONS_LOGGER = "libranet.webserver.signed_versions"


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


def own_metadata(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
    max_object_bytes: int = MAX_BYTES,
) -> OwnMetadata:
    """The metadata endpoints over ``truth``, as ``/config``'s port makes them.

    Each drop is stored in an object no larger than ``max_object_bytes``.
    """
    search = SearchHandler(
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        SearchCache.of(storage),
        recorder,
    )
    drops = DropHandler(uploads, max_object_bytes, MAX_SECONDS, MAX_MINIMUM_BITS)
    return OwnMetadata.of(
        node, search, drops, recorder, content=uploads, retry_after_seconds=RETRY_AFTER_SECONDS
    )


@fixture
def metadata(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
) -> OwnMetadata:
    return own_metadata(storage, truth, uploads, node, recorder)


def reading() -> Request:
    """``GET /config/api/metadata``."""
    return Request("GET", METADATA_PATH, body=RequestBody.of(b""))


def changing(fields: Mapping[str, str]) -> Request:
    """``PUT /config/api/metadata`` saying ``fields``."""
    return sending(dumps({"fields": fields}).encode("utf-8"))


def sending(body: bytes) -> Request:
    """``PUT`` of ``body`` to ``/config/api/metadata``."""
    return Request(
        "PUT",
        METADATA_PATH,
        headers={"Content-Type": JSON_CONTENT_TYPE},
        body=RequestBody.of(body),
    )


def version_in(uploads: OwnUploads, response: Response, node: NodeIdentity) -> NodeMetadata:
    """The version of ``node``'s metadata the drop ``response`` names holds, checked as its."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    bundle = SignedBundle.decoded(strip_targeting(b"".join(content_chunks(uploads, drop_id))))
    bundle.verified(node.public_key)
    assert bundle.signer == node.node_id
    return NodeMetadata.from_value(loads(bundle.contents))


def stored(truth: CasStore, uploads: OwnUploads, response: Response) -> ContentId:
    """The drop ``response`` names, stored in ``truth`` as the validator stores an upload."""
    drop_id = ContentId.parse(loads(response.body)["id"])
    truth.write(drop_id, uploads.read(drop_id))
    return drop_id


def held_version(truth: CasStore, node: NodeIdentity, said: NodeMetadata) -> ContentId:
    """``said``, signed by ``node``, stored in ``truth`` as a drop at its metadata's."""
    drop = NodeMetadata.drop_target(node.node_id).placed(
        node.signed(dumps(said.value())).encoded(), 0, minimum_bits=MINIMUM_BITS
    )
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def shunned(recorder: Recorder) -> list[dict[str, Any]]:
    """What this node was asked to shun."""
    return recorder.payloads(EventType.DATA_SHUNNED)


def drops_placed(recorder: Recorder, node: NodeIdentity) -> list[dict[str, Any]]:
    """The drops this node uploaded, as the validator is told of them: all but its key."""
    return [
        placed
        for placed in recorder.payloads(EventType.PUT_COMPLETED)
        if placed["hash"] != node.node_id.hash
    ]


def test_a_node_that_has_said_nothing_reads_as_saying_nothing(metadata: OwnMetadata) -> None:
    response = metadata.read(reading())

    assert response.status == 200
    assert loads(response.body) == {"id": None, "fields": {}, "version": None}


def test_a_change_places_the_first_version_signed_by_the_node(
    metadata: OwnMetadata, uploads: OwnUploads, node: NodeIdentity, recorder: Recorder
) -> None:
    response = metadata.change(changing(ATTIC))

    assert response.status == 201, response.body
    answer = loads(response.body)
    drop_id = ContentId.parse(answer["id"])
    assert answer == {
        "id": str(drop_id),
        "target": NodeMetadata.drop_target(node.node_id).hex,
        "matching_bits": answer["matching_bits"],
        "signer": str(node.node_id),
        "version": 1,
    }
    assert answer["matching_bits"] >= MINIMUM_BITS
    # This port serves nothing beneath /data for one to name.
    assert "Location" not in response.headers
    assert version_in(uploads, response, node) == NodeMetadata(ATTIC, 1)
    assert b"".join(content_chunks(uploads, node.node_id)) == node.public_key
    assert {**drop_id.fields(), "node_id": str(node.node_id)} in drops_placed(recorder, node)


def test_what_a_change_placed_is_read_before_the_validator_stores_it(
    metadata: OwnMetadata,
) -> None:
    placed = loads(metadata.change(changing(ATTIC)).body)

    response = metadata.read(reading())

    assert response.status == 200
    assert loads(response.body) == {"id": placed["id"], "fields": ATTIC, "version": 1}


def test_the_newest_version_held_is_read_with_the_drop_holding_it(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity
) -> None:
    first_id = held_version(truth, node, NodeMetadata({"name": "Old"}, 1))
    second_id = held_version(truth, node, NodeMetadata(ATTIC, 2, (first_id,)))

    response = metadata.read(reading())

    assert loads(response.body) == {
        "id": str(second_id),
        "fields": ATTIC,
        "version": 2,
        "versions": [str(first_id)],
    }


def test_a_change_places_the_next_version_over_the_newest_held(
    metadata: OwnMetadata, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    first_id = held_version(truth, node, NodeMetadata({"name": "Old"}, 1))

    response = metadata.change(changing(ATTIC))

    assert response.status == 201, response.body
    assert loads(response.body)["version"] == 2
    assert version_in(uploads, response, node) == NodeMetadata(ATTIC, 2, (first_id,))


def test_two_changes_in_a_row_are_both_kept_before_the_first_is_stored(
    metadata: OwnMetadata, uploads: OwnUploads, node: NodeIdentity
) -> None:
    first = metadata.change(changing({"name": "Old"}))

    second = metadata.change(changing(ATTIC))

    first_id = ContentId.parse(loads(first.body)["id"])
    assert version_in(uploads, second, node) == NodeMetadata(ATTIC, 2, (first_id,))


def test_a_change_is_made_over_the_last_once_the_validator_stores_it(
    metadata: OwnMetadata, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    first_id = stored(truth, uploads, metadata.change(changing({"name": "Old"})))
    second_id = stored(truth, uploads, metadata.change(changing(ATTIC)))

    third = metadata.change(changing({}))

    assert version_in(uploads, third, node) == NodeMetadata({}, 3, (second_id,))
    assert second_id != first_id


def test_changes_are_made_one_at_a_time(metadata: OwnMetadata) -> None:
    answers: list[Response] = []

    def change(name: str) -> None:
        answers.append(metadata.change(changing({"name": name})))

    threads = [Thread(target=change, args=(f"Node {index}",)) for index in range(4)]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert sorted(loads(answer.body)["version"] for answer in answers) == [1, 2, 3, 4]


def test_a_change_that_changes_nothing_is_204_and_places_nothing(
    metadata: OwnMetadata, node: NodeIdentity, recorder: Recorder
) -> None:
    metadata.change(changing(ATTIC))
    placed = len(drops_placed(recorder, node))

    response = metadata.change(changing(dict(reversed(ATTIC.items()))))

    assert response.status == 204
    assert response.body == b""
    assert len(drops_placed(recorder, node)) == placed


def test_saying_nothing_before_anything_was_said_changes_nothing(
    metadata: OwnMetadata, node: NodeIdentity, recorder: Recorder
) -> None:
    response = metadata.change(changing({}))

    assert response.status == 204
    assert not drops_placed(recorder, node)


@mark.parametrize(
    "body",
    [b"{}", b'{"fields": []}', b'{"fields": {"name": 1}}', b'{"fields": null}', b"[{}]"],
)
def test_a_body_that_is_not_fields_of_strings_is_400(
    metadata: OwnMetadata, node: NodeIdentity, recorder: Recorder, body: bytes
) -> None:
    response = metadata.change(sending(body))

    assert response.status == 400
    assert problem_type(response) == INVALID_REQUEST
    assert not drops_placed(recorder, node)


def test_a_version_someone_else_signed_is_passed_over_at_debug(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity, caplog: LogCaptureFixture
) -> None:
    held_version(truth, node, NodeMetadata(ATTIC, 1))
    impostor = new_identity()
    forged = NodeMetadata.drop_target(node.node_id).placed(
        impostor.signed(dumps(NodeMetadata({"name": "Forged"}, 9).value())).encoded(),
        0,
        minimum_bits=MINIMUM_BITS,
    )
    forged_id = ContentId.for_data(forged.data, "sha256")
    truth.write(forged_id, forged.data)

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        response = metadata.read(reading())

    assert loads(response.body)["fields"] == ATTIC
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert f"at {node.node_id}'s metadata, as {impostor.node_id} signed it" in record.getMessage()


def test_what_the_node_signed_that_is_no_version_is_passed_over_at_debug(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity, caplog: LogCaptureFixture
) -> None:
    drop = NodeMetadata.drop_target(node.node_id).placed(
        node.signed(dumps({"entries": {}, "version": 1})).encoded(),
        0,
        minimum_bits=MINIMUM_BITS,
    )
    truth.write(ContentId.for_data(drop.data, "sha256"), drop.data)

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        response = metadata.read(reading())

    assert loads(response.body) == {"id": None, "fields": {}, "version": None}
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert "as it is no version of one" in record.getMessage()


def test_a_persons_signature_is_checked_as_any_other_and_passed_over(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity
) -> None:
    person = NewIdentity.generate(2048).key
    truth.write(person.person_id, person.public_key)
    drop = NodeMetadata.drop_target(node.node_id).placed(
        person.signed(dumps(NodeMetadata({"name": "A person's"}, 1).value())).encoded(),
        0,
        minimum_bits=MINIMUM_BITS,
    )
    truth.write(ContentId.for_data(drop.data, "sha256"), drop.data)

    assert loads(metadata.read(reading()).body)["fields"] == {}


@mark.parametrize("method", ["read", "change"])
def test_while_a_version_found_is_not_held_both_are_503_and_it_is_asked_for(
    storage: StorageConfig,
    metadata: OwnMetadata,
    node: NodeIdentity,
    recorder: Recorder,
    method: str,
) -> None:
    target = NodeMetadata.drop_target(node.node_id).hex
    elsewhere = ContentId("sha256", f"{int(target, 16) ^ 1:064x}")
    SearchCache.of(storage).save_results(target, [elsewhere])

    response = metadata.read(reading()) if method == "read" else metadata.change(changing(ATTIC))

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert recorder.payloads(EventType.DATA_NOT_FOUND) == [elsewhere.fields()]
    assert not drops_placed(recorder, node)
    assert not shunned(recorder)


def test_the_nodes_own_key_is_held_again_should_it_have_been_evicted(
    metadata: OwnMetadata, truth: CasStore, uploads: OwnUploads, node: NodeIdentity
) -> None:
    held_version(truth, node, NodeMetadata(ATTIC, 1))

    response = metadata.read(reading())

    # Not held beforehand, and checked under once it is.
    assert loads(response.body)["fields"] == ATTIC
    assert b"".join(content_chunks(uploads, node.node_id)) == node.public_key


def test_a_version_placed_over_another_has_it_shunned(
    metadata: OwnMetadata, recorder: Recorder
) -> None:
    first = metadata.change(changing({"name": "Old"}))
    assert not shunned(recorder)

    metadata.change(changing(ATTIC))

    assert shunned(recorder) == [ContentId.parse(loads(first.body)["id"]).fields()]


def test_every_version_read_is_shunned_once_a_newer_is_placed(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> None:
    first_id = held_version(truth, node, NodeMetadata({"name": "Old"}, 1))
    second_id = held_version(truth, node, NodeMetadata({"name": "Older"}, 2, (first_id,)))

    response = metadata.change(changing(ATTIC))

    assert loads(response.body)["version"] == 3
    assert shunned(recorder) == [first_id.fields(), second_id.fields()]


def test_a_change_that_changes_nothing_blocks_what_the_newest_replaces(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> None:
    first_id = held_version(truth, node, NodeMetadata({"name": "Old"}, 1))
    held_version(truth, node, NodeMetadata(ATTIC, 2, (first_id,)))

    response = metadata.change(changing(ATTIC))

    assert response.status == 204
    assert shunned(recorder) == [first_id.fields()]


def test_reading_blocks_nothing(
    metadata: OwnMetadata, truth: CasStore, node: NodeIdentity, recorder: Recorder
) -> None:
    first_id = held_version(truth, node, NodeMetadata({"name": "Old"}, 1))
    held_version(truth, node, NodeMetadata(ATTIC, 2, (first_id,)))

    metadata.read(reading())

    assert not shunned(recorder)


def test_metadata_that_does_not_fit_in_an_object_is_413(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
) -> None:
    metadata = own_metadata(storage, truth, uploads, node, recorder, max_object_bytes=400)

    response = metadata.change(changing({"description": token_hex(500)}))

    assert response.status == 413
    assert problem_type(response) == CONTENT_TOO_LARGE


def test_a_negative_retry_after_is_refused(
    storage: StorageConfig,
    truth: CasStore,
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
) -> None:
    search = SearchHandler(
        LocalSearch(truth, min_bits=MINIMUM_BITS),
        SearchCache.of(storage),
        recorder,
    )

    with raises(ValueError, match="retry_after_seconds"):
        OwnMetadata.of(
            node,
            search,
            DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS),
            recorder,
            content=uploads,
            retry_after_seconds=-1,
        )
