"""Which factory builds each module a node runs.

Modules without real logic yet run as stubs; each later step swaps in its
real factory here.
"""

from __future__ import annotations
from typing import Mapping

from libranet.connections.module import connections_module_factory
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.supervision.specs import ModuleFactory, ModuleSpec
from libranet.stats.module import stats_module_factory
from libranet.supervision.stubs import stub_module_factory
from libranet.validator.module import validator_module_factory
from libranet.webserver.module import webserver_module_factory

_FACTORIES: Mapping[ModuleName, ModuleFactory] = {
    ModuleName.WEBSERVER: webserver_module_factory,
    ModuleName.VALIDATOR: validator_module_factory,
    ModuleName.STATS: stats_module_factory,
    ModuleName.CONNECTIONS: connections_module_factory,
}


def default_module_specs() -> tuple[ModuleSpec, ...]:
    """Specs for every spawned module except the dispatcher, in start order."""
    return tuple(
        ModuleSpec(name=module, factory=_FACTORIES.get(module, stub_module_factory))
        for module in SPAWNED_MODULES
        if module != ModuleName.DISPATCHER
    )
