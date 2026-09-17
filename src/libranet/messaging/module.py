"""The base class every module process is built on.

A module is given its queues through its constructor (manual dependency
injection), publishes through :meth:`ModuleBase.publish`, and receives only
the broadcasts it subscribes to. Subclasses declare ``subscriptions`` and
implement :meth:`ModuleBase.handle`; :meth:`ModuleBase.run` supplies the
receive loop. Because nothing here starts a process, a module can be
unit-tested by constructing it with plain ``queue.Queue`` objects.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from logging import Logger
from queue import Empty
from time import monotonic, time
from typing import Any, Callable, ClassVar, Mapping, Protocol

from libranet.logging_setup import get_logger
from libranet.messaging.envelope import (
    InvalidMessageError,
    Message,
    event_of,
    make_message,
    source_of,
    validate_message,
)
from libranet.messaging.events import EventType
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName

DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class StopSignal(Protocol):
    """Anything with ``is_set()``: a ``threading.Event`` or ``multiprocessing.Event``."""

    def is_set(self) -> bool: ...


class ModuleBase(ABC):
    """Publish/receive plumbing shared by every module.

    Broadcasts reach every module, including the one that published them;
    a module never receives its own messages back, and receives only the
    event types in ``subscriptions`` plus :attr:`EventType.SHUTDOWN`.
    """

    subscriptions: ClassVar[frozenset[EventType]] = frozenset()

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be positive, got {poll_interval}")

        self._name = name
        self._queues = queues
        self._logger = logger or get_logger(name)
        self._clock = clock
        self._poll_interval = poll_interval

    @property
    def name(self) -> ModuleName:
        """The module this instance runs as; the ``source`` of what it publishes."""
        return self._name

    @property
    def logger(self) -> Logger:
        """This module's named logger."""
        return self._logger

    def publish(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        """Send a message to the dispatcher for broadcast, and return it."""
        message = make_message(event, self._name, payload, clock=self._clock)
        self._queues.outbox.put(message)
        return message

    def wants(self, message: Message) -> bool:
        """Whether a validated broadcast is meant for this module."""
        if source_of(message) == self._name:
            return False

        event = event_of(message)
        return event == EventType.SHUTDOWN or event in self.subscriptions

    def receive(self, timeout: float | None = None) -> Message | None:
        """The next broadcast this module wants, or ``None`` on timeout.

        Unwanted and malformed messages are discarded while waiting.
        ``timeout=None`` blocks until a wanted message arrives.
        """
        deadline = None if timeout is None else monotonic() + timeout

        while True:
            remaining = None if deadline is None else max(0.0, deadline - monotonic())

            try:
                raw = self._queues.inbox.get(timeout=remaining)

            except Empty:
                return None

            try:
                message = validate_message(raw)

            except InvalidMessageError as error:
                self._logger.warning("Dropping malformed message: %s", error)
                continue

            if self.wants(message):
                return message

    def run(self, stop: StopSignal | None = None) -> None:
        """Handle wanted broadcasts until ``SHUTDOWN`` arrives or ``stop`` is set.

        An exception from :meth:`handle` is logged and the loop carries on,
        so one bad message cannot take the module down.
        """
        self._logger.info("Module %s starting", self._name)
        self.on_start()

        try:
            while stop is None or not stop.is_set():
                message = self.receive(timeout=self._poll_interval)

                if message is None:
                    self.on_idle()
                    continue

                if event_of(message) == EventType.SHUTDOWN:
                    self._logger.info("Module %s received shutdown", self._name)
                    break

                try:
                    self.handle(message)

                except Exception:
                    self._logger.exception(
                        "Module %s failed handling %s", self._name, event_of(message)
                    )

        finally:
            self.on_stop()
            self._logger.info("Module %s stopped", self._name)

    @abstractmethod
    def handle(self, message: Message) -> None:
        """React to one subscribed broadcast."""

    def on_start(self) -> None:
        """Hook run once before the receive loop starts."""

    def on_idle(self) -> None:
        """Hook run whenever a poll interval passes with nothing to handle."""

    def on_stop(self) -> None:
        """Hook run once after the receive loop ends, however it ends."""
