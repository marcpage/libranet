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
from libranet.supervision.process_supervisor import ProcessSupervisor
from libranet.supervision.registry import default_module_specs
from libranet.supervision.specs import DispatcherEntry, ModuleFactory, ModuleSpec, ReadySignal
from libranet.supervision.stubs import (
    CrashingStubModule,
    StubModule,
    crashing_dispatcher_main,
    crashing_module_factory,
    stub_module_factory,
)

__all__ = [
    "EXIT_CRASHED",
    "CrashingStubModule",
    "DispatcherEntry",
    "ModuleFactory",
    "ModuleSpec",
    "ProcessSupervisor",
    "ReadySignal",
    "StubModule",
    "crashing_dispatcher_main",
    "crashing_module_factory",
    "default_module_specs",
    "dispatcher_main",
    "run_dispatcher_process",
    "run_module_process",
    "stub_module_factory",
]
