"""The backup module process (Phase 1 Steps 19 and 20).

It keeps the directories configured through ``/config`` (Step 18) backed up
as encrypted directory bundles in the source of truth (BackupSpecification
§3), and restores a backup bundle into a directory (§5), as the web server
asks::

    backup.job_configured     {"job_id", "directory", "interval_seconds"}
    backup.job_removed        {"job_id"}
    backup.run_requested      {"job_id"}
    backup.restore_requested  {"restore_id", "bundle", "directory", "on_conflict"}

A job's directory is looked at once it is configured, again when the module
starts, and ``interval_seconds`` after each look, or ``backup.interval_seconds``
for a job that gives none. It is backed up whenever it may have changed since
its latest backup (:mod:`libranet.backup.changes`), and whenever a backup is
asked for. A backup reads only the files whose metadata changed, and stores
only those whose bytes did (:mod:`libranet.backup.runs`). Configuring a job's
directory again sets its interval anew and keeps its backups. Backups run one
at a time, each between two messages, so a long one holds up the rest, and
shutdown waits for it.

Every object a backup stores is announced as the validator announces one it
stores, so eviction and stats treat backup content like any other::

    data.stored  {"algorithm", "hash", "node_id", "size"}

``node_id`` is this node's own, since this node is where the content came
from.

The node's own directories, as its config lists them, are ignored both when
looking for changes and when backing up. Whatever holds them is backed up as
though they were not there, and a job whose directory lies within one fails
as though that directory did not exist. A restore never writes in them.

A restore is carried on in passes (:mod:`libranet.backup.restores`), each
restoring whatever it can from what this node holds. Content it lacks, whether
the bundle, an extension, or a file's parts, is asked for as a miss would be::

    data.not_found  {"algorithm", "hash"}

so that the fetcher (Step 12) retrieves it and it joins this node's seek list.
The restore carries on as that content is stored, and asks again for whatever
it still lacks every half ``stats.seek_entry_ttl_seconds``, so that it stays
in the seek list for peers that connect later. Asking for a restore that is
still waiting carries it on at once. Asking for one that is done or has failed
starts it again. Restores are kept in memory only, so a restart forgets them.
A pass runs between two messages, as a backup does, and a restore due goes
ahead of any backup.

Jobs, and the bundle each was last backed up to, are kept in a file
(:mod:`libranet.backup.jobs`). Removing a job forgets its bundle, but leaves
the content in CAS. What every job and restore is doing is reported whenever
it changes, for the web server to serve at ``GET /config/backups`` and ``GET
/config/restores``::

    backup.state  {"jobs": [...], "restores": [...]}

Each job is reported as::

    {"job_id", "directory", "interval_seconds", "status", "error", "checked_at",
     "bundle", "backed_up_at", "skipped"}

``status`` is ``waiting`` between looks, ``running`` during a backup, and
``failed`` if the last look or backup failed, with ``error`` saying why.
``bundle`` is the job's current bundle, made at ``backed_up_at``, and
``skipped`` counts the paths it leaves out, which are logged.

Each restore is reported, in the order they were asked for, as::

    {"restore_id", "bundle", "directory", "on_conflict", "status", "error",
     "requested_at", "finished_at", "restored", "skipped", "missing"}

``status`` is ``waiting`` between passes, ``running`` during one, ``done``
once every entry is restored or left out, and ``failed`` if it could not go
on, with ``error`` saying why. ``restored`` counts the entries restored so
far, ``skipped`` those left out, which are logged, and ``missing`` the objects
it waits on. Times are seconds since the epoch, and ``null`` until there is
one.

The backup secret is read, or first made, when a backup or restore first needs
it (§4.2), so a problem with it fails that backup or restore, where it is
reported.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from enum import StrEnum
from logging import Logger
from time import time
from typing import Any, Callable, ClassVar, Final, Mapping

from libranet.backup.changes import ChangeDetector, PollingDetector
from libranet.backup.jobs import BackupJob, load_jobs, save_jobs
from libranet.backup.restores import Restore
from libranet.backup.runs import AnnouncingStore, back_up
from libranet.bundle.building import IgnoredPaths
from libranet.bundle.errors import BundleError
from libranet.cas.content_id import ContentId
from libranet.cas.store import source_of_truth_store
from libranet.config.models import LibranetConfig
from libranet.identity.errors import KeyFileError
from libranet.identity.keys import load_or_create_backup_secret
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, event_of
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.webserver.config_requests import BackupJobRequest, ConflictBehavior, RestoreRequest

# A restore asks again for what it lacks this many times in the life of a seek
# entry, so it never ages out of the seek list.
_ASKS_PER_SEEK_ENTRY_TTL: Final = 2


class JobStatus(StrEnum):
    """What a job is doing, as reported."""

    WAITING = "waiting"
    RUNNING = "running"
    FAILED = "failed"


@dataclass
class _Progress:
    """When a job is next looked at, and how the last look went; kept in memory only."""

    due_at: float
    requested: bool = False
    status: JobStatus = JobStatus.WAITING
    error: str | None = None
    checked_at: float | None = None


class BackupModule(ModuleBase):
    """Keeps configured directories backed up into the source of truth, and restores them."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.BACKUP_JOB_CONFIGURED,
            EventType.BACKUP_JOB_REMOVED,
            EventType.BACKUP_RUN_REQUESTED,
            EventType.RESTORE_REQUESTED,
            EventType.DATA_STORED,
        }
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        detector: ChangeDetector | None = None,
    ) -> None:
        super().__init__(name, queues, logger=logger, clock=clock, poll_interval=poll_interval)
        self._config = config
        self._detector = detector or PollingDetector(config.directories())
        self._store = AnnouncingStore(source_of_truth_store(config.storage), self._announce)
        self._node_id: ContentId | None = None
        self._secret: bytes | None = None
        self._jobs: dict[str, BackupJob] = {}
        self._progress: dict[str, _Progress] = {}
        self._restores: dict[str, Restore] = {}
        self._handlers: Mapping[EventType, Callable[[Message], None]] = {
            EventType.BACKUP_JOB_CONFIGURED: self._on_job_configured,
            EventType.BACKUP_JOB_REMOVED: self._on_job_removed,
            EventType.BACKUP_RUN_REQUESTED: self._on_run_requested,
            EventType.RESTORE_REQUESTED: self._on_restore_requested,
            EventType.DATA_STORED: self._on_data_stored,
        }

    @property
    def node_id(self) -> ContentId:
        """This node's id; only available once the module has started."""
        if self._node_id is None:
            raise RuntimeError("The backup module is not running")

        return self._node_id

    @property
    def jobs(self) -> Mapping[str, BackupJob]:
        """Every configured job, by id."""
        return self._jobs

    @property
    def restores(self) -> Mapping[str, Restore]:
        """Every restore asked for since the module started, by id."""
        return self._restores

    def on_start(self) -> None:
        """Read the saved jobs and report them; each is looked at once the module is idle.

        The node identity is read here rather than passed across the process
        boundary, for the same reason the web server reads it: the private
        key stays on disk.

        Raises:
            JobFileError: the saved jobs cannot be read. The module stops
                rather than start with no jobs and save over them.
        """
        self._node_id = load_node_identity(self._config).node_id
        self._jobs = load_jobs(self._config.storage.backup_jobs_path)
        now = self._clock()
        self._progress = {job_id: _Progress(now) for job_id in self._jobs}
        self._report()
        self.logger.info("Keeping %d directories backed up", len(self._jobs))

    def on_idle(self) -> None:
        """Carry on with the next restore or job due."""
        self._work_next()

    def handle(self, message: Message) -> None:
        """React to one ``/config`` request, or content stored, then to the next restore or job due.

        A malformed message raises, and :meth:`run` logs it.
        """
        self._handlers[event_of(message)](message)
        self._work_next()

    def _on_job_configured(self, message: Message) -> None:
        request = BackupJobRequest(message["directory"], message.get("interval_seconds"))

        if request.job_id != message["job_id"]:
            raise ValueError(f"Job {message['job_id']} does not name {request.directory}")

        job = self._jobs.get(request.job_id)
        configured = BackupJob(request) if job is None else replace(job, request=request)
        self._keep({**self._jobs, request.job_id: configured})
        self._progress.setdefault(request.job_id, _Progress(0.0)).due_at = self._clock()
        self._report()
        self.logger.info("Keeping %s backed up as job %s", request.directory, request.job_id)

    def _on_job_removed(self, message: Message) -> None:
        job_id: str = message["job_id"]
        job = self._jobs.get(job_id)

        if job is None:
            self.logger.info("There is no backup job %s to remove", job_id)
            return

        self._keep({other: kept for other, kept in self._jobs.items() if other != job_id})
        del self._progress[job_id]
        self._report()
        self.logger.info("No longer backing up %s", job.directory)

    def _on_run_requested(self, message: Message) -> None:
        job_id: str = message["job_id"]
        progress = self._progress.get(job_id)

        if progress is None:
            self.logger.info("There is no backup job %s to run", job_id)
            return

        progress.requested = True

    def _on_restore_requested(self, message: Message) -> None:
        request = RestoreRequest(
            ContentId.parse(message["bundle"]),
            message["directory"],
            ConflictBehavior(message["on_conflict"]),
        )

        if request.restore_id != message["restore_id"]:
            raise ValueError(
                f"Restore {message['restore_id']} does not name {request.bundle} "
                f"into {request.directory}"
            )

        now = self._clock()
        restore = self._restores.get(request.restore_id)

        if restore is None or restore.finished:
            self._restores.pop(request.restore_id, None)
            self._restores[request.restore_id] = Restore(request, now, self._ask_interval())
            self.logger.info("Restoring %s into %s", request.bundle, request.directory)

        else:
            restore.ask_again(request, now)
            self.logger.info("Carrying on restoring %s into %s", request.bundle, request.directory)

    def _on_data_stored(self, message: Message) -> None:
        content_id = ContentId.create(message["algorithm"], message["hash"])
        now = self._clock()
        landed = [restore.landed(content_id, now) for restore in self._restores.values()]

        if any(landed):
            self._report()

    def _work_next(self) -> None:
        """Carry on with the restore longest due, if any, or else look at the next job due."""
        now = self._clock()
        due = [
            (restore.due_at, restore_id)
            for restore_id, restore in self._restores.items()
            if restore.is_due(now)
        ]

        if due:
            self._carry_on(self._restores[min(due)[-1]])

        else:
            self._back_up_next()

    def _carry_on(self, restore: Restore) -> None:
        """Restore whatever is held of what ``restore`` has left, and ask for what is lacked."""
        request = restore.request
        restore.begin()
        self._report()

        try:
            restore_pass = restore.attempt(
                self._store,
                self._backup_secret(),
                IgnoredPaths(self._config.directories()),
                self._clock(),
            )

        except (OSError, BundleError, KeyFileError) as error:
            restore.fail(error, self._clock())
            self.logger.warning(
                "Could not restore %s into %s: %s", request.bundle, request.directory, error
            )

        except Exception as error:
            restore.fail(error, self._clock())
            self.logger.exception("Restoring %s into %s failed", request.bundle, request.directory)

        else:
            for path, reason in restore_pass.skipped.items():
                self.logger.warning("Left %s out of %s: %s", path, request.directory, reason)

            for content_id in restore_pass.ask_for:
                self.publish(
                    EventType.DATA_NOT_FOUND,
                    {"algorithm": content_id.algorithm, "hash": content_id.hash},
                )

            self._log_restore(restore)

        self._report()

    def _log_restore(self, restore: Restore) -> None:
        request = restore.request

        if restore.finished:
            self.logger.info("Restored %s into %s", request.bundle, request.directory)
            return

        self.logger.info(
            "Restoring %s into %s waits on %d objects not held here",
            request.bundle,
            request.directory,
            len(restore.missing),
        )

    def _back_up_next(self) -> None:
        """Look at the job a backup was asked for, or else the one longest due, if any."""
        now = self._clock()
        due = [
            (not progress.requested, progress.due_at, job_id)
            for job_id, progress in self._progress.items()
            if progress.requested or progress.due_at <= now
        ]

        if due:
            job_id = min(due)[-1]
            self._look_at(self._jobs[job_id], self._progress[job_id])

    def _look_at(self, job: BackupJob, progress: _Progress) -> None:
        """Back ``job`` up if a backup was asked for, or its directory may have changed."""
        requested, progress.requested = progress.requested, False
        progress.checked_at = self._clock()

        try:
            fingerprint = self._detector.fingerprint(job.directory)
            latest = job.latest

            if requested or latest is None or fingerprint != latest.fingerprint:
                progress.status = JobStatus.RUNNING
                self._report()
                backup = back_up(
                    job.directory,
                    fingerprint,
                    latest,
                    self._store,
                    self._backup_secret(),
                    self._clock(),
                    self._config.storage.max_object_bytes,
                    self._config.directories(),
                )
                self._keep({**self._jobs, job.job_id: replace(job, latest=backup.latest)})
                self._log_backup(job, backup.latest.bundle, backup.skipped)

        except (OSError, BundleError, KeyFileError) as error:
            _fail(progress, error)
            self.logger.warning("Could not back up %s: %s", job.directory, error)

        except Exception as error:
            _fail(progress, error)
            self.logger.exception("Backing up %s failed", job.directory)

        else:
            progress.status, progress.error = JobStatus.WAITING, None

        progress.due_at = self._clock() + self._interval_of(job)
        self._report()

    def _log_backup(self, job: BackupJob, bundle: ContentId, skipped: Mapping[str, str]) -> None:
        for path, reason in skipped.items():
            self.logger.warning("Left %s out of the backup of %s: %s", path, job.directory, reason)

        if job.latest is not None and bundle == job.latest.bundle:
            self.logger.info("%s is unchanged since its backup %s", job.directory, bundle)

        else:
            self.logger.info("Backed up %s as %s", job.directory, bundle)

    def _backup_secret(self) -> bytes:
        """The node's backup secret, made the first time it is needed (§4.2).

        Raises:
            KeyFileError: the stored secret is not one this node wrote.
            OSError: it could not be read or written.
        """
        if self._secret is None:
            identity = self._config.identity
            key_dir = identity.resolved_key_dir(self._config.storage)
            self._secret = load_or_create_backup_secret(key_dir / identity.backup_secret_path_name)

        return self._secret

    def _announce(self, content_id: ContentId, size: int) -> None:
        self.publish(
            EventType.DATA_STORED,
            {
                "algorithm": content_id.algorithm,
                "hash": content_id.hash,
                "node_id": str(self.node_id),
                "size": size,
            },
        )

    def _interval_of(self, job: BackupJob) -> float:
        interval = job.request.interval_seconds
        return self._config.backup.interval_seconds if interval is None else interval

    def _ask_interval(self) -> float:
        """How often a restore asks again for what it lacks, so it stays in the seek list."""
        return self._config.stats.seek_entry_ttl_seconds / _ASKS_PER_SEEK_ENTRY_TTL

    def _keep(self, jobs: dict[str, BackupJob]) -> None:
        """Save ``jobs``, then make them the jobs kept, so what is kept is always saved.

        Raises:
            OSError: they could not be saved; the jobs kept are unchanged.
        """
        save_jobs(self._config.storage.backup_jobs_path, jobs.values())
        self._jobs = jobs

    def _report(self) -> None:
        """Publish what every job and restore is doing, for ``GET /config/backups`` and ``restores``."""
        jobs = sorted(self._jobs.values(), key=lambda job: job.directory)
        self.publish(
            EventType.BACKUP_STATE,
            {
                "jobs": [self._job_report(job) for job in jobs],
                "restores": [restore.report() for restore in self._restores.values()],
            },
        )

    def _job_report(self, job: BackupJob) -> dict[str, Any]:
        progress = self._progress[job.job_id]
        latest = job.latest
        return {
            "job_id": job.job_id,
            "directory": job.request.directory,
            "interval_seconds": self._interval_of(job),
            "status": progress.status.value,
            "error": progress.error,
            "checked_at": progress.checked_at,
            "bundle": None if latest is None else str(latest.bundle),
            "backed_up_at": None if latest is None else latest.made_at,
            "skipped": 0 if latest is None else latest.skipped,
        }


def _fail(progress: _Progress, error: Exception) -> None:
    """Report that a job's last look failed, and why."""
    progress.status, progress.error = JobStatus.FAILED, str(error) or type(error).__name__


def backup_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`BackupModule`."""
    return BackupModule(name, queues, config)
