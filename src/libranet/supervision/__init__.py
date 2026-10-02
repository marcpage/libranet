"""Module process management (Phase 1 Step 4).

Spawns the dispatcher and every module as ``spawn``-method processes,
passes each the validated config object, and restarts whatever exits,
bringing the dispatcher back first.
"""

from libranet.supervision.children import (
    EXIT_CRASHED,
    dispatcher_main,
    run_dispatcher_process,
    run_module_process,
)
from libranet.supervision.process_supervisor import (
    DEFAULT_MAX_RESTART_DELAY_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_READY_TIMEOUT_SECONDS,
    DEFAULT_RESTART_DELAY_SECONDS,
    DEFAULT_STABLE_AFTER_SECONDS,
    DEFAULT_STOP_TIMEOUT_SECONDS,
    ProcessSupervisor,
)
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import DispatcherEntry, ModuleFactory, ModuleSpec, ReadySignal

__all__ = [
    "DEFAULT_MAX_RESTART_DELAY_SECONDS",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "DEFAULT_READY_TIMEOUT_SECONDS",
    "DEFAULT_RESTART_DELAY_SECONDS",
    "DEFAULT_STABLE_AFTER_SECONDS",
    "DEFAULT_STOP_TIMEOUT_SECONDS",
    "EXIT_CRASHED",
    "DispatcherEntry",
    "ModuleFactory",
    "ModuleSpec",
    "ProcessSupervisor",
    "ReadySignal",
    "default_module_specs",
    "dispatcher_main",
    "run_dispatcher_process",
    "run_module_process",
]
