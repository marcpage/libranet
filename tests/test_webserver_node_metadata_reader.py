"""Tests for reading what a node says of itself from what this node holds (HttpApi §11.8)."""

from __future__ import annotations
from json import dumps
from logging import DEBUG
from typing import Any, Mapping

from pytest import LogCaptureFixture, fixture

from libranet.cas.content_id import ContentId
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
from libranet.protocol.search import LocalSearch, SearchCache
from libranet.webserver.node_metadata_reader import NODE_METADATA_KIND, NodeMetadataReader
from libranet.webserver.search_handler import SearchHandler

from tests.helpers import new_identity

ATTIC = {"name": "Attic", "city": "Springfield"}
SIGNED_VERSIONS_LOGGER = "libranet.webserver.signed_versions"


class Recorder:
    """Stands in for the module's ``publish``, keeping what was published."""

    def __init__(self) -> None:
        self.messages: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        self.messages.append((event, dict(payload or {})))
        return {}


@fixture(scope="module")
def node() -> NodeIdentity:
    return new_identity()


@fixture(scope="module")
def impostor() -> NodeIdentity:
    return new_identity()


@fixture
def truth(storage: StorageConfig, node: NodeIdentity) -> CasStore:
    """The source of truth, holding the node's public key."""
    store = CasStore.source_of_truth(storage)
    store.write(node.node_id, node.public_key)
    return store


@fixture
def reader(storage: StorageConfig, truth: CasStore) -> NodeMetadataReader:
    search = SearchHandler(
        LocalSearch(truth, storage.search_max_results), SearchCache.of(storage), Recorder()
    )
    return NodeMetadataReader(search, truth)


def placed(truth: CasStore, owner: NodeIdentity, signer: NodeIdentity, value: object) -> ContentId:
    """``value``, signed by ``signer``, stored in ``truth`` as a drop at ``owner``'s metadata."""
    data = signer.signed(dumps(value)).encoded()
    drop = NodeMetadata.drop_target(owner.node_id).placed(
        data, 0, minimum_bits=MIN_NODE_METADATA_BITS
    )
    content_id = ContentId.for_data(drop.data, "sha256")
    truth.write(content_id, drop.data)
    return content_id


def test_what_a_node_says_is_read_from_the_versions_held_at_its_drop(
    reader: NodeMetadataReader, truth: CasStore, node: NodeIdentity
) -> None:
    first = NodeMetadata({"name": "Old"}, 1)
    first_id = placed(truth, node, node, first.value())
    second = NodeMetadata(ATTIC, 2, (first_id,))
    second_id = placed(truth, node, node, second.value())

    held = reader.read(node.node_id)

    assert sorted(held.versions, key=lambda version: version.number) == [
        MetadataVersion(first_id, node.node_id, first),
        MetadataVersion(second_id, node.node_id, second),
    ]
    assert held.missing == ()
    assert held.fields == ATTIC


def test_versions_are_read_at_the_nodes_drop_by_the_bits_each_is_placed_at(
    reader: NodeMetadataReader,
) -> None:
    versions = reader.versions

    assert versions.kind == NODE_METADATA_KIND
    assert versions.target is NodeMetadata.drop_target
    assert versions.minimum_bits == MIN_NODE_METADATA_BITS


def test_a_version_another_node_signed_is_passed_over_at_debug(
    reader: NodeMetadataReader,
    truth: CasStore,
    node: NodeIdentity,
    impostor: NodeIdentity,
    caplog: LogCaptureFixture,
) -> None:
    placed(truth, node, node, NodeMetadata(ATTIC, 1).value())
    forged = placed(truth, node, impostor, NodeMetadata({"name": "Forged"}, 9).value())

    with caplog.at_level(DEBUG, logger=SIGNED_VERSIONS_LOGGER):
        held = reader.read(node.node_id)

    assert held.fields == ATTIC
    (record,) = caplog.records
    assert record.levelno == DEBUG
    assert record.getMessage().startswith(f"Passing over {forged} at {node.node_id}'s metadata")


def test_without_the_nodes_key_it_is_named_missing(
    reader: NodeMetadataReader, truth: CasStore, node: NodeIdentity
) -> None:
    placed(truth, node, node, NodeMetadata(ATTIC, 1).value())
    truth.delete(node.node_id)

    held = reader.read(node.node_id)

    assert held == HeldNodeMetadata(node.node_id, missing=(node.node_id,))
