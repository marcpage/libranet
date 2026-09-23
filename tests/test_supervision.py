"""Tests for spawning and restarting module processes.

These start real ``spawn``-method processes running stub modules, so they
are slower than the rest of the suite.
"""

from __future__ import annotations
from functools import partial
from logging import getLogger
from multiprocessing import get_context
from multiprocessing.process import BaseProcess
from os import kill
from pathlib import Path
from queue import Queue
from signal import SIGTERM
from threading import Event, Timer
from time import monotonic, sleep
from typing import Any, Callable, Iterator

from pytest import LogCaptureFixture, MonkeyPatch, fixture, raises

from libranet.config.models import LibranetConfig
from libranet.messaging.queues import START_METHOD, ModuleQueues
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.process_supervisor import ProcessSupervisor
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import ModuleSpec
from libranet.supervision.stubs import (
    CrashingStubModule,
    crashing_dispatcher_main,
    crashing_module_factory,
    stub_module_factory,
    unready_dispatcher_main,
)

TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 0.5

STUBS = (
    ModuleSpec(ModuleName.WEBSERVER, stub_module_factory),
    ModuleSpec(ModuleName.VALIDATOR, stub_module_factory),
)


@fixture
def config(tmp_path: Path) -> LibranetConfig:
    return LibranetConfig.model_validate(
        {
            "storage": {"data_dir": tmp_path / "data", "cache_dir": tmp_path / "cache"},
            "logging": {"directory": tmp_path / "logs", "console": False},
        }
    )


@fixture
def make_supervisor(config: LibranetConfig) -> Iterator[Callable[..., ProcessSupervisor]]:
    created: list[ProcessSupervisor] = []

    def make(modules: tuple[ModuleSpec, ...] = STUBS, **options: Any) -> ProcessSupervisor:
        options.setdefault("poll_interval", 0.05)
        options.setdefault("restart_delay", 0.0)
        supervisor = ProcessSupervisor(config, modules, **options)
        created.append(supervisor)
        return supervisor

    yield make

    for supervisor in created:
        supervisor.shutdown()


def _poll_until(supervisor: ProcessSupervisor, condition: Callable[[], bool]) -> None:
    deadline = monotonic() + TIMEOUT_SECONDS

    while not condition():
        assert monotonic() < deadline, "timed out waiting for the supervisor"
        supervisor.poll()
        sleep(0.05)


def _wait_until(condition: Callable[[], bool]) -> None:
    deadline = monotonic() + TIMEOUT_SECONDS

    while not condition():
        assert monotonic() < deadline, "timed out waiting for the condition"
        sleep(0.05)


def _has_started(log_dir: Path, name: ModuleName) -> Callable[[], bool]:
    log = log_dir / f"libranet-{name}.log"
    marker = "Dispatcher starting" if name == ModuleName.DISPATCHER else "Hello from stub module"
    return lambda: log.is_file() and marker in log.read_text()


def _running(supervisor: ProcessSupervisor, *names: ModuleName) -> bool:
    return all(supervisor.process_id(name) is not None for name in names)


class _StubbornProcess:
    """A child that nothing, not even ``SIGKILL``, makes exit."""

    pid: int | None = None
    exitcode: int | None = None

    def __init__(self) -> None:
        self.started = Event()
        self.started_at = 0.0

    def start(self) -> None:
        self.started_at = monotonic()
        self.started.set()

    def is_alive(self) -> bool:
        return True

    def join(self, timeout: float | None = None) -> None:
        assert timeout is not None, "waited with no timeout for a child that will never exit"
        sleep(timeout)

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def close(self) -> None:
        raise ValueError("Cannot close a process while it is still running")


class _StubbornContext:
    """The ``spawn`` context, except that one module's process never exits."""

    def __init__(self, name: ModuleName, stubborn: _StubbornProcess) -> None:
        self._real = get_context(START_METHOD)
        self._name = f"libranet-{name}"
        self._stubborn = stubborn

    def __getattr__(self, attribute: str) -> object:
        return getattr(self._real, attribute)

    # Named as on a multiprocessing context, which the supervisor calls.
    def Process(
        self, *, target: Callable[..., object], args: tuple[object, ...], name: str, daemon: bool
    ) -> BaseProcess | _StubbornProcess:
        if name == self._name:
            return self._stubborn

        return self._real.Process(target=target, args=args, name=name, daemon=daemon)


def test_default_specs_cover_every_module_but_the_dispatcher() -> None:
    names = tuple(spec.name for spec in default_module_specs())

    assert names == tuple(module for module in SPAWNED_MODULES if module != ModuleName.DISPATCHER)


def test_dispatcher_may_not_be_listed_as_a_module(config: LibranetConfig) -> None:
    with raises(ValueError, match="dispatcher"):
        ProcessSupervisor(config, (ModuleSpec(ModuleName.DISPATCHER, stub_module_factory),))


def test_module_names_must_be_unique(config: LibranetConfig) -> None:
    with raises(ValueError, match="unique"):
        ProcessSupervisor(config, (STUBS[0], STUBS[0]))


def test_restart_delays_must_be_ordered(config: LibranetConfig) -> None:
    with raises(ValueError, match="restart_delay"):
        ProcessSupervisor(config, STUBS, restart_delay=5.0, max_restart_delay=1.0)


def test_crashing_stub_raises_once_its_time_is_up() -> None:
    module = CrashingStubModule(
        ModuleName.WEBSERVER,
        ModuleQueues(inbox=Queue(), outbox=Queue()),
        crash_after=0.0,
        poll_interval=0.01,
    )

    with raises(RuntimeError, match="on purpose"):
        module.run(Event())


def test_dispatcher_starts_before_every_module(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    supervisor = make_supervisor()

    supervisor.poll()

    assert supervisor.start_log == (
        ModuleName.DISPATCHER,
        ModuleName.WEBSERVER,
        ModuleName.VALIDATOR,
    )
    assert _running(supervisor, ModuleName.DISPATCHER, ModuleName.WEBSERVER, ModuleName.VALIDATOR)


def test_each_module_logs_to_its_own_file(
    make_supervisor: Callable[..., ProcessSupervisor], tmp_path: Path
) -> None:
    supervisor = make_supervisor()
    log = tmp_path / "logs" / "libranet-webserver.log"

    _poll_until(supervisor, lambda: log.is_file() and "Hello from stub module" in log.read_text())


def test_crash_looping_module_keeps_being_restarted(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    crasher = ModuleSpec(ModuleName.FETCHER, partial(crashing_module_factory, crash_after=0.0))
    supervisor = make_supervisor((STUBS[0], crasher))

    _poll_until(supervisor, lambda: supervisor.restart_count(ModuleName.FETCHER) >= 3)

    assert supervisor.restart_count(ModuleName.DISPATCHER) == 0
    assert supervisor.restart_count(ModuleName.WEBSERVER) == 0


def test_restart_waits_for_the_backoff_delay(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    crasher = ModuleSpec(ModuleName.FETCHER, partial(crashing_module_factory, crash_after=0.0))
    supervisor = make_supervisor((crasher,), restart_delay=60.0, max_restart_delay=60.0)
    supervisor.poll()
    deadline = monotonic() + 3.0

    while monotonic() < deadline:
        supervisor.poll()
        sleep(0.05)

    assert supervisor.process_id(ModuleName.FETCHER) is None
    assert supervisor.restart_count(ModuleName.FETCHER) == 0


def test_terminated_module_is_restarted(
    make_supervisor: Callable[..., ProcessSupervisor], tmp_path: Path
) -> None:
    supervisor = make_supervisor()
    supervisor.poll()
    _wait_until(_has_started(tmp_path / "logs", ModuleName.WEBSERVER))
    pid = supervisor.process_id(ModuleName.WEBSERVER)
    assert pid is not None

    kill(pid, SIGTERM)

    _poll_until(supervisor, lambda: supervisor.restart_count(ModuleName.WEBSERVER) == 1)
    assert supervisor.process_id(ModuleName.WEBSERVER) not in (None, pid)


def test_dispatcher_is_restarted_before_other_modules(
    make_supervisor: Callable[..., ProcessSupervisor], tmp_path: Path
) -> None:
    supervisor = make_supervisor()
    supervisor.poll()
    killed = (ModuleName.WEBSERVER, ModuleName.DISPATCHER)

    for name in killed:
        _wait_until(_has_started(tmp_path / "logs", name))

    for name in killed:
        pid = supervisor.process_id(name)
        assert pid is not None
        kill(pid, SIGTERM)

    _wait_until(lambda: all(supervisor.process_id(name) is None for name in killed))

    _poll_until(supervisor, lambda: supervisor.restart_count(ModuleName.WEBSERVER) == 1)

    restarts = supervisor.start_log[3:]
    assert restarts.index(ModuleName.DISPATCHER) < restarts.index(ModuleName.WEBSERVER)
    assert _running(supervisor, ModuleName.DISPATCHER, ModuleName.WEBSERVER, ModuleName.VALIDATOR)


def test_modules_wait_while_the_dispatcher_cannot_start(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    supervisor = make_supervisor(dispatcher_entry=crashing_dispatcher_main)

    _poll_until(supervisor, lambda: supervisor.restart_count(ModuleName.DISPATCHER) >= 2)

    assert set(supervisor.start_log) == {ModuleName.DISPATCHER}


def test_shutdown_stops_every_process(make_supervisor: Callable[..., ProcessSupervisor]) -> None:
    supervisor = make_supervisor()
    supervisor.poll()

    supervisor.shutdown()

    assert not _running(supervisor, ModuleName.DISPATCHER)
    assert not _running(supervisor, ModuleName.WEBSERVER)
    assert not _running(supervisor, ModuleName.VALIDATOR)
    supervisor.poll()
    assert supervisor.process_id(ModuleName.WEBSERVER) is None


def test_run_returns_at_once_when_already_stopped(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    supervisor = make_supervisor()
    stop = Event()
    stop.set()

    supervisor.run(stop)

    assert supervisor.start_log == ()


def test_readiness_wait_ends_once_the_node_is_asked_to_stop(
    make_supervisor: Callable[..., ProcessSupervisor],
) -> None:
    supervisor = make_supervisor(
        dispatcher_entry=unready_dispatcher_main, ready_timeout=TIMEOUT_SECONDS
    )
    stop = Event()
    Timer(0.5, stop.set).start()
    started = monotonic()

    supervisor.run(stop)

    # Long before the dispatcher's readiness deadline.
    assert monotonic() - started < TIMEOUT_SECONDS / 4
    assert supervisor.start_log == (ModuleName.DISPATCHER,)


def test_run_returns_although_a_child_will_not_exit(
    make_supervisor: Callable[..., ProcessSupervisor],
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    supervisor = make_supervisor(stop_timeout=STOP_TIMEOUT_SECONDS, logger=getLogger(__name__))
    stubborn = _StubbornProcess()
    monkeypatch.setattr(supervisor, "_context", _StubbornContext(ModuleName.WEBSERVER, stubborn))

    # The node is asked to stop as soon as the child that will not exit starts.
    supervisor.run(stubborn.started)

    # A stop timeout each to let it stop, to terminate it, and to kill it.
    assert monotonic() - stubborn.started_at < 3 * STOP_TIMEOUT_SECONDS + 1.0
    assert "did not exit; leaving it" in caplog.text
