"""What the backup module last said its jobs, restores, builds, and exports were doing.

The web server does no backup work and keeps no job state of its own, so
what a ``GET`` reads back is whatever the backup module (Step 19) last
published. Each report replaces the one before it, so the module is free to
publish its whole state whenever it changes rather than a delta.

Until the first report arrives — including when the backup module is not
running — there is nothing to read back, and the endpoints answer ``503``
rather than an empty list, which would claim nothing is configured.

The module's receive loop writes reports while request threads read them,
as with the application outcomes the unbundler reports.
"""

from __future__ import annotations
from dataclasses import dataclass
from threading import Lock
from typing import Any, Mapping

from libranet.messaging.envelope import Message

#: Payload members of a ``backup.state`` message, and the endpoint each backs.
JOBS_FIELD = "jobs"
RESTORES_FIELD = "restores"
BUILDS_FIELD = "builds"
EXPORTS_FIELD = "exports"


class InvalidBackupReportError(ValueError):
    """A ``backup.state`` message does not carry the lists it must."""


@dataclass(frozen=True)
class BackupReport:
    """One report of every configured job, and every requested restore, build, and export.

    The entries are passed to clients as the backup module published them,
    so it alone decides what it says about each.
    """

    jobs: tuple[Mapping[str, Any], ...] = ()
    restores: tuple[Mapping[str, Any], ...] = ()
    builds: tuple[Mapping[str, Any], ...] = ()
    exports: tuple[Mapping[str, Any], ...] = ()

    @classmethod
    def from_message(cls, message: Message) -> BackupReport:
        """The report ``message`` carries.

        Raises:
            InvalidBackupReportError: a list is missing, is not an array, or
                holds anything but objects.
        """
        return cls(
            _entries(message, JOBS_FIELD),
            _entries(message, RESTORES_FIELD),
            _entries(message, BUILDS_FIELD),
            _entries(message, EXPORTS_FIELD),
        )

    def entries(self, field: str) -> tuple[Mapping[str, Any], ...]:
        """The report's ``jobs``, ``restores``, ``builds``, or ``exports``.

        Raises:
            KeyError: ``field`` names none of them.
        """
        return {
            JOBS_FIELD: self.jobs,
            RESTORES_FIELD: self.restores,
            BUILDS_FIELD: self.builds,
            EXPORTS_FIELD: self.exports,
        }[field]


class BackupState:
    """The latest report, written by the receive loop and read by request threads."""

    def __init__(self) -> None:
        self._report: BackupReport | None = None
        self._lock = Lock()

    def report(self, report: BackupReport) -> None:
        """Replace what the endpoints read back with ``report``."""
        with self._lock:
            self._report = report

    @property
    def latest(self) -> BackupReport | None:
        """The last report, or ``None`` if the backup module has published none."""
        with self._lock:
            return self._report


def _entries(message: Message, field: str) -> tuple[Mapping[str, Any], ...]:
    """The array of objects ``message`` holds under ``field``.

    Raises:
        InvalidBackupReportError: it is missing, is not an array, or holds
            anything but objects.
    """
    entries = message.get(field)

    if not isinstance(entries, list):
        raise InvalidBackupReportError(f'A backup report\'s "{field}" must be an array')

    if not all(isinstance(entry, dict) for entry in entries):
        raise InvalidBackupReportError(
            f'Every entry of a backup report\'s "{field}" must be an object'
        )

    return tuple(entries)
