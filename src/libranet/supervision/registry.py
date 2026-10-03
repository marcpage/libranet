"""Which factory builds each module a node runs, and which events it subscribes to.

Every module the supervisor spawns, but the dispatcher, has one here.
"""

from __future__ import annotations
from typing import Final, Mapping

from libranet.backup.module import BackupModule, backup_module_factory
from libranet.connections.module import ConnectionsModule, connections_module_factory
from libranet.eviction.module import EvictionModule, eviction_module_factory
from libranet.fetcher.module import FetcherModule, fetcher_module_factory
from libranet.messaging.module import ModuleBase
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.stats.module import StatsModule, stats_module_factory
from libranet.supervision.specs import ModuleFactory, ModuleSpec
from libranet.unbundler.module import UnbundlerModule, unbundler_module_factory
from libranet.validator.module import ValidatorModule, validator_module_factory
from libranet.webserver.module import WebServerModule, webserver_module_factory

# Each module's factory, and the class it builds, whose subscriptions the
# dispatcher delivers to it.
_MODULES: Final[Mapping[ModuleName, tuple[ModuleFactory, type[ModuleBase]]]] = {
    ModuleName.WEBSERVER: (webserver_module_factory, WebServerModule),
    ModuleName.VALIDATOR: (validator_module_factory, ValidatorModule),
    ModuleName.STATS: (stats_module_factory, StatsModule),
    ModuleName.CONNECTIONS: (connections_module_factory, ConnectionsModule),
    ModuleName.FETCHER: (fetcher_module_factory, FetcherModule),
    ModuleName.UNBUNDLER: (unbundler_module_factory, UnbundlerModule),
    ModuleName.EVICTION: (eviction_module_factory, EvictionModule),
    ModuleName.BACKUP: (backup_module_factory, BackupModule),
}


def default_module_specs() -> tuple[ModuleSpec, ...]:
    """Specs for every spawned module except the dispatcher, in start order."""
    specs: list[ModuleSpec] = []

    for module in SPAWNED_MODULES:
        if module == ModuleName.DISPATCHER:
            continue

        factory, module_class = _MODULES[module]
        specs.append(ModuleSpec(module, factory, module_class.subscriptions))

    return tuple(specs)
