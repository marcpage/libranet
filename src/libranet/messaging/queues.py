"""The queue pair each module uses to talk to the dispatcher.

Modules and the dispatcher depend on :class:`MessageQueue`, not on
``multiprocessing.Queue`` directly, so tests can pass a plain
``queue.Queue`` instead. Both raise :class:`queue.Empty` from a ``get`` that
times out. Queues are unbounded for v1 — no backpressure handling yet.
"""

from __future__ import annotations
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Final, Iterable, Protocol

from libranet.messaging.envelope import Message
from libranet.modules import ModuleName

#: The start method the supervisor uses; queues must come from the same context.
START_METHOD: Final = "spawn"


class MessageQueue(Protocol):
    """The subset of the ``multiprocessing.Queue`` API the bus relies on."""

    def put(self, item: Message, /) -> None: ...

    def get(self, block: bool = True, timeout: float | None = None) -> Message: ...


@dataclass(frozen=True)
class ModuleQueues:
    """One module's connection to the dispatcher.

    ``inbox`` carries broadcasts from the dispatcher to the module;
    ``outbox`` carries the module's published messages to the dispatcher.
    """

    inbox: MessageQueue
    outbox: MessageQueue


def create_module_queues(
    modules: Iterable[ModuleName],
    start_method: str = START_METHOD,
) -> dict[ModuleName, ModuleQueues]:
    """A fresh inbox/outbox pair for every module, usable across processes."""
    context = get_context(start_method)
    return {
        module: ModuleQueues(inbox=context.Queue(), outbox=context.Queue()) for module in modules
    }
