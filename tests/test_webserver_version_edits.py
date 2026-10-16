"""Tests for placing the next version of a document an identity signs, as a change asks."""

from __future__ import annotations
from json import dumps
from typing import Any, Mapping

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.identity.node_metadata import (
    MIN_NODE_METADATA_BITS,
    HeldNodeMetadata,
    MetadataVersion,
    NodeMetadata,
)
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.problems import CONTENT_TOO_LARGE, CONTENT_UNAVAILABLE
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.bundle_edits import OwnUploads
from libranet.webserver.drop_handler import DropHandler, StoredDrop
from libranet.webserver.http_types import Request, RequestBody, Response
from libranet.webserver.search_handler import SearchHandler
from libranet.webserver.signed_versions import SignedVersions
from libranet.webserver.version_edits import ReplacedVersions, VersionEdits

from tests.helpers import new_identity, problem_type

MAX_BYTES = 64 * 1024
MAX_SECONDS = 1.0
MAX_MINIMUM_BITS = 20
RETRY_AFTER_SECONDS = 3
DETAIL = "A version found is not stored here yet."
FIRST = ContentId("sha256", "1" * 64)
SECOND = ContentId("sha256", "2" * 64)
THIRD = ContentId("sha256", "3" * 64)
REQUEST = Request("PUT", "/config/api/metadata", body=RequestBody.of(b""))


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
def uploads(storage: StorageConfig, node: NodeIdentity, recorder: Recorder) -> OwnUploads:
    return OwnUploads.of(storage, CasStore.source_of_truth(storage), node.node_id, recorder)


@fixture
def versions(
    storage: StorageConfig, uploads: OwnUploads, recorder: Recorder
) -> SignedVersions[NodeMetadata]:
    truth = CasStore.source_of_truth(storage)
    search = SearchHandler(
        LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), recorder
    )
    return SignedVersions(
        search,
        uploads,
        kind="metadata",
        target=NodeMetadata.drop_target,
        minimum_bits=MIN_NODE_METADATA_BITS,
        parse=NodeMetadata.from_value,
    )


def edits_of(
    versions: SignedVersions[NodeMetadata],
    uploads: OwnUploads,
    recorder: Recorder,
    max_object_bytes: int = MAX_BYTES,
) -> VersionEdits[NodeMetadata]:
    """The edits of ``versions``' kind, each drop stored in at most ``max_object_bytes``."""
    drops = DropHandler(uploads, max_object_bytes, MAX_SECONDS, MAX_MINIMUM_BITS)
    return VersionEdits(versions, drops, recorder, retry_after_seconds=RETRY_AFTER_SECONDS)


@fixture
def edits(
    versions: SignedVersions[NodeMetadata], uploads: OwnUploads, recorder: Recorder
) -> VersionEdits[NodeMetadata]:
    return edits_of(versions, uploads, recorder)


def ids(payloads: list[dict[str, Any]]) -> list[ContentId]:
    """The content each of ``payloads`` names."""
    return [ContentId.from_fields(payload) for payload in payloads]


def test_retry_after_seconds_must_not_be_negative(
    versions: SignedVersions[NodeMetadata], uploads: OwnUploads, recorder: Recorder
) -> None:
    drops = DropHandler(uploads, MAX_BYTES, MAX_SECONDS, MAX_MINIMUM_BITS)

    with raises(ValueError, match="retry_after_seconds"):
        VersionEdits(versions, drops, recorder, retry_after_seconds=-1)


def test_replaced_versions_are_each_asked_to_be_blocked(recorder: Recorder) -> None:
    ReplacedVersions(recorder, "metadata").block(FIRST, (SECOND, THIRD))

    assert ids(recorder.payloads(EventType.DATA_BLOCKED)) == [SECOND, THIRD]


def test_a_version_remembered_is_read_until_a_search_finds_it(
    edits: VersionEdits[NodeMetadata], node: NodeIdentity
) -> None:
    placed = MetadataVersion(FIRST, NodeMetadata({"name": "Attic"}, 1))
    edits.remember(node.node_id, placed)

    assert edits.held(HeldNodeMetadata(node.node_id)).versions == (placed,)
    found = HeldNodeMetadata(node.node_id, (placed,))
    assert edits.held(found) is found
    assert edits.held(HeldNodeMetadata(node.node_id)).versions == ()


def test_what_is_held_for_another_owner_is_not_read_with_it(
    edits: VersionEdits[NodeMetadata], node: NodeIdentity
) -> None:
    edits.remember(node.node_id, MetadataVersion(FIRST, NodeMetadata({}, 1)))
    other = HeldNodeMetadata(SECOND)

    assert edits.held(other) is other


def test_what_is_missing_is_asked_for_and_the_change_held_off(
    edits: VersionEdits[NodeMetadata], node: NodeIdentity, recorder: Recorder
) -> None:
    held = HeldNodeMetadata(node.node_id, missing=(FIRST, SECOND))

    response = edits.missing_response(REQUEST, held, DETAIL)

    assert response.status == 503
    assert problem_type(response) == CONTENT_UNAVAILABLE
    assert response.headers["Retry-After"] == str(RETRY_AFTER_SECONDS)
    assert DETAIL.encode("utf-8") in response.body
    assert ids(recorder.payloads(EventType.DATA_NOT_FOUND)) == [FIRST, SECOND]


def test_every_version_but_the_newest_is_blocked(
    edits: VersionEdits[NodeMetadata], node: NodeIdentity, recorder: Recorder
) -> None:
    held = HeldNodeMetadata(
        node.node_id,
        (
            MetadataVersion(FIRST, NodeMetadata({}, 1)),
            MetadataVersion(THIRD, NodeMetadata({}, 3)),
            MetadataVersion(SECOND, NodeMetadata({}, 2)),
        ),
    )

    edits.block_replaced(held)

    assert ids(recorder.payloads(EventType.DATA_BLOCKED)) == [FIRST, SECOND]


def test_a_version_placed_is_a_drop_at_its_owners_and_the_newest_is_blocked(
    edits: VersionEdits[NodeMetadata],
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
) -> None:
    held = HeldNodeMetadata(node.node_id, (MetadataVersion(FIRST, NodeMetadata({}, 1)),))
    signed = node.signed(dumps(NodeMetadata({"name": "Attic"}, 2, (FIRST,)).value()))

    stored = edits.place(REQUEST, held, signed)

    assert isinstance(stored, StoredDrop)
    target = NodeMetadata.drop_target(node.node_id)
    assert stored.target == target
    assert stored.matching_bits >= MIN_NODE_METADATA_BITS
    assert matching_bits(target.hex, stored.content_id.hash) == stored.matching_bits
    assert uploads.exists(stored.content_id)
    assert ids(recorder.payloads(EventType.DATA_BLOCKED)) == [FIRST]


def test_the_first_version_placed_blocks_nothing(
    edits: VersionEdits[NodeMetadata], node: NodeIdentity, recorder: Recorder
) -> None:
    signed = node.signed(dumps(NodeMetadata({}, 1).value()))

    stored = edits.place(REQUEST, HeldNodeMetadata(node.node_id), signed)

    assert isinstance(stored, StoredDrop)
    assert recorder.payloads(EventType.DATA_BLOCKED) == []


def test_a_version_refused_blocks_nothing(
    versions: SignedVersions[NodeMetadata],
    uploads: OwnUploads,
    node: NodeIdentity,
    recorder: Recorder,
) -> None:
    edits = edits_of(versions, uploads, recorder, max_object_bytes=64)
    held = HeldNodeMetadata(node.node_id, (MetadataVersion(FIRST, NodeMetadata({}, 1)),))
    signed = node.signed(dumps(NodeMetadata({"name": "Attic"}, 2, (FIRST,)).value()))

    refused = edits.place(REQUEST, held, signed)

    assert isinstance(refused, Response)
    assert problem_type(refused) == CONTENT_TOO_LARGE
    assert recorder.payloads(EventType.DATA_BLOCKED) == []
