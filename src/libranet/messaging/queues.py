"""The queue pair each module uses to talk to the dispatcher.

Modules and the dispatcher depend on :class:`MessageQueue`, not on
``multiprocessing.Queue`` directly, so tests can pass a plain
``queue.Queue`` instead. Both raise :class:`queue.Empty` from a ``get`` that
times out, and :class:`queue.Full` from a ``put`` that cannot add its item
in time.

A ``multiprocessing.Queue`` made without a size is not unbounded: it holds
at most ``SEM_VALUE_MAX`` items not yet read, 32,767 on macOS, and a ``put``
past that waits for a reader. So the dispatcher never waits on a full inbox,
but holds what it cannot take until the module reads more (Phase 2 Step 61).
Nothing tells a publisher to slow down.
"""

from __future__ import annotations
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Final, Iterable, Mapping, Protocol

from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.modules import ModuleName

#: The start method the supervisor uses; queues must come from the same context.
START_METHOD: Final = "spawn"


class MessageQueue(Protocol):
    """The subset of the ``multiprocessing.Queue`` API the bus relies on."""

    def put(self, item: Message, /, block: bool = True, timeout: float | None = None) -> None:
        """Add ``item``, waiting up to ``timeout`` seconds for room if ``block`` is set.

        Raises:
            queue.Full: there is no room, or none came in time.
        """
        ...

    def get(self, block: bool = True, timeout: float | None = None) -> Message:
        """The next message, waiting up to ``timeout`` seconds for one if ``block`` is set.

        Raises:
            queue.Empty: there is none, or none came in time.
        """
        ...


@dataclass(frozen=True)
class ModuleQueues:
    """One module's connection to the dispatcher.

    ``inbox`` carries the messages the dispatcher delivers to the module;
    ``outbox`` carries the module's published messages to the dispatcher.
    ``subscriptions`` are the events delivered to the inbox besides
    ``shutdown``, as the module subscribes to them; ``None`` delivers every
    event. The module's own messages are never delivered to it.
    """

    inbox: MessageQueue
    outbox: MessageQueue
    subscriptions: frozenset[EventType] | None = None


def create_module_queues(
    modules: Iterable[ModuleName],
    start_method: str = START_METHOD,
    subscriptions: Mapping[ModuleName, frozenset[EventType]] | None = None,
) -> dict[ModuleName, ModuleQueues]:
    """A fresh inbox/outbox pair for every module, usable across processes.

    ``subscriptions`` gives the events each module named in it subscribes to;
    a module it does not name has every event delivered.
    """
    context = get_context(start_method)
    return {
        module: ModuleQueues(
            inbox=context.Queue(),
            outbox=context.Queue(),
            subscriptions=None if subscriptions is None else subscriptions.get(module),
        )
        for module in modules
    }
