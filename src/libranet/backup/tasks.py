"""Work the backup module does when asked: builds, exports, restores, and imports.

Builds, exports, and restores come from Phase 1 Steps 20 and 38, and imports
from Phase 3 Step 69. A build, an export, or an import is done or fails the
first time it runs, and asking for it again starts it over. A restore goes on
in passes until done (:mod:`libranet.backup.restores`). Tasks are kept in
memory only, so a restart forgets them.
"""

from __future__ import annotations
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    """What a task is doing, or a backup job's last look did, as reported.

    A job is never done: once looked at, it waits for its next look.
    """

    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Task:
    """A build, export, restore, or import asked for at ``requested_at``, and how it went."""

    def __init__(self, requested_at: float) -> None:
        self._requested_at = requested_at
        self._status = TaskStatus.WAITING
        self._error: str | None = None
        self._finished_at: float | None = None

    @property
    def requested_at(self) -> float:
        """When the task was asked for."""
        return self._requested_at

    @property
    def status(self) -> TaskStatus:
        """What the task is doing."""
        return self._status

    def begin(self) -> None:
        """Note that the task is under way."""
        self._status = TaskStatus.RUNNING

    def fail(self, error: Exception, now: float) -> None:
        """Note that the task failed, and why."""
        self._failed(failure_reason(error), now)

    def progress(self) -> dict[str, Any]:
        """What every task reports: what it is doing, why it failed, and when."""
        return {
            "status": self._status.value,
            "error": self._error,
            "requested_at": self._requested_at,
            "finished_at": self._finished_at,
        }

    def _finish(self, now: float) -> None:
        """Note that the task is done."""
        self._status, self._finished_at = TaskStatus.DONE, now

    def _failed(self, reason: str, now: float) -> None:
        """Note that the task failed, for ``reason``."""
        self._status, self._error, self._finished_at = TaskStatus.FAILED, reason, now


def failure_reason(error: Exception) -> str:
    """Why ``error`` says something failed, as reported: its message, or its type if it has none."""
    return str(error) or type(error).__name__
