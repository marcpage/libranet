"""The dispatcher: the hub every module's messages pass through.

It reads every module's outbox and copies each valid message into every
module's inbox; modules filter for what they care about (see
:class:`~libranet.messaging.module.ModuleBase`). Messages from one outbox
are broadcast in the order they were published; no ordering is promised
between different outboxes.

A ``multiprocessing.Queue`` cannot be waited on alongside others, so
:meth:`Dispatcher.run` gives each outbox a reader thread that forwards into
a single local queue, and broadcasts from that queue on the calling thread.
"""

from __future__ import annotations
from logging import Logger
from queue import Empty, SimpleQueue
from threading import Event, Thread
from typing import Mapping

from libranet.logging_setup import get_logger
from libranet.messaging.envelope import InvalidMessageError, Message, event_of, validate_message
from libranet.messaging.events import EventType
from libranet.messaging.module import StopSignal
from libranet.messaging.queues import MessageQueue, ModuleQueues
from libranet.modules import ModuleName

DEFAULT_POLL_INTERVAL_SECONDS = 0.1


class Dispatcher:
    """Broadcasts every module's published messages to every module."""

    def __init__(
        self,
        endpoints: Mapping[ModuleName, ModuleQueues],
        *,
        logger: Logger | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be positive, got {poll_interval}")

        self._endpoints = dict(endpoints)
        self._logger = logger or get_logger(ModuleName.DISPATCHER)
        self._poll_interval = poll_interval

    def dispatch(self, raw: object) -> Message | None:
        """Broadcast one message to every inbox; returns it, or ``None`` if invalid.

        Malformed messages are logged and dropped rather than raised, so a
        misbehaving module cannot stop the bus.
        """
        try:
            message = validate_message(raw)

        except InvalidMessageError as error:
            self._logger.warning("Dropping malformed message: %s", error)
            return None

        for queues in self._endpoints.values():
            queues.inbox.put(message)

        return message

    def dispatch_pending(self) -> int:
        """Broadcast whatever is already waiting in the outboxes, without blocking.

        Returns the number of valid messages broadcast. Intended for
        single-threaded tests; :meth:`run` is the production loop.
        """
        count = 0

        for queues in self._endpoints.values():
            while True:
                try:
                    raw = queues.outbox.get(block=False)

                except Empty:
                    break

                if self.dispatch(raw) is not None:
                    count += 1

        return count

    def run(self, stop: StopSignal | None = None) -> None:
        """Broadcast until a ``SHUTDOWN`` message is broadcast or ``stop`` is set."""
        pending: SimpleQueue[object] = SimpleQueue()
        readers_stop = Event()
        readers = [
            Thread(
                target=self._forward,
                args=(module, queues.outbox, pending, readers_stop),
                name=f"dispatcher-{module}",
                daemon=True,
            )
            for module, queues in self._endpoints.items()
        ]
        self._logger.info("Dispatcher starting for %d modules", len(readers))

        for reader in readers:
            reader.start()

        try:
            while stop is None or not stop.is_set():
                try:
                    raw = pending.get(timeout=self._poll_interval)

                except Empty:
                    continue

                message = self.dispatch(raw)

                if message is not None and event_of(message) == EventType.SHUTDOWN:
                    self._logger.info("Dispatcher broadcast shutdown")
                    break

        finally:
            readers_stop.set()

            for reader in readers:
                reader.join()

            self._logger.info("Dispatcher stopped")

    def _forward(
        self,
        module: ModuleName,
        outbox: MessageQueue,
        pending: SimpleQueue[object],
        stop: Event,
    ) -> None:
        """Reader-thread body: move one outbox's messages onto ``pending``."""
        while not stop.is_set():
            try:
                pending.put(outbox.get(timeout=self._poll_interval))

            except Empty:
                continue

            except (EOFError, OSError):
                self._logger.exception("Outbox for %s is no longer readable", module)
                return
