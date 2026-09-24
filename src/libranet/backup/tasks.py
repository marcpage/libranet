"""Work the backup module does once, when asked: builds (Phase 1 Step 38).

Unlike a restore, which goes on in passes until done, a task is done or fails
the first time it runs. Asking for it again starts it over. Tasks are kept in
memory only, so a restart forgets them, as it forgets restores.
"""

from __future__ import annotations
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    """What a task is doing, as reported."""

    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Task:
    """A build asked for at ``requested_at``, and how it went."""

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
        self._failed(str(error) or type(error).__name__, now)

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
