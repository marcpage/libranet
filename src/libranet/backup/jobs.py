"""Backup jobs, and the file they are kept in (BackupSpecification §3.3).

A job is a directory to keep backed up, and how often to look at it, as
``/config`` configured it (Step 18). Once backed up, it also has a latest
backup: its current bundle, and what the directory was like when the bundle
was made, so the next look can tell whether anything changed.

Jobs are kept in a JSON file only the backup module opens, not in the stats
database, so the mapping from each directory to its current bundle outlives
the node (§3.3)::

    {"jobs": [{"directory": "/home/me/notes", "interval_seconds": null,
               "latest": {"bundle": "sha256/<hex>", "made_at": 1789000000.0,
                          "fingerprint": "<hex>", "entries_digest": "<hex>",
                          "skipped": 0}}]}

The file is replaced whole on every change, so a crash leaves it as it was
before the change or after it. One that cannot be read is an error rather
than no jobs, since saving over it would lose the only record of which
bundle holds each directory's backups.
"""

from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from math import isfinite
from pathlib import Path
from typing import Any, Iterable

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.webserver.config_requests import BackupJobRequest


class JobFileError(ValueError):
    """The backup jobs file cannot be read, or does not hold backup jobs."""


@dataclass(frozen=True)
class LatestBackup:
    """A job's current bundle (§3.3), and what it was made from.

    ``fingerprint`` is what the directory was like when the bundle was made,
    as a :class:`~libranet.backup.changes.ChangeDetector` describes it.
    ``entries_digest`` is the SHA-256 of the bundle's entries, without the
    versions it supersedes, so that building an unchanged directory again
    can be told apart from a change. ``skipped`` counts the paths the bundle
    leaves out (:class:`~libranet.bundle.building.DirectoryBuild`).

    Raises:
        ValueError: ``made_at`` is not finite, or ``skipped`` is negative.
    """

    bundle: ContentId
    made_at: float
    fingerprint: str
    entries_digest: str
    skipped: int = 0

    def __post_init__(self) -> None:
        if not isfinite(self.made_at):
            raise ValueError(f"made_at must be finite, got {self.made_at}")

        if self.skipped < 0:
            raise ValueError(f"skipped must not be negative, got {self.skipped}")


@dataclass(frozen=True)
class BackupJob:
    """A directory kept backed up, and its latest backup once it has one."""

    request: BackupJobRequest
    latest: LatestBackup | None = None

    @property
    def job_id(self) -> str:
        """What names the job: derived from its directory (Step 18)."""
        return self.request.job_id

    @property
    def directory(self) -> Path:
        """The directory kept backed up."""
        return Path(self.request.directory)


def load_jobs(path: Path) -> dict[str, BackupJob]:
    """The jobs saved at ``path``, by id; none if nothing has been saved there.

    Raises:
        JobFileError: the file cannot be read, or does not hold backup jobs.
    """
    try:
        value = loads(path.read_bytes())

    except FileNotFoundError:
        return {}

    except (OSError, ValueError) as error:
        raise JobFileError(f"Cannot read backup jobs from {path}: {error}") from None

    jobs = value.get("jobs") if isinstance(value, dict) else None

    if not isinstance(jobs, list):
        raise JobFileError(f'{path} must hold an object with a "jobs" array')

    try:
        parsed = [_job(job) for job in jobs]

    except ValueError as error:
        raise JobFileError(f"{path} holds an unusable backup job: {error}") from None

    return {job.job_id: job for job in parsed}


def save_jobs(path: Path, jobs: Iterable[BackupJob]) -> None:
    """Replace what ``path`` holds with ``jobs``.

    Raises:
        OSError: the file could not be written.
    """
    value = {"jobs": [_job_value(job) for job in sorted(jobs, key=lambda job: job.directory)]}
    write_atomically(path, dumps(value, indent=2).encode("utf-8"))


def _job(value: object) -> BackupJob:
    """The job a saved object describes.

    Raises:
        ValueError: it is not a job object, or describes an unusable job.
    """
    if not isinstance(value, dict):
        raise ValueError("A backup job must be an object")

    interval = value.get("interval_seconds")
    latest = value.get("latest")

    return BackupJob(
        BackupJobRequest(
            _string(value, "directory"),
            None if interval is None else _number(value, "interval_seconds"),
        ),
        None if latest is None else _latest(latest),
    )


def _latest(value: object) -> LatestBackup:
    """The latest backup a saved object describes.

    Raises:
        ValueError: it is not a latest-backup object, or describes an unusable one.
    """
    if not isinstance(value, dict):
        raise ValueError('A backup job\'s "latest" must be an object')

    skipped = value.get("skipped")

    if not isinstance(skipped, int) or isinstance(skipped, bool):
        raise ValueError('"skipped" must be an integer')

    return LatestBackup(
        ContentId.parse(_string(value, "bundle")),
        _number(value, "made_at"),
        _string(value, "fingerprint"),
        _string(value, "entries_digest"),
        skipped,
    )


def _string(value: dict[str, Any], key: str) -> str:
    """The string ``value`` holds at ``key``.

    Raises:
        ValueError: it holds something else there, or nothing.
    """
    field = value.get(key)

    if not isinstance(field, str):
        raise ValueError(f'"{key}" must be a string')

    return field


def _number(value: dict[str, Any], key: str) -> float:
    """The number ``value`` holds at ``key``.

    Raises:
        ValueError: it holds something else there, or nothing.
    """
    field = value.get(key)

    if not isinstance(field, (int, float)) or isinstance(field, bool):
        raise ValueError(f'"{key}" must be a number')

    return float(field)


def _job_value(job: BackupJob) -> dict[str, Any]:
    """The object ``job`` is saved as."""
    return {
        "directory": job.request.directory,
        "interval_seconds": job.request.interval_seconds,
        "latest": None if job.latest is None else _latest_value(job.latest),
    }


def _latest_value(latest: LatestBackup) -> dict[str, Any]:
    """The object ``latest`` is saved as."""
    return {
        "bundle": str(latest.bundle),
        "made_at": latest.made_at,
        "fingerprint": latest.fingerprint,
        "entries_digest": latest.entries_digest,
        "skipped": latest.skipped,
    }
