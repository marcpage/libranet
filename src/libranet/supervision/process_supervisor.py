"""Spawning, watching, and restarting a node's module processes.

Every child is started with the ``spawn`` method and handed the validated
config object and its queues directly. The supervisor owns the queues, so
they outlive any one child: messages published while a process is down wait
in its queues until its replacement picks them up.

Restart policy:

* Any child that exits while the node is running is restarted, however
  often it crashes.
* Modules back off exponentially between consecutive crashes, capped at
  ``max_restart_delay``; a module that stayed up for ``stable_after``
  seconds starts again from the base delay.
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
from typing import Sequence

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

DEFAULT_POLL_INTERVAL_SECONDS = 0.2
DEFAULT_RESTART_DELAY_SECONDS = 0.5
DEFAULT_MAX_RESTART_DELAY_SECONDS = 30.0
DEFAULT_STABLE_AFTER_SECONDS = 60.0
DEFAULT_READY_TIMEOUT_SECONDS = 30.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0

_READY_CHECK_INTERVAL_SECONDS = 0.05


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


class ProcessSupervisor:
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
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        restart_delay: float = DEFAULT_RESTART_DELAY_SECONDS,
        max_restart_delay: float = DEFAULT_MAX_RESTART_DELAY_SECONDS,
        stable_after: float = DEFAULT_STABLE_AFTER_SECONDS,
        ready_timeout: float = DEFAULT_READY_TIMEOUT_SECONDS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        names = [spec.name for spec in modules]

        if ModuleName.DISPATCHER in names:
            raise ValueError("The dispatcher is always supervised; do not list it as a module")

        if len(set(names)) != len(names):
            raise ValueError(f"Module names must be unique, got {names}")

        if poll_interval <= 0 or ready_timeout <= 0 or stop_timeout <= 0:
            raise ValueError("poll_interval, ready_timeout, and stop_timeout must be positive")

        if restart_delay < 0 or max_restart_delay < restart_delay:
            raise ValueError("Need 0 <= restart_delay <= max_restart_delay")

        self._config = config
        self._specs = {spec.name: spec for spec in modules}
        self._dispatcher_entry = dispatcher_entry
        self._logger = logger or get_logger(ModuleName.SUPERVISOR)
        self._poll_interval = poll_interval
        self._restart_delay = restart_delay
        self._max_restart_delay = max_restart_delay
        self._stable_after = stable_after
        self._ready_timeout = ready_timeout
        self._stop_timeout = stop_timeout

        self._context = get_context(START_METHOD)
        # Children watch ``_stop``, which only :meth:`shutdown` sets. The node's
        # signal handler sets the signal :meth:`run` is given instead, so a
        # child never sees a signal meant for the supervisor. Until then,
        # ``_stop`` stands in for it; :meth:`_stopping` checks both.
        self._stop = self._context.Event()
        self._stop_requested: StopSignal = self._stop
        self._queues = create_module_queues(names, START_METHOD)
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
                sleep(self._poll_interval)

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

        Modules are stopped before the dispatcher so nothing they publish on
        the way out is stranded.
        """
        self._stop.set()
        running = [
            (child, child.process)
            for child in (*self._modules.values(), self._dispatcher)
            if child.process is not None
        ]
        deadline = monotonic() + self._stop_timeout

        for _, process in running:
            process.join(max(0.0, deadline - monotonic()))

        for child, process in running:
            if process.is_alive():
                self._logger.warning("Module %s did not stop in time; terminating", child.name)
                process.terminate()
                process.join(self._stop_timeout)

            if process.is_alive():
                self._logger.error("Module %s ignored SIGTERM; killing", child.name)
                process.kill()
                process.join(self._stop_timeout)

            self._release(child, process)

        self._logger.info("All module processes stopped")

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
        """Start the dispatcher and wait until it is ready or has failed."""
        ready = self._context.Event()
        process = self._context.Process(
            target=run_dispatcher_process,
            args=(self._dispatcher_entry, self._config, self._queues, self._stop, ready),
            name=f"libranet-{ModuleName.DISPATCHER}",
            daemon=True,
        )
        self._launch(self._dispatcher, process)
        deadline = monotonic() + self._ready_timeout

        while not ready.wait(_READY_CHECK_INTERVAL_SECONDS):
            if self._stopping():
                return False

            if not process.is_alive():
                self._logger.error("Dispatcher exited before becoming ready")
                return False

            if monotonic() >= deadline:
                self._logger.error(
                    "Dispatcher not ready after %.1fs; terminating it", self._ready_timeout
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
            delay = self._backoff(child.failures)
            child.restart_at = now + delay
            self._logger.info("Restarting module %s in %.1fs", child.name, delay)

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
        """Collect a dead child's exit status and count the failure."""
        process = child.process
        assert process is not None
        process.join(self._stop_timeout)
        uptime = now - child.started_at
        child.failures = 1 if uptime >= self._stable_after else child.failures + 1
        self._logger.warning(
            "Module %s exited with status %s after %.1fs (consecutive failures: %d)",
            child.name,
            process.exitcode,
            uptime,
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
        return float(min(self._restart_delay * 2 ** (failures - 1), self._max_restart_delay))
