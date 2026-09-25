"""Tests for the backup module, with the test standing in for the web server's ``/config``
endpoints, and the clock faked."""

from __future__ import annotations
from io import BytesIO
from logging import ERROR, INFO, WARNING
from os import mkfifo
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.backup.builds import Build, BuildRecord
from libranet.backup.exports import Export
from libranet.backup.jobs import JobFileError, load_jobs
from libranet.backup.module import BackupModule, backup_module_factory
from libranet.backup.restores import Restore
from libranet.bundle.building import build_directory
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.bundle.storing import store_bundle
from libranet.cas.archive import ArchiveSink, ArchiveSource
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import (
    BackupConfig,
    IdentityConfig,
    LibranetConfig,
    LoggingConfig,
    StorageConfig,
)
from libranet.eviction.priority import held_objects
from libranet.identity.keys import load_or_create_backup_secret
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.webserver.backup_state import BackupReport
from libranet.webserver.config_requests import (
    BackupJobRequest,
    BuildRequest,
    ConflictBehavior,
    ExportRequest,
    Password,
    RestoreRequest,
)

INTERVAL = 100.0
START = 1_789_000_000.0


class Blind:
    """A change detector that never notices a change."""

    def fingerprint(self, directory: Path) -> str:
        return "the same as ever"


class Broken:
    """A change detector that fails without saying why."""

    def fingerprint(self, directory: Path) -> str:
        raise RuntimeError()


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return LibranetConfig(
        storage=StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache"),
        identity=IdentityConfig(key_dir=tmp_path / "keys"),
        logging=LoggingConfig(directory=tmp_path / "logs"),
        backup=BackupConfig(interval_seconds=INTERVAL),
    )


@fixture
def queues() -> ModuleQueues:
    return ModuleQueues(inbox=Queue(), outbox=Queue())


@fixture
def now() -> list[float]:
    return [START]


@fixture
def store(config: LibranetConfig) -> CasStore:
    return source_of_truth_store(config.storage)


@fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "docs").mkdir(parents=True)
    (root / "readme.txt").write_bytes(b"read me")
    (root / "docs" / "notes.txt").write_bytes(b"some notes")
    return root


def start(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], **options: Any
) -> BackupModule:
    module = BackupModule(
        ModuleName.BACKUP, queues, config, clock=lambda: now[0], poll_interval=0.01, **options
    )
    module.on_start()
    return module


def published(queues: ModuleQueues) -> list[Message]:
    messages = []

    while True:
        try:
            messages.append(queues.outbox.get(block=False))

        except Empty:
            return messages


def of(messages: list[Message], event: EventType) -> list[Message]:
    return [message for message in messages if message["event"] == event]


def reports(messages: list[Message]) -> list[list[dict[str, Any]]]:
    """The jobs each ``backup.state`` reported, in order."""
    return [message["jobs"] for message in of(messages, EventType.BACKUP_STATE)]


def stored_ids(messages: list[Message]) -> list[ContentId]:
    return [
        ContentId.create(message["algorithm"], message["hash"])
        for message in of(messages, EventType.DATA_STORED)
    ]


def asked(event: EventType, **payload: Any) -> Message:
    return make_message(event, ModuleName.WEBSERVER, payload)


def configure(module: BackupModule, directory: Path, interval: float | None = None) -> str:
    job = BackupJobRequest(str(directory), interval)
    module.handle(asked(EventType.BACKUP_JOB_CONFIGURED, **job.payload()))
    return job.job_id


def job_id_of(directory: Path) -> str:
    return BackupJobRequest(str(directory)).job_id


def restores(messages: list[Message]) -> list[list[dict[str, Any]]]:
    """The restores each ``backup.state`` reported, in order."""
    return [message["restores"] for message in of(messages, EventType.BACKUP_STATE)]


def asked_for(messages: list[Message]) -> list[ContentId]:
    return [
        ContentId.create(message["algorithm"], message["hash"])
        for message in of(messages, EventType.DATA_NOT_FOUND)
    ]


def restore(
    module: BackupModule,
    bundle: str,
    directory: Path,
    on_conflict: ConflictBehavior = ConflictBehavior.REFUSE,
) -> str:
    request = RestoreRequest(ContentId.parse(bundle), str(directory), on_conflict)
    module.handle(asked(EventType.RESTORE_REQUESTED, **request.payload()))
    return request.restore_id


def fetched(content_id: ContentId, size: int) -> Message:
    """The ``data.stored`` the validator publishes once it has stored what a peer sent."""
    return make_message(
        EventType.DATA_STORED,
        ModuleName.VALIDATOR,
        {
            "algorithm": content_id.algorithm,
            "hash": content_id.hash,
            "node_id": f"sha256/{'0' * 64}",
            "size": size,
        },
    )


def backed_up_bundle(module: BackupModule, queues: ModuleQueues, tree: Path) -> str:
    configure(module, tree)
    bundle: str = reports(published(queues))[-1][0]["bundle"]
    return bundle


def secret_of(config: LibranetConfig) -> bytes:
    identity = config.identity
    key_dir = identity.resolved_key_dir(config.storage)
    return load_or_create_backup_secret(key_dir / identity.backup_secret_path_name)


def restored(bundle: ContentId, store: CasStore, secret: bytes) -> dict[str, bytes]:
    """Every file ``bundle`` holds, by path."""
    top = load_bundle(bundle, store, password=secret)
    assert isinstance(top, DirectoryBundle)
    files: dict[str, bytes] = {}

    for path, entry in resolve_directory(
        top, lambda content_id: load_bundle(content_id, store, password=secret)
    ).items():
        assert isinstance(entry, FileBundle)
        output = BytesIO()
        write_file(entry, store, output)
        files[path] = output.getvalue()

    return files


def test_the_module_reports_its_jobs_once_started(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    start(config, queues, now)
    messages = published(queues)

    assert [message["event"] for message in messages] == [EventType.BACKUP_STATE]
    assert BackupReport.from_message(messages[0]) == BackupReport((), ())


def test_a_configured_job_is_backed_up_at_once(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    published(queues)
    job_id = configure(module, tree)
    messages = published(queues)
    states = reports(messages)
    final = states[-1][0]

    assert [jobs[0]["status"] for jobs in states] == ["waiting", "running", "waiting"]
    assert final == {
        "job_id": job_id,
        "directory": str(tree),
        "interval_seconds": INTERVAL,
        "status": "waiting",
        "error": None,
        "checked_at": START,
        "bundle": final["bundle"],
        "backed_up_at": START,
        "skipped": 0,
    }
    assert restored(ContentId.parse(final["bundle"]), store, secret_of(config)) == {
        "readme.txt": b"read me",
        "docs/notes.txt": b"some notes",
    }

    for message in of(messages, EventType.BACKUP_STATE):
        BackupReport.from_message(message)


def test_every_object_a_backup_stores_is_announced_from_this_node(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    node_id = load_node_identity(config).node_id
    configure(module, tree)
    announced = of(published(queues), EventType.DATA_STORED)
    held = set(store.iter_prefix("sha256", "")) - {node_id}

    assert sorted(stored_ids(announced)) == sorted(held)

    for message in announced:
        content_id = ContentId.create(message["algorithm"], message["hash"])
        assert message["node_id"] == str(node_id)
        assert message["size"] == store.path_for(content_id).stat().st_size


def test_a_second_backup_writes_only_what_changed_and_supersedes_the_first(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    job_id = configure(module, tree)
    first = reports(published(queues))[-1][0]["bundle"]
    (tree / "readme.txt").write_bytes(b"read me, changed")
    now[0] += INTERVAL
    module.on_idle()
    messages = published(queues)
    second = ContentId.parse(reports(messages)[-1][0]["bundle"])
    top = load_bundle(second, store, password=secret_of(config))

    assert sorted(stored_ids(messages)) == sorted(
        [ContentId.for_data(b"read me, changed", "sha256"), second]
    )
    assert isinstance(top, DirectoryBundle)
    assert top.versions == (first,)
    assert reports(messages)[-1][0]["backed_up_at"] == START + INTERVAL
    latest = module.jobs[job_id].latest
    assert latest is not None and latest.bundle == second


def test_an_unchanged_directory_is_looked_at_but_not_backed_up(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    configure(module, tree)
    bundle = reports(published(queues))[-1][0]["bundle"]
    now[0] += INTERVAL
    module.on_idle()
    messages = published(queues)

    assert stored_ids(messages) == []
    assert [jobs[0]["status"] for jobs in reports(messages)] == ["waiting"]
    assert reports(messages)[0][0]["checked_at"] == START + INTERVAL
    assert reports(messages)[0][0]["bundle"] == bundle
    assert reports(messages)[0][0]["backed_up_at"] == START


def test_a_job_is_not_looked_at_before_its_interval_is_up(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    configure(module, tree)
    published(queues)
    now[0] += INTERVAL - 1
    module.on_idle()

    assert published(queues) == []


def test_a_job_interval_overrides_the_configured_one(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    configure(module, tree, 10.0)
    published(queues)
    now[0] += 10.0
    module.on_idle()

    assert reports(published(queues))[-1][0]["interval_seconds"] == 10.0


def test_a_requested_backup_runs_though_no_change_is_noticed(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now, detector=Blind())
    job_id = configure(module, tree)
    published(queues)
    (tree / "readme.txt").write_bytes(b"read me, changed")
    now[0] += INTERVAL
    module.on_idle()

    assert stored_ids(published(queues)) == []

    module.handle(asked(EventType.BACKUP_RUN_REQUESTED, job_id=job_id))

    assert ContentId.for_data(b"read me, changed", "sha256") in stored_ids(published(queues))


def test_a_requested_backup_of_an_unchanged_directory_keeps_its_bundle(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    job_id = configure(module, tree)
    bundle = reports(published(queues))[-1][0]["bundle"]
    module.handle(asked(EventType.BACKUP_RUN_REQUESTED, job_id=job_id))
    messages = published(queues)

    assert [jobs[0]["status"] for jobs in reports(messages)] == ["running", "waiting"]
    assert reports(messages)[-1][0]["bundle"] == bundle
    assert stored_ids(messages) == []


def test_a_requested_backup_goes_ahead_of_a_job_longer_due(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tmp_path: Path
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    module = start(config, queues, now)
    configure(module, first)
    configure(module, second)
    now[0] += 2 * INTERVAL
    published(queues)
    module.handle(asked(EventType.BACKUP_RUN_REQUESTED, job_id=job_id_of(second)))
    checked = {job["directory"]: job["checked_at"] for job in reports(published(queues))[-1]}

    assert checked == {str(first): START, str(second): now[0]}

    module.on_idle()
    checked = {job["directory"]: job["checked_at"] for job in reports(published(queues))[-1]}

    assert checked == {str(first): now[0], str(second): now[0]}


def test_configuring_a_job_again_keeps_its_backup_and_sets_its_interval(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    job_id = configure(module, tree)
    bundle = reports(published(queues))[-1][0]["bundle"]
    configure(module, tree, 5.0)
    job = reports(published(queues))[-1][0]

    assert (job["bundle"], job["interval_seconds"]) == (bundle, 5.0)
    assert load_jobs(config.storage.backup_jobs_path)[job_id].request.interval_seconds == 5.0


def test_removing_a_job_forgets_it_but_leaves_its_content(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    job_id = configure(module, tree)
    bundle = ContentId.parse(reports(published(queues))[-1][0]["bundle"])
    module.handle(asked(EventType.BACKUP_JOB_REMOVED, job_id=job_id))

    assert reports(published(queues)) == [[]]
    assert load_jobs(config.storage.backup_jobs_path) == {}
    assert store.exists(bundle)

    now[0] += INTERVAL
    module.on_idle()

    assert published(queues) == []


def test_jobs_this_node_does_not_have_are_neither_removed_nor_run(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    module = start(config, queues, now)
    published(queues)
    module.handle(asked(EventType.BACKUP_JOB_REMOVED, job_id="0" * 16))
    module.handle(asked(EventType.BACKUP_RUN_REQUESTED, job_id="0" * 16))

    assert published(queues) == []


def test_jobs_and_their_backups_outlive_the_module(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    configure(start(config, queues, now), tree)
    before = reports(published(queues))[-1]
    now[0] += 1
    restarted = start(config, queues, now)

    assert reports(published(queues)) == [[{**before[0], "checked_at": None}]]

    restarted.on_idle()
    messages = published(queues)

    assert stored_ids(messages) == []
    assert reports(messages)[-1][0]["bundle"] == before[0]["bundle"]


def test_a_missing_directory_fails_its_job_until_it_appears(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tmp_path: Path
) -> None:
    later = tmp_path / "later"
    module = start(config, queues, now)
    configure(module, later)
    job = reports(published(queues))[-1][0]

    assert job["status"] == "failed"
    assert "No such file or directory" in job["error"]
    assert job["bundle"] is None

    later.mkdir()
    (later / "file.txt").write_bytes(b"at last")
    now[0] += INTERVAL
    module.on_idle()
    job = reports(published(queues))[-1][0]

    assert (job["status"], job["error"]) == ("waiting", None)
    assert job["bundle"] is not None


def test_the_node_own_directories_are_left_out_as_though_absent(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tmp_path: Path,
    tree: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    configure(module, tmp_path)
    job = reports(published(queues))[-1][0]

    assert (job["status"], job["skipped"]) == ("waiting", 0)
    assert restored(ContentId.parse(job["bundle"]), store, secret_of(config)) == {
        "tree/readme.txt": b"read me",
        "tree/docs/notes.txt": b"some notes",
    }


def test_what_a_backup_stores_does_not_set_off_another(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tmp_path: Path, tree: Path
) -> None:
    module = start(config, queues, now)
    configure(module, tmp_path)
    bundle = reports(published(queues))[-1][0]["bundle"]
    now[0] += INTERVAL
    module.on_idle()
    messages = published(queues)

    assert stored_ids(messages) == []
    assert [jobs[0]["status"] for jobs in reports(messages)] == ["waiting"]
    assert reports(messages)[-1][0]["bundle"] == bundle


def test_a_job_within_the_node_own_directory_fails_as_though_absent(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    module = start(config, queues, now)
    configure(module, config.storage.source_of_truth_dir)
    messages = published(queues)
    job = reports(messages)[-1][0]

    assert job["status"] == "failed"
    assert "Ignored" in job["error"]
    assert stored_ids(messages) == []


def test_a_job_id_that_does_not_name_its_directory_is_refused(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    published(queues)

    with raises(ValueError):
        module.handle(
            asked(
                EventType.BACKUP_JOB_CONFIGURED,
                job_id="0" * 16,
                directory=str(tree),
                interval_seconds=None,
            )
        )

    assert module.jobs == {}
    assert published(queues) == []


def test_an_unreadable_jobs_file_stops_the_module(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    config.storage.backup_jobs_path.parent.mkdir(parents=True, exist_ok=True)
    config.storage.backup_jobs_path.write_bytes(b"not json")

    with raises(JobFileError):
        start(config, queues, now)


def test_jobs_that_cannot_be_saved_are_not_kept(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    published(queues)
    config.storage.backup_jobs_path.mkdir(parents=True)

    with raises(OSError):
        configure(module, tree)

    assert module.jobs == {}
    assert published(queues) == []


def test_a_backup_that_cannot_be_saved_fails_its_job_and_keeps_the_last(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    job_id = configure(module, tree)
    bundle = reports(published(queues))[-1][0]["bundle"]
    config.storage.backup_jobs_path.unlink()
    config.storage.backup_jobs_path.mkdir()
    (tree / "readme.txt").write_bytes(b"read me, changed")
    now[0] += INTERVAL
    module.on_idle()
    job = reports(published(queues))[-1][0]

    latest = module.jobs[job_id].latest

    assert job["status"] == "failed"
    assert job["bundle"] == bundle
    assert latest is not None and str(latest.bundle) == bundle


def test_an_unusable_backup_secret_fails_the_backup(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    identity = config.identity
    secret = identity.resolved_key_dir(config.storage) / identity.backup_secret_path_name
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_bytes(b"short")
    module = start(config, queues, now)
    configure(module, tree)
    job = reports(published(queues))[-1][0]

    assert job["status"] == "failed"
    assert "Backup secret" in job["error"]


def test_an_unexpected_failure_fails_the_job_and_is_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now, detector=Broken())

    with caplog.at_level(ERROR):
        configure(module, tree)

    job = reports(published(queues))[-1][0]

    assert (job["status"], job["error"]) == ("failed", "RuntimeError")
    assert any(record.exc_info for record in caplog.records)


def test_paths_left_out_are_counted_and_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    caplog: LogCaptureFixture,
) -> None:
    mkfifo(tree / "pipe")
    module = start(config, queues, now)

    with caplog.at_level(WARNING):
        configure(module, tree)

    assert reports(published(queues))[-1][0]["skipped"] == 1
    assert "Left pipe out of the backup" in caplog.text


def test_the_node_id_is_only_known_once_started(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    with raises(RuntimeError):
        BackupModule(ModuleName.BACKUP, queues, config).node_id


def test_the_supervisor_runs_the_backup_module(
    config: LibranetConfig, queues: ModuleQueues
) -> None:
    factories = {spec.name: spec.factory for spec in default_module_specs()}

    assert factories[ModuleName.BACKUP] is backup_module_factory
    assert isinstance(backup_module_factory(ModuleName.BACKUP, config, queues), BackupModule)


def test_a_restore_rebuilds_a_backed_up_directory_and_is_reported(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, tmp_path: Path
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    target = tmp_path / "restored"
    restore_id = restore(module, bundle, target)
    messages = published(queues)
    states = restores(messages)

    assert [state[0]["status"] for state in states] == ["running", "done"]
    assert states[-1] == [
        {
            "restore_id": restore_id,
            "bundle": bundle,
            "directory": str(target),
            "on_conflict": "refuse",
            "status": "done",
            "error": None,
            "requested_at": START,
            "finished_at": START,
            "restored": 2,
            "skipped": 0,
            "missing": 0,
        }
    ]
    assert (target / "readme.txt").read_bytes() == b"read me"
    assert (target / "docs" / "notes.txt").read_bytes() == b"some notes"
    assert asked_for(messages) == []

    for message in of(messages, EventType.BACKUP_STATE):
        BackupReport.from_message(message)


def test_a_restore_reads_content_held_only_in_an_archive(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    bundle = backed_up_bundle(start(config, queues, now), queues, tree)
    archive = tmp_path / "backup.zip"

    with ArchiveSink.create(archive) as sink:
        for content_id in [stored.content_id for stored in held_objects(store)]:
            sink.write(content_id, store.read(content_id))
            store.delete(content_id)

    storage = config.storage.model_copy(update={"archives": (archive,)})
    module = start(config.model_copy(update={"storage": storage}), queues, now)
    published(queues)
    target = tmp_path / "restored"
    restore(module, bundle, target)
    messages = published(queues)

    assert restores(messages)[-1][0]["status"] == "done"
    assert asked_for(messages) == []
    assert (target / "readme.txt").read_bytes() == b"read me"
    assert (target / "docs" / "notes.txt").read_bytes() == b"some notes"


def test_a_restore_asks_for_content_not_held_and_carries_on_once_it_lands(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    part = ContentId.for_data(b"some notes", "sha256")
    data = store.read(part)
    store.delete(part)
    target = tmp_path / "restored"
    restore(module, bundle, target)
    messages = published(queues)
    waiting = restores(messages)[-1][0]

    assert asked_for(messages) == [part]
    assert (waiting["status"], waiting["restored"], waiting["missing"]) == ("waiting", 1, 1)
    assert (target / "readme.txt").read_bytes() == b"read me"

    store.write(part, data)
    module.handle(fetched(part, len(data)))
    states = restores(published(queues))

    assert [(state[0]["status"], state[0]["missing"]) for state in states] == [
        ("waiting", 0),
        ("running", 0),
        ("done", 0),
    ]
    assert (target / "docs" / "notes.txt").read_bytes() == b"some notes"


def test_a_waiting_restore_asks_again_twice_in_the_life_of_a_seek_entry(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    part = ContentId.for_data(b"some notes", "sha256")
    store.delete(part)
    restore(module, bundle, tmp_path / "restored")
    published(queues)
    ask_interval = config.stats.seek_entry_ttl_seconds / 2
    now[0] += ask_interval - 1
    module.on_idle()

    assert asked_for(published(queues)) == []

    now[0] += 1
    module.on_idle()

    assert asked_for(published(queues)) == [part]


def test_asking_for_a_waiting_restore_again_carries_it_on_at_once(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    part = ContentId.for_data(b"some notes", "sha256")
    store.delete(part)
    restore_id = restore(module, bundle, tmp_path / "restored")
    published(queues)
    now[0] += 1
    restore(module, bundle, tmp_path / "restored")
    messages = published(queues)

    assert asked_for(messages) == [part]
    assert restores(messages)[-1][0]["requested_at"] == START
    assert list(module.restores) == [restore_id]


def test_asking_for_a_finished_restore_starts_it_again(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, tmp_path: Path
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    target = tmp_path / "restored"
    restore(module, bundle, target)
    (target / "readme.txt").write_bytes(b"changed since")
    now[0] += 1
    restore(module, bundle, target)
    refused = restores(published(queues))[-1][0]

    assert (refused["status"], refused["requested_at"]) == ("failed", START + 1)
    assert "Not empty" in refused["error"]

    restore(module, bundle, target, ConflictBehavior.OVERWRITE)
    overwritten = restores(published(queues))[-1][0]

    assert (overwritten["status"], overwritten["on_conflict"]) == ("done", "overwrite")
    assert (target / "readme.txt").read_bytes() == b"read me"


def test_a_restore_due_goes_ahead_of_a_backup_due(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, tmp_path: Path
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    now[0] += INTERVAL
    restore(module, bundle, tmp_path / "restored")
    messages = published(queues)

    assert restores(messages)[-1][0]["status"] == "done"
    assert reports(messages)[-1][0]["checked_at"] == START

    module.on_idle()

    assert reports(published(queues))[-1][0]["checked_at"] == START + INTERVAL


def test_content_stored_that_no_restore_waits_on_changes_nothing(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    module = start(config, queues, now)
    published(queues)
    module.handle(fetched(ContentId.for_data(b"other", "sha256"), 5))

    assert published(queues) == []


def test_a_restore_id_that_does_not_name_its_request_is_refused(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tmp_path: Path
) -> None:
    module = start(config, queues, now)
    published(queues)

    with raises(ValueError):
        module.handle(
            asked(
                EventType.RESTORE_REQUESTED,
                restore_id="0" * 16,
                bundle=f"sha256/{'0' * 64}",
                directory=str(tmp_path / "restored"),
                on_conflict="refuse",
            )
        )

    assert module.restores == {}
    assert published(queues) == []


def test_a_bundle_the_backup_secret_does_not_open_fails_its_restore(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tmp_path: Path,
    store: CasStore,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    bundle = store_bundle(DirectoryBundle({}), store, b"another node's secret")

    with caplog.at_level(WARNING):
        restore(module, str(bundle), tmp_path / "restored")

    failed = restores(published(queues))[-1][0]

    assert (failed["status"], failed["finished_at"]) == ("failed", START)
    assert "password" in failed["error"]
    assert "Could not restore" in caplog.text


def test_a_restore_never_writes_in_the_node_own_directories(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    target = config.storage.source_of_truth_dir / "restored"
    restore(module, bundle, target)
    failed = restores(published(queues))[-1][0]

    assert failed["status"] == "failed"
    assert "Ignored" in failed["error"]
    assert not target.exists()


def test_paths_a_restore_leaves_out_are_counted_and_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    (tree / "up").symlink_to("..")
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)

    with caplog.at_level(WARNING):
        restore(module, bundle, tmp_path / "restored")

    done = restores(published(queues))[-1][0]

    assert (done["status"], done["restored"], done["skipped"]) == ("done", 2, 1)
    assert "Left up out of" in caplog.text
    assert not (tmp_path / "restored" / "up").exists()


def test_an_unexpected_restore_failure_fails_it_and_is_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    def broken(*arguments: object) -> None:
        raise RuntimeError()

    monkeypatch.setattr(Restore, "attempt", broken)
    module = start(config, queues, now)

    with caplog.at_level(ERROR):
        restore(module, f"sha256/{'0' * 64}", tmp_path / "restored")

    failed = restores(published(queues))[-1][0]

    assert (failed["status"], failed["error"]) == ("failed", "RuntimeError")
    assert any(record.exc_info for record in caplog.records)


def builds(messages: list[Message]) -> list[list[dict[str, Any]]]:
    """The builds each ``backup.state`` reported, in order."""
    return [message["builds"] for message in of(messages, EventType.BACKUP_STATE)]


def exports(messages: list[Message]) -> list[list[dict[str, Any]]]:
    """The exports each ``backup.state`` reported, in order."""
    return [message["exports"] for message in of(messages, EventType.BACKUP_STATE)]


def build(module: BackupModule, directory: Path, password: str | None = None) -> str:
    request = BuildRequest(str(directory), Password.optional(password))
    module.handle(asked(EventType.BUILD_REQUESTED, **request.payload()))
    return request.build_id


def export(
    module: BackupModule,
    bundle: str,
    archive: Path,
    on_conflict: ConflictBehavior = ConflictBehavior.REFUSE,
    password: str | None = None,
) -> str:
    request = ExportRequest(
        ContentId.parse(bundle), str(archive), on_conflict, Password.optional(password)
    )
    module.handle(asked(EventType.EXPORT_REQUESTED, **request.payload()))
    return request.export_id


def built_bundle(module: BackupModule, queues: ModuleQueues, tree: Path) -> str:
    build(module, tree)
    bundle: str = builds(published(queues))[-1][-1]["bundle"]
    return bundle


def test_the_module_hears_build_and_export_requests() -> None:
    assert {EventType.BUILD_REQUESTED, EventType.EXPORT_REQUESTED} <= BackupModule.subscriptions


def test_a_build_is_made_at_once_recorded_beside_its_directory_and_reported(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    published(queues)
    build_id = build(module, tree)
    messages = published(queues)
    states = builds(messages)
    final = states[-1][0]

    assert [state[0]["status"] for state in states] == ["waiting", "running", "done"]
    assert final == {
        "build_id": build_id,
        "directory": str(tree),
        "protected": False,
        "status": "done",
        "error": None,
        "requested_at": START,
        "finished_at": START,
        "bundle": final["bundle"],
        "previous": None,
        "skipped": 0,
    }
    assert BuildRecord.load(BuildRecord.beside(tree)) == BuildRecord(
        ContentId.parse(final["bundle"])
    )
    # Plain, so it can be served once registered.
    top = load_bundle(ContentId.parse(final["bundle"]), store)
    assert isinstance(top, DirectoryBundle)
    assert set(top.entries) == {"readme.txt", "docs/notes.txt"}

    for message in of(messages, EventType.BACKUP_STATE):
        BackupReport.from_message(message)


def test_every_object_a_build_stores_is_announced_from_this_node(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    node_id = load_node_identity(config).node_id
    build(module, tree)
    announced = of(published(queues), EventType.DATA_STORED)

    assert sorted(stored_ids(announced)) == sorted(set(store.iter_prefix("sha256", "")) - {node_id})
    assert all(message["node_id"] == str(node_id) for message in announced)


def test_building_again_starts_over_and_reports_the_bundle_superseded(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, store: CasStore
) -> None:
    module = start(config, queues, now)
    first = built_bundle(module, queues, tree)
    (tree / "readme.txt").write_bytes(b"read me again")
    now[0] += 1
    build_id = build(module, tree)
    final = builds(published(queues))[-1]

    assert [build["build_id"] for build in final] == [build_id]
    assert (final[0]["requested_at"], final[0]["previous"]) == (START + 1, first)
    assert final[0]["bundle"] != first
    top = load_bundle(ContentId.parse(final[0]["bundle"]), store)
    assert isinstance(top, DirectoryBundle)
    assert top.versions == (first,)


def test_building_an_unchanged_directory_again_keeps_its_bundle(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    first = built_bundle(module, queues, tree)

    with caplog.at_level(INFO):
        build(module, tree)

    final = builds(published(queues))[-1][0]

    assert (final["bundle"], final["previous"]) == (first, first)
    assert "unchanged since it was built" in caplog.text


def test_a_build_finishes_when_it_is_done_rather_than_when_it_began(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    def slow(*arguments: Any, **options: Any) -> Any:
        now[0] += 5
        return build_directory(*arguments, **options)

    monkeypatch.setattr("libranet.backup.builds.build_directory", slow)
    module = start(config, queues, now)
    build(module, tree)
    done = builds(published(queues))[-1][0]

    assert (done["requested_at"], done["finished_at"]) == (START, START + 5)


def test_a_build_with_a_password_never_reports_or_logs_it(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    store: CasStore,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)

    with caplog.at_level(0):
        build(module, tree, "correct horse")

    messages = published(queues)
    final = builds(messages)[-1][0]

    assert (final["status"], final["protected"]) == ("done", True)
    assert "correct horse" not in repr(messages)
    assert "correct horse" not in caplog.text
    top = load_bundle(ContentId.parse(final["bundle"]), store, password=b"correct horse")
    assert isinstance(top, DirectoryBundle)


def test_a_build_that_cannot_be_made_fails_and_says_why(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)

    with caplog.at_level(WARNING):
        build(module, tmp_path / "missing")

    failed = builds(published(queues))[-1][0]

    assert (failed["status"], failed["bundle"]) == ("failed", None)
    assert "No such file" in failed["error"]
    assert "Could not build" in caplog.text


def test_a_build_beside_a_file_that_is_not_its_record_fails_and_keeps_the_file(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    BuildRecord.beside(tree).write_bytes(b"my own notes")
    module = start(config, queues, now)
    build(module, tree)
    failed = builds(published(queues))[-1][0]

    assert failed["status"] == "failed"
    assert "Cannot read a build record" in failed["error"]
    assert BuildRecord.beside(tree).read_bytes() == b"my own notes"


def test_a_directory_within_the_node_own_directories_cannot_be_built(
    config: LibranetConfig, queues: ModuleQueues, now: list[float]
) -> None:
    directory = config.storage.source_of_truth_dir / "site"
    directory.mkdir(parents=True)
    module = start(config, queues, now)
    build(module, directory)
    failed = builds(published(queues))[-1][0]

    assert failed["status"] == "failed"
    assert "Ignored" in failed["error"]
    assert not BuildRecord.beside(directory).exists()


def test_a_build_id_that_does_not_name_its_directory_is_refused(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    published(queues)

    with raises(ValueError):
        module.handle(
            asked(EventType.BUILD_REQUESTED, build_id="0" * 16, directory=str(tree), password=None)
        )

    assert module.builds == {}
    assert published(queues) == []


def test_an_unexpected_build_failure_fails_it_and_is_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    def broken(*arguments: object) -> None:
        raise RuntimeError()

    monkeypatch.setattr(Build, "run", broken)
    module = start(config, queues, now)

    with caplog.at_level(ERROR):
        build(module, tree)

    failed = builds(published(queues))[-1][0]

    assert (failed["status"], failed["error"]) == ("failed", "RuntimeError")
    assert any(record.exc_info for record in caplog.records)


def test_paths_a_build_leaves_out_are_counted_and_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    caplog: LogCaptureFixture,
) -> None:
    mkfifo(tree / "pipe")
    module = start(config, queues, now)

    with caplog.at_level(WARNING):
        build(module, tree)

    done = builds(published(queues))[-1][0]

    assert (done["status"], done["skipped"]) == ("done", 1)
    assert "Left pipe out of the build of" in caplog.text


def test_an_export_writes_an_archive_holding_all_a_bundle_needs(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, tmp_path: Path
) -> None:
    module = start(config, queues, now)
    bundle = built_bundle(module, queues, tree)
    archive = tmp_path / "tree.zip"
    export_id = export(module, bundle, archive)
    messages = published(queues)
    states = exports(messages)

    assert [state[0]["status"] for state in states] == ["waiting", "running", "done"]
    assert states[-1] == [
        {
            "export_id": export_id,
            "bundle": bundle,
            "archive": str(archive),
            "on_conflict": "refuse",
            "status": "done",
            "error": None,
            "requested_at": START,
            "finished_at": START,
            "objects": 3,
        }
    ]
    assert asked_for(messages) == []

    with ArchiveSource.open(archive) as opened:
        assert ContentId.parse(bundle) in set(opened.iter_prefix("sha256", ""))


def test_an_export_reads_content_held_only_in_an_archive(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    bundle = built_bundle(start(config, queues, now), queues, tree)
    shipped = tmp_path / "shipped.zip"

    with ArchiveSink.create(shipped) as sink:
        for content_id in [stored.content_id for stored in held_objects(store)]:
            sink.write(content_id, store.read(content_id))
            store.delete(content_id)

    storage = config.storage.model_copy(update={"archives": (shipped,)})
    module = start(config.model_copy(update={"storage": storage}), queues, now)
    published(queues)
    export(module, bundle, tmp_path / "again.zip")

    assert exports(published(queues))[-1][0]["status"] == "done"


def test_an_export_lacking_content_fails_and_asks_for_it(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    bundle = built_bundle(module, queues, tree)
    part = ContentId.for_data(b"some notes", "sha256")
    store.delete(part)
    archive = tmp_path / "tree.zip"
    export(module, bundle, archive)
    messages = published(queues)
    failed = exports(messages)[-1][0]

    assert (failed["status"], failed["error"]) == ("failed", "Lacks 1 of the objects it needs")
    assert asked_for(messages) == [part]
    assert not archive.exists()


def test_an_export_that_cannot_be_written_fails_and_says_why(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    caplog: LogCaptureFixture,
) -> None:
    module = start(config, queues, now)
    bundle = built_bundle(module, queues, tree)
    archive = tmp_path / "tree.zip"
    archive.write_bytes(b"something else")

    with caplog.at_level(WARNING):
        export(module, bundle, archive)

    failed = exports(published(queues))[-1][0]

    assert failed["status"] == "failed"
    assert "may not overwrite" in failed["error"]
    assert "Could not export" in caplog.text
    assert archive.read_bytes() == b"something else"


def test_an_export_never_writes_in_the_node_own_directories(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path
) -> None:
    module = start(config, queues, now)
    bundle = built_bundle(module, queues, tree)
    archive = config.storage.source_of_truth_dir / "tree.zip"
    export(module, bundle, archive)
    failed = exports(published(queues))[-1][0]

    assert failed["status"] == "failed"
    assert "Ignored" in failed["error"]
    assert not archive.exists()


def test_an_export_id_that_does_not_name_its_request_is_refused(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tmp_path: Path
) -> None:
    module = start(config, queues, now)
    published(queues)

    with raises(ValueError):
        module.handle(
            asked(
                EventType.EXPORT_REQUESTED,
                export_id="0" * 16,
                bundle=f"sha256/{'0' * 64}",
                archive=str(tmp_path / "site.zip"),
                on_conflict="refuse",
                password=None,
            )
        )

    assert module.exports == {}
    assert published(queues) == []


def test_an_unexpected_export_failure_fails_it_and_is_logged(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    def broken(*arguments: object) -> None:
        raise RuntimeError()

    monkeypatch.setattr(Export, "run", broken)
    module = start(config, queues, now)

    with caplog.at_level(ERROR):
        export(module, f"sha256/{'0' * 64}", tmp_path / "site.zip")

    failed = exports(published(queues))[-1][0]

    assert (failed["status"], failed["error"]) == ("failed", "RuntimeError")
    assert any(record.exc_info for record in caplog.records)


def test_a_build_goes_ahead_of_a_backup_due(
    config: LibranetConfig, queues: ModuleQueues, now: list[float], tree: Path, tmp_path: Path
) -> None:
    module = start(config, queues, now)
    backed_up_bundle(module, queues, tree)
    now[0] += INTERVAL
    site = tmp_path / "site"
    site.mkdir()
    build(module, site)
    messages = published(queues)

    assert builds(messages)[-1][0]["status"] == "done"
    assert reports(messages)[-1][0]["checked_at"] == START

    module.on_idle()

    assert reports(published(queues))[-1][0]["checked_at"] == START + INTERVAL


def test_a_restore_due_goes_ahead_of_a_build_and_builds_and_exports_run_in_turn(
    config: LibranetConfig,
    queues: ModuleQueues,
    now: list[float],
    tree: Path,
    tmp_path: Path,
    store: CasStore,
) -> None:
    module = start(config, queues, now)
    bundle = backed_up_bundle(module, queues, tree)
    store.delete(ContentId.for_data(b"some notes", "sha256"))
    restore(module, bundle, tmp_path / "restored")
    now[0] += config.stats.seek_entry_ttl_seconds
    site = tmp_path / "site"
    site.mkdir()
    build(module, site)
    messages = published(queues)

    # The restore due carries on first, and the build waits its turn.
    assert [state[0]["status"] for state in restores(messages)][-2:] == ["running", "waiting"]
    assert builds(messages)[-1][0]["status"] == "waiting"

    export(module, bundle, tmp_path / "backup.zip")
    messages = published(queues)

    # The build, asked for first, runs next, and the export waits its turn.
    assert builds(messages)[-1][0]["status"] == "done"
    assert exports(messages)[-1][0]["status"] == "waiting"

    module.on_idle()
    failed = exports(published(queues))[-1][0]

    # A backup bundle is protected with the backup secret, which no request gives.
    assert failed["status"] == "failed"
    assert "password" in failed["error"].lower()
