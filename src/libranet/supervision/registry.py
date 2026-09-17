"""Which factory builds each module a node runs.

Every module is a stub for now; each later step swaps in its real factory
here.
"""

from __future__ import annotations

from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.specs import ModuleSpec
from libranet.supervision.stubs import stub_module_factory


def default_module_specs() -> tuple[ModuleSpec, ...]:
    """Specs for every spawned module except the dispatcher, in start order."""
    return tuple(
        ModuleSpec(name=module, factory=stub_module_factory)
        for module in SPAWNED_MODULES
        if module != ModuleName.DISPATCHER
    )
