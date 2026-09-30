"""Backup jobs, and the file they are kept in (BackupSpecification §3.3).

A job is a directory to keep backed up, and how often to look at it, as
``/config`` configured it (Step 18). Once backed up, it also has a latest
backup: its current bundle, and a digest of what it holds.

Jobs are kept in a JSON file only the backup module opens, not in the stats
database, so the mapping from each directory to its current bundle outlives
the node (§3.3)::

    {"jobs": [{"directory": "/home/me/notes", "interval_seconds": null,
               "latest": {"bundle": "sha256/<hex>", "made_at": 1789000000.0,
                          "entries_digest": "<hex>", "skipped": 0,
                          "layering": {"layers": 1, "extensions": 1}}}]}

The file is replaced whole on every change, so a crash leaves it as it was
before the change or after it. One that cannot be read is an error rather
than no jobs, since saving over it would lose the only record of which
bundle holds each directory's backups.

Each job's latest bundle is also kept expanded, every entry it holds with its
extensions overlaid, so that the next backup is built from it without reading
the bundle back (Phase 2 Step 48). A million-file directory's entries do not
belong in the jobs file, so each job's are kept in a file of their own, named
by the job's id, in a directory beside it (:class:`ExpandedBackups`). A
change to metadata alone that a backup held back is kept there too (Phase 2
Step 49).
"""

from __future__ import annotations
from dataclasses import dataclass
from json import dumps, loads
from logging import getLogger
from math import isfinite
from pathlib import Path
from typing import Any, Final, Iterable

from libranet.atomic_file import write_atomically
from libranet.bundle.errors import BundleError
from libranet.bundle.layering import Layering, Superseded
from libranet.bundle.protection import protect, unprotect
from libranet.cas.content_id import ContentId
from libranet.json_format import compact_json
from libranet.webserver.config_requests import BackupJobRequest

_LOGGER = getLogger(__name__)

# The files are this node's own, and a million-file directory's run to
# hundreds of megabytes, so the only limit is one no directory reaches.
_MAX_EXPANDED_BYTES: Final = 1 << 40


class JobFileError(ValueError):
    """The backup jobs file cannot be read, or does not hold backup jobs."""


@dataclass(frozen=True)
class LatestBackup:
    """A job's current bundle (§3.3), and what it was made from.

    ``entries_digest`` is the SHA-256 of the bundle's entries, without the
    versions it supersedes, so that building an unchanged directory again
    can be told apart from a change where the bundle cannot be read.
    ``skipped`` counts the paths the bundle leaves out
    (:class:`~libranet.bundle.building.DirectoryBuild`). ``layering`` is
    where the bundle sits among update layers
    (:mod:`libranet.bundle.layering`), not known for one made before layers
    were written.

    Raises:
        ValueError: ``made_at`` is not finite, or ``skipped`` is negative.
    """

    bundle: ContentId
    made_at: float
    entries_digest: str
    skipped: int = 0
    layering: Layering | None = None

    def __post_init__(self) -> None:
        if not isfinite(self.made_at):
            raise ValueError(f"made_at must be finite, got {self.made_at}")

        if self.skipped < 0:
            raise ValueError(f"skipped must not be negative, got {self.skipped}")

    @classmethod
    def from_value(cls, value: object) -> LatestBackup:
        """The latest backup a saved JSON object describes.

        A ``fingerprint``, saved before Phase 2 Step 49, is not used.

        Raises:
            ValueError: it is not a latest-backup object, or describes an
                unusable one.
        """
        if not isinstance(value, dict):
            raise ValueError('A backup job\'s "latest" must be an object')

        skipped = value.get("skipped")

        if not isinstance(skipped, int) or isinstance(skipped, bool):
            raise ValueError('"skipped" must be an integer')

        layering = value.get("layering")

        return cls(
            ContentId.parse(_string(value, "bundle")),
            _number(value, "made_at"),
            _string(value, "entries_digest"),
            skipped,
            None if layering is None else Layering.from_value(layering),
        )

    def value(self) -> dict[str, Any]:
        """The JSON object this is saved as."""
        return {
            "bundle": str(self.bundle),
            "made_at": self.made_at,
            "entries_digest": self.entries_digest,
            "skipped": self.skipped,
            "layering": None if self.layering is None else self.layering.value(),
        }


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

    @classmethod
    def from_value(cls, value: object) -> BackupJob:
        """The job a saved JSON object describes.

        Raises:
            ValueError: it is not a job object, or describes an unusable job.
        """
        if not isinstance(value, dict):
            raise ValueError("A backup job must be an object")

        interval_seconds = value.get("interval_seconds")
        latest = value.get("latest")

        return cls(
            BackupJobRequest(
                _string(value, "directory"),
                None if interval_seconds is None else _number(value, "interval_seconds"),
            ),
            None if latest is None else LatestBackup.from_value(latest),
        )

    def value(self) -> dict[str, Any]:
        """The JSON object this job is saved as."""
        return {
            "directory": self.request.directory,
            "interval_seconds": self.request.interval_seconds,
            "latest": None if self.latest is None else self.latest.value(),
        }


class ExpandedBackups:
    """Each job's latest bundle, kept expanded, in a file of its own named by the job's id.

    A file holds a :class:`~libranet.bundle.layering.Superseded` as JSON,
    password-protected with the backup secret as the bundle is
    (BackupSpecification §4.4), since its names and part hashes are what
    encrypting the bundle keeps from whoever can fetch it. Unlike the bundle,
    it is not limited to one object's size. A file the secret does not open,
    as after the secret was lost and made anew, is not used, so nothing is
    layered over a bundle the secret cannot read.

    Each is replaced whole, so a crash leaves it as it was before or after,
    and is written before the job's latest backup is saved. One naming a
    bundle other than the job's latest is left over from a backup whose
    saving failed, so it is not used.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path(self, job_id: str) -> Path:
        """Where job ``job_id``'s latest bundle is kept expanded."""
        return self._directory / job_id

    def load(self, job_id: str, bundle: ContentId, secret: bytes) -> Superseded | None:
        """``bundle`` as job ``job_id`` keeps it expanded; ``None`` if it keeps no usable one.

        Nothing kept is not logged: a job backed up before bundles were kept
        expanded keeps none until its next backup. Anything else is, and the
        bundle is read back instead.
        """
        path = self.path(job_id)

        try:
            data = path.read_bytes()

        except FileNotFoundError:
            # Not logged: a job backed up before bundles were kept expanded has none yet.
            return None

        except OSError as error:
            _LOGGER.warning("Cannot read %s, so the last backup is read back: %s", path, error)
            return None

        try:
            expanded = Superseded.from_value(loads(unprotect(data, secret, _MAX_EXPANDED_BYTES)))

        except (BundleError, ValueError) as error:
            _LOGGER.warning(
                "%s is not a bundle kept expanded with this node's backup secret, "
                "so the last backup is read back: %s",
                path,
                error,
            )
            return None

        if expanded.bundle != bundle:
            _LOGGER.info(
                "%s keeps %s, not the latest backup %s, so the last backup is read back",
                path,
                expanded.bundle,
                bundle,
            )
            return None

        return expanded

    def save(self, job_id: str, expanded: Superseded, secret: bytes) -> None:
        """Keep ``expanded`` as job ``job_id``'s latest bundle, in place of any kept before.

        Raises:
            OSError: it could not be written.
        """
        plaintext = compact_json(expanded.value())
        write_atomically(self.path(job_id), protect(plaintext, secret, _MAX_EXPANDED_BYTES))

    def remove(self, job_id: str) -> None:
        """Keep nothing for job ``job_id`` any longer.

        A file that cannot be deleted is logged and left, since it is not used
        unless it names the job's latest backup.
        """
        path = self.path(job_id)

        try:
            path.unlink(missing_ok=True)

        except OSError as error:
            _LOGGER.warning("Could not delete %s: %s", path, error)


def load_jobs(path: Path) -> dict[str, BackupJob]:
    """The jobs saved at ``path``, by id; none if nothing has been saved there.

    Raises:
        JobFileError: the file cannot be read, or does not hold backup jobs.
    """
    try:
        value = loads(path.read_bytes())

    except FileNotFoundError:
        # Not logged: there are no jobs until one is saved.
        return {}

    except (OSError, ValueError) as error:
        raise JobFileError(f"Cannot read backup jobs from {path}: {error}") from None

    jobs = value.get("jobs") if isinstance(value, dict) else None

    if not isinstance(jobs, list):
        raise JobFileError(f'{path} must hold an object with a "jobs" array')

    try:
        parsed = [BackupJob.from_value(job) for job in jobs]

    except ValueError as error:
        raise JobFileError(f"{path} holds an unusable backup job: {error}") from None

    return {job.job_id: job for job in parsed}


def save_jobs(path: Path, jobs: Iterable[BackupJob]) -> None:
    """Replace what ``path`` holds with ``jobs``.

    Raises:
        OSError: the file could not be written.
    """
    value = {"jobs": [job.value() for job in sorted(jobs, key=lambda job: job.directory)]}
    write_atomically(path, dumps(value, indent=2).encode("utf-8"))


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
