"""Tests for the validator module, fed "PUT completed" messages directly."""

from __future__ import annotations
from logging import WARNING
from pathlib import Path
from queue import Empty, Queue
from zlib import compress

from pytest import LogCaptureFixture, fixture

from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, node_store, source_of_truth_store
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.validator.module import ValidatorModule, validator_module_factory

CONTENT = b"validated content " * 32
CONTENT_ID = ContentId.for_data(CONTENT, "sha256")
NODE_ID = ContentId.for_data(b"a node's public key", "sha256")
OTHER_NODE_ID = ContentId.for_data(b"another node's public key", "sha256")


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def truth(storage: StorageConfig) -> CasStore:
    return source_of_truth_store(storage)


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def validator(storage: StorageConfig, queues: ModuleQueues) -> ValidatorModule:
    return ValidatorModule(ModuleName.VALIDATOR, queues, storage, poll_interval=0.01)


def upload(
    storage: StorageConfig,
    data: bytes,
    content_id: ContentId = CONTENT_ID,
    node_id: ContentId = NODE_ID,
) -> Message:
    """Store ``data`` as the web server would and return its announcement."""
    node_store(storage, node_id).write(content_id, data)
    return completed(content_id, node_id)


def completed(content_id: ContentId = CONTENT_ID, node_id: ContentId = NODE_ID) -> Message:
    return make_message(
        EventType.PUT_COMPLETED,
        ModuleName.WEBSERVER,
        {"algorithm": content_id.algorithm, "hash": content_id.hash, "node_id": str(node_id)},
    )


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def test_valid_upload_is_promoted_and_announced(
    validator: ValidatorModule, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    validator.handle(upload(storage, CONTENT))

    assert truth.read(CONTENT_ID) == CONTENT
    assert not node_store(storage, NODE_ID).exists(CONTENT_ID)

    (message,) = published(queues)
    assert message["event"] == EventType.DATA_STORED
    assert message["source"] == ModuleName.VALIDATOR
    assert message["algorithm"] == "sha256"
    assert message["hash"] == CONTENT_ID.hash
    assert message["node_id"] == str(NODE_ID)
    assert message["size"] == len(CONTENT)


def test_compressed_upload_is_stored_as_received(
    validator: ValidatorModule, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    compressed = compress(CONTENT, 9)

    validator.handle(upload(storage, compressed))

    assert truth.read(CONTENT_ID) == compressed
    assert not node_store(storage, NODE_ID).exists(CONTENT_ID)
    (message,) = published(queues)
    assert message["event"] == EventType.DATA_STORED
    assert message["size"] == len(compressed)


def test_mismatched_upload_is_discarded_and_reported(
    validator: ValidatorModule,
    storage: StorageConfig,
    truth: CasStore,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    for data in (b"not the content", compress(b"not the content")):
        validator.handle(upload(storage, data))

        assert not truth.exists(CONTENT_ID)
        assert not node_store(storage, NODE_ID).exists(CONTENT_ID)
        (message,) = published(queues)
        assert message["event"] == EventType.DATA_REJECTED
        assert message["source"] == ModuleName.VALIDATOR
        assert message["algorithm"] == "sha256"
        assert message["hash"] == CONTENT_ID.hash
        assert message["node_id"] == str(NODE_ID)
        assert "size" not in message

    assert [record.levelno for record in caplog.records].count(WARNING) == 2


def test_upload_of_content_already_held_is_discarded_quietly(
    validator: ValidatorModule, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    compressed = compress(CONTENT)
    truth.write(CONTENT_ID, compressed)

    validator.handle(upload(storage, CONTENT))

    assert truth.read(CONTENT_ID) == compressed
    assert not node_store(storage, NODE_ID).exists(CONTENT_ID)
    assert published(queues) == []


def test_announcement_of_a_missing_upload_is_ignored(
    validator: ValidatorModule, truth: CasStore, queues: ModuleQueues
) -> None:
    validator.handle(completed())

    assert not truth.exists(CONTENT_ID)
    assert published(queues) == []


def test_duplicate_announcements_store_once(
    validator: ValidatorModule, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    message = upload(storage, CONTENT)

    validator.handle(message)
    validator.handle(message)

    assert truth.read(CONTENT_ID) == CONTENT
    assert [message["event"] for message in published(queues)] == [EventType.DATA_STORED]


def test_uploads_from_different_nodes_are_checked_separately(
    validator: ValidatorModule, storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    bad = upload(storage, b"forged", node_id=OTHER_NODE_ID)
    good = upload(storage, CONTENT)

    validator.handle(bad)
    validator.handle(good)

    assert truth.read(CONTENT_ID) == CONTENT
    assert [(message["event"], message["node_id"]) for message in published(queues)] == [
        (EventType.DATA_REJECTED, str(OTHER_NODE_ID)),
        (EventType.DATA_STORED, str(NODE_ID)),
    ]


def test_run_survives_malformed_announcements(
    validator: ValidatorModule,
    storage: StorageConfig,
    truth: CasStore,
    queues: ModuleQueues,
    caplog: LogCaptureFixture,
) -> None:
    for payload in (
        {"hash": CONTENT_ID.hash, "node_id": str(NODE_ID)},
        {"algorithm": "sha256", "hash": "xyz", "node_id": str(NODE_ID)},
        {"algorithm": "sha256", "hash": CONTENT_ID.hash, "node_id": "not a node id"},
    ):
        queues.inbox.put(make_message(EventType.PUT_COMPLETED, ModuleName.WEBSERVER, payload))

    queues.inbox.put(upload(storage, CONTENT))
    queues.inbox.put(make_message(EventType.SHUTDOWN, ModuleName.SUPERVISOR))

    validator.run()

    assert truth.read(CONTENT_ID) == CONTENT
    assert [message["event"] for message in published(queues)] == [EventType.DATA_STORED]
    assert caplog.text.count("failed handling data.put_completed") == 3


def test_validator_subscribes_only_to_completed_uploads() -> None:
    assert ValidatorModule.subscriptions == frozenset({EventType.PUT_COMPLETED})


def test_factory_builds_a_validator_for_the_configured_storage(
    storage: StorageConfig, truth: CasStore, queues: ModuleQueues
) -> None:
    validator = validator_module_factory(
        ModuleName.VALIDATOR, LibranetConfig(storage=storage), queues
    )

    assert isinstance(validator, ValidatorModule)
    assert validator.name == ModuleName.VALIDATOR

    validator.handle(upload(storage, CONTENT))

    assert truth.read(CONTENT_ID) == CONTENT
