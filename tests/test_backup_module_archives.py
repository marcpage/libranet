"""Tests for the backup module keeping content in archives of the node's own, the clock faked."""

from __future__ import annotations
from datetime import UTC, datetime
from json import dumps
from logging import INFO, WARNING
from pathlib import Path
from typing import Any

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.backup.errors import KeptArchivesFileError
from libranet.backup.kept_archives import KeptArchive, KeptRecord
from libranet.backup.module import BackupModule
from libranet.bundle.parts import PartPath, PartWriter
from libranet.bundle.serialization import encode_bundle
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.bundle.storing import store_object
from libranet.cas.archive import ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.layered import LayeredSource
from libranet.cas.store import CasStore
from libranet.config.models import (
    BackupConfig,
    IdentityConfig,
    LibranetConfig,
    LoggingConfig,
    StorageConfig,
)
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import ARCHIVES_FIELD, EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.backup_requests import ArchiveRequest
from libranet.webserver.backup_state import BackupReport

from tests.helpers import published, with_node_key

START = 1_789_000_000.0
EXPIRES = datetime(2026, 11, 20, tzinfo=UTC)
LATER = datetime(2027, 1, 1, tzinfo=UTC)
FILM = (b"the first part of a film", b"the second part of a film")
VERSION = b"a version listing the collection, at a drop"


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return with_node_key(
        LibranetConfig(
            storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
            identity=IdentityConfig(key_dir=tmp_path / "keys"),
            logging=LoggingConfig(directory=tmp_path / "logs"),
            backup=BackupConfig(interval_seconds=100.0),
        )
    )


@fixture
def now() -> list[float]:
    return [START]


@fixture
def store(config: LibranetConfig) -> CasStore:
    return CasStore.source_of_truth(config.storage)


def start(config: LibranetConfig, queues: ModuleQueues, now: list[float]) -> BackupModule:
    module = BackupModule(
        ModuleName.BACKUP, queues, config, clock=lambda: now[0], poll_interval_seconds=0.01
    )
    module.on_start()
    return module


def id_of(data: bytes) -> ContentId:
    return ContentId.for_data(data, "sha256")


def collection(store: CasStore, *, held: bool = True) -> PartPath:
    """An encrypted collection holding one film, its parts stored only if ``held``."""
    if held:
        for data in FILM:
            store_object(data, store)

    film = FileBundle(tuple(str(id_of(data)) for data in FILM))
    return PartWriter(store, encrypted=True).store(
        encode_bundle(DirectoryBundle({"film/film.mp4": film}))
    )


def keep(
    module: BackupModule, bundle: PartPath, *objects: ContentId, expires: datetime | None = EXPIRES
) -> str:
    request = ArchiveRequest((bundle,), objects, expires)
    module.handle(
        make_message(EventType.ARCHIVE_REQUESTED, ModuleName.WEBSERVER, request.payload())
    )
    return request.archive_id


def remove(module: BackupModule, archive_id: str) -> None:
    module.handle(
        make_message(EventType.ARCHIVE_REMOVED, ModuleName.WEBSERVER, {"archive_id": archive_id})
    )


def archives(messages: list[Message]) -> list[list[dict[str, Any]]]:
    """The archives each ``backup.state`` reported, in order."""
    return [
        message[ARCHIVES_FIELD]
        for message in messages
        if message["event"] == EventType.BACKUP_STATE
    ]


def asked_for(messages: list[Message]) -> list[ContentId]:
    return [
        ContentId.create(message["algorithm"], message["hash"])
        for message in messages
        if message["event"] == EventType.DATA_NOT_FOUND
    ]


def fetched(content_id: ContentId) -> Message:
    """The ``data.stored`` the validator publishes once it has stored what a peer sent."""
    return make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {**content_id.fields(), "node_id": f"sha256/{'0' * 64}", "size": 10},
    )


def archive_path(config: LibranetConfig, archive_id: str) -> Path:
    return config.storage.kept_archives_dir / f"{archive_id}.zip"


def test_the_module_hears_archives_asked_for_and_removed() -> None:
    assert EventType.ARCHIVE_REQUESTED in BackupModule.subscriptions
    assert EventType.ARCHIVE_REMOVED in BackupModule.subscriptions


def test_an_archive_of_content_held_is_written_recorded_and_read_once_the_store_lets_go(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    bundle = collection(store)
    version = store_object(VERSION, store)
    published(queues)

    archive_id = keep(module, bundle, version)

    messages = published(queues)
    states = archives(messages)
    assert [state[0]["status"] for state in states] == ["waiting", "running", "done"]
    assert states[-1][0]["bundles"] == [str(bundle.content_id)]
    assert states[-1][0]["expires"] == "2026-11-20T00:00:00Z"
    assert bundle.key is not None and bundle.key.hex() not in dumps(messages[-1])
    assert BackupReport.from_message(messages[-1]).archives == tuple(states[-1])
    assert archive_id not in module.archiving
    assert module.kept[archive_id].bundles == (bundle.content_id,)
    assert KeptRecord.load_all(config.storage.kept_archives_path) == module.kept
    assert bundle.key.hex() not in config.storage.kept_archives_path.read_text()

    with ArchiveSource.open(archive_path(config, archive_id)) as opened:
        assert set(opened.iter_prefix("sha256", "")) == {
            bundle.content_id,
            version,
            *(id_of(data) for data in FILM),
        }

    with LayeredSource.open(config.storage) as content:
        store.delete(id_of(FILM[0]))
        assert content.read(id_of(FILM[0])) == FILM[0]


def test_content_lacked_is_asked_for_and_the_archive_written_once_it_has_all_arrived(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    bundle = collection(store, held=False)
    published(queues)

    archive_id = keep(module, bundle)

    messages = published(queues)
    assert set(asked_for(messages)) == {id_of(data) for data in FILM}
    assert archives(messages)[-1][0]["status"] == "waiting"
    assert archives(messages)[-1][0]["missing"] == 2

    for data in FILM:
        module.handle(fetched(store_object(data, store)))

    assert archives(published(queues))[-1][0]["status"] == "done"
    assert archive_path(config, archive_id).is_file()


def test_asking_for_one_waiting_again_carries_it_on(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    bundle = collection(store, held=False)
    archive_id = keep(module, bundle)
    published(queues)
    now[0] += 1

    keep(module, bundle, expires=LATER)

    messages = published(queues)
    assert set(asked_for(messages)) == {id_of(data) for data in FILM}
    assert module.archiving[archive_id].request.expires == LATER


def test_asking_for_one_kept_again_keeps_it_longer_without_writing_it_again(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    bundle = collection(store)
    archive_id = keep(module, bundle)
    written = archive_path(config, archive_id).stat().st_mtime_ns
    published(queues)

    keep(module, bundle, expires=None)

    assert archives(published(queues))[-1][0]["expires"] is None
    assert module.kept[archive_id].expires is None
    assert KeptRecord.load_all(config.storage.kept_archives_path)[archive_id].expires is None
    assert archive_path(config, archive_id).stat().st_mtime_ns == written


def test_one_removed_is_deleted_and_forgotten(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    archive_id = keep(module, collection(store))
    published(queues)

    remove(module, archive_id)

    assert archives(published(queues))[-1] == []
    assert not archive_path(config, archive_id).exists()
    assert KeptRecord.load_all(config.storage.kept_archives_path) == {}


def test_one_removed_while_it_waits_is_forgotten(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    archive_id = keep(module, collection(store, held=False))

    remove(module, archive_id)

    assert module.archiving == {}
    assert archives(published(queues))[-1] == []


def test_removing_one_not_kept_says_so_and_reports_nothing(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], caplog: LogCaptureFixture
) -> None:
    module = start(config, queues, now)
    published(queues)

    with caplog.at_level(INFO, logger=module.logger.name):
        remove(module, "0123456789abcdef")

    assert archives(published(queues)) == []
    assert "no kept archive 0123456789abcdef" in caplog.text


def test_one_is_removed_once_it_expires(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    archive_id = keep(module, collection(store))
    now[0] = EXPIRES.timestamp() - 1
    module.on_idle()
    assert archive_path(config, archive_id).is_file()
    published(queues)

    now[0] = EXPIRES.timestamp()
    module.on_idle()

    assert archives(published(queues))[-1] == []
    assert not archive_path(config, archive_id).exists()
    assert module.kept == {}


def test_one_still_waiting_when_it_expires_is_forgotten(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    keep(module, collection(store, held=False))
    now[0] = EXPIRES.timestamp()

    module.on_idle()

    assert module.archiving == {}


def test_one_that_expired_while_the_node_was_down_is_removed_once_it_starts(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    archive_id = keep(start(config, queues, now), collection(store))
    now[0] = EXPIRES.timestamp() + 1

    restarted = start(config, queues, now)
    assert archive_id in restarted.kept
    restarted.on_idle()

    assert restarted.kept == {}
    assert not archive_path(config, archive_id).exists()


def test_records_outlive_a_restart_and_one_no_longer_there_is_forgotten(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    store: CasStore,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    kept_id = keep(module, collection(store))
    gone_id = keep(module, collection(store), store_object(VERSION, store))
    archive_path(config, gone_id).unlink()
    published(queues)

    with caplog.at_level(WARNING):
        restarted = start(config, queues, now)

    assert set(restarted.kept) == {kept_id}
    assert set(KeptRecord.load_all(config.storage.kept_archives_path)) == {kept_id}
    assert gone_id in caplog.text
    assert [state["archive_id"] for state in archives(published(queues))[-1]] == [kept_id]


def test_records_that_cannot_be_read_stop_the_module(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    config.storage.kept_archives_path.write_text("not JSON")

    with raises(KeptArchivesFileError):
        start(config, queues, now)


def test_an_archive_id_that_does_not_name_its_content_is_refused(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    payload = ArchiveRequest((collection(store),)).payload()

    with raises(ValueError, match="does not name"):
        module.handle(
            make_message(
                EventType.ARCHIVE_REQUESTED,
                ModuleName.WEBSERVER,
                {**payload, "archive_id": "0123456789abcdef"},
            )
        )


def test_an_archive_that_cannot_be_written_fails_and_says_why(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    store: CasStore,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    config.storage.kept_archives_dir.parent.mkdir(parents=True, exist_ok=True)
    config.storage.kept_archives_dir.write_text("not a folder")
    published(queues)

    with caplog.at_level(WARNING, logger=module.logger.name):
        archive_id = keep(module, collection(store))

    final = archives(published(queues))[-1][0]
    assert final["status"] == "failed"
    assert final["error"]
    assert archive_id in caplog.text


def test_an_archive_that_cannot_be_recorded_is_removed_and_fails(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    store: CasStore,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    config.storage.kept_archives_path.mkdir(parents=True)

    with caplog.at_level(WARNING, logger=module.logger.name):
        archive_id = keep(module, collection(store))

    assert module.archiving[archive_id].status.value == "failed"
    assert not archive_path(config, archive_id).exists()
    assert "Could not record" in caplog.text


def test_an_unexpected_failure_fails_it_and_is_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    store: CasStore,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)

    def broken(*_: object) -> tuple[ContentId, ...]:
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(KeptArchive, "attempt", broken)

    with caplog.at_level(WARNING, logger=module.logger.name):
        archive_id = keep(module, collection(store))

    assert module.archiving[archive_id].error == "something unexpected"
    assert "failed" in caplog.text


def test_the_archives_are_reported_in_the_order_asked_for(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], store: CasStore
) -> None:
    module = start(config, queues, now)
    first = keep(module, collection(store))
    now[0] += 1
    second = keep(module, collection(store, held=False), store_object(VERSION, store))

    assert [state["archive_id"] for state in archives(published(queues))[-1]] == [first, second]
