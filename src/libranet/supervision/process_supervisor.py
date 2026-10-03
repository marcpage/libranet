"""Spawning, watching, and restarting a node's module processes.

Every child is started with the ``spawn`` method and handed the validated
config object and its queues directly. The supervisor owns the queues, so
they outlive any one child: messages published while a process is down wait
in its queues, and in the dispatcher once its inbox is full, until its
replacement picks them up. Each module's queues carry the events its spec
says it subscribes to, so the dispatcher delivers it only those.

Restart policy:

* Any child that exits while the node is running is restarted, however
  often it crashes.
* Modules back off exponentially between consecutive crashes, capped at
  ``max_restart_delay_seconds``; a module that stayed up for
  ``stable_after_seconds`` starts again from the base delay.
* The dispatcher is restarted immediately and ahead of everything else.
  Other modules are neither started nor restarted until the dispatcher has
  reported that it is up, since every module depends on it.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import Logger
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from time import monotonic, sleep
from typing import Final, Iterable, Sequence

from libranet.config.models import LibranetConfig
from libranet.logging_setup import get_logger
from libranet.messaging.module import StopSignal
from libranet.messaging.queues import START_METHOD, create_module_queues
from libranet.modules import ModuleName
from libranet.supervision.children import (
    dispatcher_main,
    run_dispatcher_process,
    run_module_process,
)
from libranet.supervision.specs import DispatcherEntry, ModuleSpec

DEFAULT_POLL_INTERVAL_SECONDS: Final = 0.2
DEFAULT_RESTART_DELAY_SECONDS: Final = 0.5
DEFAULT_MAX_RESTART_DELAY_SECONDS: Final = 30.0
DEFAULT_STABLE_AFTER_SECONDS: Final = 60.0
DEFAULT_READY_TIMEOUT_SECONDS: Final = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS: Final = 5.0

_READY_CHECK_INTERVAL_SECONDS: Final = 0.05


@dataclass
class _Child:
    """Bookkeeping for one supervised process slot."""

    name: ModuleName
    process: BaseProcess | None = None
    started_at: float = 0.0
    starts: int = 0
    failures: int = 0
    # When a dead child may next be started; ``None`` with no process means
    # it has never been started and is due now.
    restart_at: float | None = None


class ProcessSupervisor:  # pylint: disable=too-many-instance-attributes
    """Runs the dispatcher and a fixed set of modules, restarting any that exit.

    An instance is single-use: once :meth:`shutdown` has run it cannot be
    started again.
    """

    def __init__(
        self,
        config: LibranetConfig,
        modules: Sequence[ModuleSpec],
        *,
        dispatcher_entry: DispatcherEntry = dispatcher_main,
        logger: Logger | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        restart_delay_seconds: float = DEFAULT_RESTART_DELAY_SECONDS,
        max_restart_delay_seconds: float = DEFAULT_MAX_RESTART_DELAY_SECONDS,
        stable_after_seconds: float = DEFAULT_STABLE_AFTER_SECONDS,
        ready_timeout_seconds: float = DEFAULT_READY_TIMEOUT_SECONDS,
        stop_timeout_seconds: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        names = [spec.name for spec in modules]

        if ModuleName.DISPATCHER in names:
            raise ValueError("The dispatcher is always supervised; do not list it as a module")

        if len(set(names)) != len(names):
            raise ValueError(f"Module names must be unique, got {names}")

        if poll_interval_seconds <= 0 or ready_timeout_seconds <= 0 or stop_timeout_seconds <= 0:
            raise ValueError(
                "poll_interval_seconds, ready_timeout_seconds, and stop_timeout_seconds "
                "must be positive"
            )

        if restart_delay_seconds < 0 or max_restart_delay_seconds < restart_delay_seconds:
            raise ValueError("Need 0 <= restart_delay_seconds <= max_restart_delay_seconds")

        self._config = config
        self._specs = {spec.name: spec for spec in modules}
        self._dispatcher_entry = dispatcher_entry
        self._logger = logger or get_logger(ModuleName.SUPERVISOR)
        self._poll_interval_seconds = poll_interval_seconds
        self._restart_delay_seconds = restart_delay_seconds
        self._max_restart_delay_seconds = max_restart_delay_seconds
        self._stable_after_seconds = stable_after_seconds
        self._ready_timeout_seconds = ready_timeout_seconds
        self._stop_timeout_seconds = stop_timeout_seconds

        self._context = get_context(START_METHOD)
        # Children watch ``_stop``, which only :meth:`shutdown` sets. The node's
        # signal handler sets the signal :meth:`run` is given instead, so a
        # child never sees a signal meant for the supervisor. Until then,
        # ``_stop`` stands in for it; :meth:`_stopping` checks both.
        self._stop = self._context.Event()
        # The dispatcher has its own, set once every module has exited.
        self._dispatcher_stop = self._context.Event()
        self._stop_requested: StopSignal = self._stop
        self._queues = create_module_queues(
            names,
            START_METHOD,
            {spec.name: spec.subscriptions for spec in modules if spec.subscriptions is not None},
        )
        self._dispatcher = _Child(ModuleName.DISPATCHER)
        self._modules = {name: _Child(name) for name in names}
        self._start_log: list[ModuleName] = []

    @property
    def start_log(self) -> tuple[ModuleName, ...]:
        """Every process start so far, in order, including restarts."""
        return tuple(self._start_log)

    def restart_count(self, name: ModuleName) -> int:
        """How many times ``name`` has been started after its first start."""
        return max(0, self._child(name).starts - 1)

    def process_id(self, name: ModuleName) -> int | None:
        """The pid of the running process for ``name``, if there is one."""
        process = self._child(name).process
        return process.pid if process is not None and process.is_alive() else None

    def run(self, stop: StopSignal) -> None:
        """Start everything and keep it running until ``stop`` is set, then shut down."""
        if stop.is_set():
            return

        self._stop_requested = stop
        self._logger.info("Supervising the dispatcher and %d modules", len(self._modules))

        try:
            while not self._stopping():
                self.poll()
                sleep(self._poll_interval_seconds)

        finally:
            self.shutdown()

    def poll(self) -> None:
        """One supervision pass: start anything due, and notice anything that died.

        The first call starts every process. Blocks while waiting for a
        (re)started dispatcher to come up, until it does, fails, or the node
        is asked to stop.
        """
        if self._stopping():
            return

        if not self._ensure_dispatcher():
            return

        now = monotonic()

        for child in self._modules.values():
            self._check_module(child, now)

    def shutdown(self) -> None:
        """Stop every child: ask nicely, then ``SIGTERM``, then ``SIGKILL``.

        Modules are stopped before the dispatcher, which reads their outboxes
        until they have exited. A module cannot exit until whatever it
        published on the way out has been read.
        """
        self._stop.set()
        self._stop_children(self._modules.values())
        self._dispatcher_stop.set()
        self._stop_children([self._dispatcher])
        self._logger.info("All module processes stopped")

    def _stop_children(self, children: Iterable[_Child]) -> None:
        """Wait for ``children``, asked to stop, escalating to ``SIGTERM`` and ``SIGKILL``."""
        running = [(child, child.process) for child in children if child.process is not None]
        deadline = monotonic() + self._stop_timeout_seconds

        for _, process in running:
            process.join(max(0.0, deadline - monotonic()))

        for child, process in running:
            if process.is_alive():
                self._logger.warning("Module %s did not stop in time; terminating", child.name)
                process.terminate()
                process.join(self._stop_timeout_seconds)

            if process.is_alive():
                self._logger.error("Module %s ignored SIGTERM; killing", child.name)
                process.kill()
                process.join(self._stop_timeout_seconds)

            self._release(child, process)

    def _stopping(self) -> bool:
        """Whether the node was asked to stop, or has been shut down."""
        return self._stop.is_set() or self._stop_requested.is_set()

    def _child(self, name: ModuleName) -> _Child:
        if name == ModuleName.DISPATCHER:
            return self._dispatcher

        return self._modules[name]

    def _ensure_dispatcher(self) -> bool:
        """Whether the dispatcher is up, (re)starting it first if it is not."""
        child = self._dispatcher

        if child.process is not None:
            if child.process.is_alive():
                return True

            self._reap(child, monotonic())

        return self._start_dispatcher()

    def _start_dispatcher(self) -> bool:
        """Start the dispatcher and wait until it is ready or has failed.

        Returns:
            Whether it became ready; ``False`` if it exited or ran out of time
            first, or the supervisor is stopping.
        """
        ready = self._context.Event()
        process = self._context.Process(
            target=run_dispatcher_process,
            args=(
                self._dispatcher_entry,
                self._config,
                self._queues,
                self._dispatcher_stop,
                ready,
            ),
            name=f"libranet-{ModuleName.DISPATCHER}",
            daemon=True,
        )
        self._launch(self._dispatcher, process)
        deadline = monotonic() + self._ready_timeout_seconds

        while not ready.wait(_READY_CHECK_INTERVAL_SECONDS):
            if self._stopping():
                return False

            if not process.is_alive():
                self._logger.error("Dispatcher exited before becoming ready")
                return False

            if monotonic() >= deadline:
                self._logger.error(
                    "Dispatcher not ready after %.1fs; terminating it", self._ready_timeout_seconds
                )
                process.terminate()
                return False

        return True

    def _check_module(self, child: _Child, now: float) -> None:
        """Reap ``child`` if it died, and start it if it is due."""
        if child.process is not None:
            if child.process.is_alive():
                return

            self._reap(child, now)
            delay_seconds = self._backoff(child.failures)
            child.restart_at = now + delay_seconds
            self._logger.info("Restarting module %s in %.1fs", child.name, delay_seconds)

        if child.restart_at is None or now >= child.restart_at:
            spec = self._specs[child.name]
            process = self._context.Process(
                target=run_module_process,
                args=(spec.name, spec.factory, self._config, self._queues[spec.name], self._stop),
                name=f"libranet-{spec.name}",
                daemon=True,
            )
            self._launch(child, process)

    def _launch(self, child: _Child, process: BaseProcess) -> None:
        process.start()
        child.process = process
        child.started_at = monotonic()
        child.starts += 1
        child.restart_at = None
        self._start_log.append(child.name)

        if child.starts == 1:
            self._logger.info("Started module %s (pid %s)", child.name, process.pid)

        else:
            self._logger.info(
                "Restarted module %s (pid %s, restart %d)",
                child.name,
                process.pid,
                child.starts - 1,
            )

    def _reap(self, child: _Child, now: float) -> None:
        """Collect a dead child's exit status and count the failure.

        Raises:
            RuntimeError: ``child`` was never started, so has no process.
        """
        process = child.process

        if process is None:
            raise RuntimeError(f"Module {child.name} has no process to reap")

        process.join(self._stop_timeout_seconds)
        uptime_seconds = now - child.started_at
        child.failures = 1 if uptime_seconds >= self._stable_after_seconds else child.failures + 1
        self._logger.warning(
            "Module %s exited with status %s after %.1fs (consecutive failures: %d)",
            child.name,
            process.exitcode,
            uptime_seconds,
            child.failures,
        )
        self._release(child, process)

    def _release(self, child: _Child, process: BaseProcess) -> None:
        """Empty ``child``'s slot, closing ``process`` unless it has not exited.

        A process still running after every wait for it is left rather than
        waited on further, so that it cannot hold up the supervisor.
        """
        if process.is_alive():
            self._logger.error(
                "Module %s (pid %s) did not exit; leaving it", child.name, process.pid
            )

        else:
            process.close()

        child.process = None

    def _backoff(self, failures: int) -> float:
        """Delay before restarting a module that has failed ``failures`` times in a row."""
        return float(
            min(self._restart_delay_seconds * 2 ** (failures - 1), self._max_restart_delay_seconds)
        )
