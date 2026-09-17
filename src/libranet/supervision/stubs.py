"""Placeholder modules for areas that have no real logic yet.

:class:`StubModule` stands in for every module until its own step replaces
it. The crashing variants exist to demonstrate and test the supervisor's
restart behavior.
"""

from __future__ import annotations
from time import monotonic
from typing import Mapping

from libranet.config.models import LibranetConfig
from libranet.messaging.envelope import Message
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase, StopSignal
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.specs import ReadySignal


class StubModule(ModuleBase):
    """A "hello world" module: it announces itself and subscribes to nothing."""

    def handle(self, message: Message) -> None:
        """Never called: a stub has no subscriptions."""

    def on_start(self) -> None:
        self.logger.info("Hello from stub module %s", self.name)


class CrashingStubModule(StubModule):
    """A stub that raises once it has been running for ``crash_after`` seconds."""

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        *,
        crash_after: float,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(name, queues, poll_interval=poll_interval)
        self._crash_after = crash_after
        self._started_at = monotonic()

    def on_start(self) -> None:
        super().on_start()
        self._started_at = monotonic()

    def on_idle(self) -> None:
        if monotonic() - self._started_at >= self._crash_after:
            raise RuntimeError(f"Stub module {self.name} crashing on purpose")


def stub_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`StubModule`."""
    return StubModule(name, queues)


def crashing_module_factory(
    name: ModuleName,
    config: LibranetConfig,
    queues: ModuleQueues,
    *,
    crash_after: float = 0.0,
    poll_interval: float = 0.05,
) -> ModuleBase:
    """Factory for :class:`CrashingStubModule`; bind options with ``functools.partial``."""
    return CrashingStubModule(name, queues, crash_after=crash_after, poll_interval=poll_interval)


def crashing_dispatcher_main(
    config: LibranetConfig,
    endpoints: Mapping[ModuleName, ModuleQueues],
    stop: StopSignal,
    ready: ReadySignal,
) -> None:
    """A :data:`~libranet.supervision.specs.DispatcherEntry` that dies before it is ready."""
    raise RuntimeError("Stub dispatcher crashing on purpose")
