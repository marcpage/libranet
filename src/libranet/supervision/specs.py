"""What the supervisor needs to know to run a module process.

Everything here crosses a ``spawn`` process boundary, so a factory must be
picklable: a module-level function or class, or a ``functools.partial`` of
one. Lambdas and nested functions will not work.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

from libranet.config.models import LibranetConfig
from libranet.messaging.module import ModuleBase, StopSignal
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName


class ReadySignal(Protocol):
    """Anything with ``set()``: how the dispatcher reports that it is up."""

    def set(self) -> None: ...


#: Builds a module inside its own process from the node config and its queues.
ModuleFactory = Callable[[ModuleName, LibranetConfig, ModuleQueues], ModuleBase]

#: Runs the dispatcher inside its own process; must call ``ready.set()`` once up.
DispatcherEntry = Callable[[LibranetConfig, Mapping[ModuleName, ModuleQueues], StopSignal, ReadySignal], None]


@dataclass(frozen=True)
class ModuleSpec:
    """One non-dispatcher module the supervisor runs, and how to build it."""

    name: ModuleName
    factory: ModuleFactory
