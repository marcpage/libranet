"""Tests for the backup module, with the test standing in for the web server's ``/config``
endpoints, and the clock faked."""

from __future__ import annotations
from io import BytesIO
from logging import ERROR, WARNING
from os import mkfifo
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.backup.jobs import JobFileError, load_jobs
from libranet.backup.module import BackupModule, backup_module_factory
from libranet.backup.restores import Restore
from libranet.bundle.extensions import resolve_directory
from libranet.bundle.loading import load_bundle
from libranet.bundle.reassembly import write_file
from libranet.bundle.shapes import DirectoryBundle, FileBundle
from libranet.bundle.storing import store_bundle
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore, source_of_truth_store
from libranet.config.models import (
    BackupConfig,
    IdentityConfig,
    LibranetConfig,
    LoggingConfig,
    StorageConfig,
)
from libranet.identity.keys import load_or_create_backup_secret
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, make_message
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.registry import default_module_specs
from libranet.webserver.backup_state import BackupReport
from libranet.webserver.config_requests import BackupJobRequest, ConflictBehavior, RestoreRequest

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
