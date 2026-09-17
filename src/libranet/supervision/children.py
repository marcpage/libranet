"""Entry points that run inside each spawned module process.

Each child sets up its own logging (see :mod:`libranet.logging_setup`) from
the config object it was spawned with, so no child re-reads the YAML file.

Children leave ``SIGINT`` to the supervisor, which stops them through the
shared stop signal, and turn ``SIGTERM`` into :class:`SystemExit`. Unwinding
matters: a process killed while blocked in ``multiprocessing.Queue.get``
without unwinding never releases that queue's reader lock, and the module's
replacement could then never read from it. ``SIGKILL`` cannot be caught, so
the supervisor only resorts to it when a child ignores ``SIGTERM``.
"""

from __future__ import annotations
from logging import Logger
from signal import SIG_IGN, SIGINT, SIGTERM, signal
from types import FrameType
from typing import Callable, Mapping

from libranet.config.models import LibranetConfig
from libranet.logging_setup import configure_logging
from libranet.messaging.dispatcher import Dispatcher
from libranet.messaging.module import StopSignal
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.supervision.specs import DispatcherEntry, ModuleFactory, ReadySignal

#: Exit status of a child whose module raised an unhandled exception.
EXIT_CRASHED = 1


def run_dispatcher_process(
    entry: DispatcherEntry,
    config: LibranetConfig,
    endpoints: Mapping[ModuleName, ModuleQueues],
    stop: StopSignal,
    ready: ReadySignal,
) -> None:
    """Process target for the dispatcher."""
    logger = _prepare_process(config, ModuleName.DISPATCHER)
    _run_logged(logger, ModuleName.DISPATCHER, lambda: entry(config, endpoints, stop, ready))


def dispatcher_main(
    config: LibranetConfig,
    endpoints: Mapping[ModuleName, ModuleQueues],
    stop: StopSignal,
    ready: ReadySignal,
) -> None:
    """The production :data:`DispatcherEntry`: broadcast until stopped."""
    dispatcher = Dispatcher(endpoints)
    ready.set()
    dispatcher.run(stop)


def run_module_process(
    name: ModuleName,
    factory: ModuleFactory,
    config: LibranetConfig,
    queues: ModuleQueues,
    stop: StopSignal,
) -> None:
    """Process target for every module other than the dispatcher."""
    logger = _prepare_process(config, name)
    _run_logged(logger, name, lambda: factory(name, config, queues).run(stop))


def _prepare_process(config: LibranetConfig, name: ModuleName) -> Logger:
    """Install this child's signal handling and logging."""
    signal(SIGINT, SIG_IGN)
    signal(SIGTERM, _exit_on_sigterm)
    return configure_logging(config.logging, name)


def _exit_on_sigterm(signum: int, frame: FrameType | None) -> None:
    """Unwind the stack, releasing any queue locks, instead of dying in place."""
    raise SystemExit(128 + signum)


def _run_logged(logger: Logger, name: ModuleName, body: Callable[[], None]) -> None:
    """Run ``body``, logging an unhandled exception and exiting non-zero."""
    try:
        body()

    except Exception:
        logger.exception("Module %s crashed", name)
        raise SystemExit(EXIT_CRASHED) from None
