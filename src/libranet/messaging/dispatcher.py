"""The dispatcher: the hub every module's messages pass through.

It reads every module's outbox and puts each valid message into the inbox
of every module that subscribes to its event (Phase 2 Step 61), as the
:class:`~libranet.messaging.queues.ModuleQueues` of each module say, and
``shutdown`` into every inbox. A message is never put into its
publisher's own inbox. Each module filters what it receives all the same
(see :class:`~libranet.messaging.module.ModuleBase`). Messages from one
outbox are delivered in the order they were published; no ordering is
promised between different outboxes.

The dispatcher never waits on a full inbox, since that would hold up every
other module too: a module busy with long work, as the backup module is
with a backup, reads nothing meanwhile. What a full inbox cannot take is
held, in order, and delivered as it has room. The warning that it filled is
logged once, and how much was held once it has all been delivered.

A ``multiprocessing.Queue`` cannot be waited on alongside others, so
:meth:`Dispatcher.run` gives each outbox a reader thread that forwards into
a single local queue, and delivers from that queue on the calling thread.
"""

from __future__ import annotations
from collections import deque
from logging import Logger
from queue import Empty, Full, SimpleQueue
from threading import Event, Thread
from time import monotonic
from typing import Final, Mapping

from libranet.logging_setup import get_logger
from libranet.messaging.envelope import Message, delivered_to, event_of, validate_message
from libranet.messaging.errors import InvalidMessageError
from libranet.messaging.events import EventType
from libranet.messaging.module import StopSignal
from libranet.messaging.queues import MessageQueue, ModuleQueues
from libranet.modules import ModuleName

DEFAULT_DISPATCH_POLL_INTERVAL_SECONDS: Final = 0.1


class _Inbox:
    """One module's inbox, and the messages held for it while the inbox is full."""

    def __init__(self, module: ModuleName, queues: ModuleQueues, logger: Logger) -> None:
        self._module = module
        self._inbox = queues.inbox
        self._subscriptions = queues.subscriptions
        self._logger = logger
        self._held: deque[Message] = deque()
        # Since the inbox filled, while messages are held: how many have
        # been, and when it filled.
        self._held_count = 0
        self._full_since = 0.0

    def wants(self, message: Message) -> bool:
        """Whether ``message`` is delivered to this module, as it subscribes."""
        return delivered_to(message, self._module, self._subscriptions)

    def put(self, message: Message) -> None:
        """Deliver ``message``, or hold it, behind those held already, while the inbox is full."""
        if self._held:
            self._hold(message)
            return

        try:
            self._inbox.put(message, block=False)

        except Full:
            self._full_since = monotonic()
            self._hold(message)
            self._logger.warning(
                "The %s module's inbox is full; holding its messages until it reads more",
                self._module,
            )

    def flush(self) -> None:
        """Deliver the messages held, oldest first, until none is left or the inbox is full."""
        if not self._held:
            return

        while self._held:
            try:
                self._inbox.put(self._held[0], block=False)

            except Full:
                # Not logged: the inbox filling was, and it is still full.
                return

            self._held.popleft()

        self._logger.info(
            "Delivered the %d messages held for the %s module over %.0f seconds",
            self._held_count,
            self._module,
            monotonic() - self._full_since,
        )
        self._held_count = 0

    def _hold(self, message: Message) -> None:
        self._held.append(message)
        self._held_count += 1


class Dispatcher:
    """Delivers every module's published messages to the modules that subscribe to them."""

    def __init__(
        self,
        endpoints: Mapping[ModuleName, ModuleQueues],
        *,
        logger: Logger | None = None,
        poll_interval_seconds: float = DEFAULT_DISPATCH_POLL_INTERVAL_SECONDS,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError(f"poll_interval_seconds must be positive, got {poll_interval_seconds}")

        self._endpoints = dict(endpoints)
        self._logger = logger or get_logger(ModuleName.DISPATCHER)
        self._poll_interval_seconds = poll_interval_seconds
        self._inboxes = [
            _Inbox(module, queues, self._logger) for module, queues in self._endpoints.items()
        ]

    def dispatch(self, raw: object) -> Message | None:
        """Deliver one message to every inbox that takes it; returns it, or ``None`` if invalid.

        Malformed messages are logged and dropped rather than raised, so a
        misbehaving module cannot stop the bus. A full inbox holds up no
        other: what it cannot take is held for it.
        """
        try:
            message = validate_message(raw)

        except InvalidMessageError as error:
            self._logger.warning("Dropping malformed message: %s", error)
            return None

        for inbox in self._inboxes:
            if inbox.wants(message):
                inbox.put(message)

        return message

    def dispatch_pending(self) -> int:
        """Deliver what is held for full inboxes, then what the outboxes hold, without blocking.

        Intended for single-threaded tests; :meth:`run` is the production
        loop.

        Returns:
            The number of valid messages taken from the outboxes.
        """
        self._flush()
        count = 0

        for queues in self._endpoints.values():
            while True:
                try:
                    raw = queues.outbox.get(block=False)

                except Empty:
                    # Not logged: an empty outbox ends the drain.
                    break

                if self.dispatch(raw) is not None:
                    count += 1

        return count

    def run(self, stop: StopSignal | None = None) -> None:
        """Deliver until a ``SHUTDOWN`` message is delivered or ``stop`` is set.

        What is held for a full inbox is delivered, as far as it has room,
        before each message, and at each poll.
        """
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
                self._flush()

                try:
                    raw = pending.get(timeout=self._poll_interval_seconds)

                except Empty:
                    # Not logged: a timeout is how the loop polls.
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

    def _flush(self) -> None:
        """Deliver what is held for each full inbox, as far as it now has room."""
        for inbox in self._inboxes:
            inbox.flush()

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
                pending.put(outbox.get(timeout=self._poll_interval_seconds))

            except Empty:
                # Not logged: a timeout is how the loop polls.
                continue

            except (EOFError, OSError):
                self._logger.exception("Outbox for %s is no longer readable", module)
                return
