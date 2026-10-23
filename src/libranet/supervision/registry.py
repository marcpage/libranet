"""Which factory builds each module a node runs, and which events it subscribes to.

Every module the supervisor spawns, but the dispatcher, has one here.
"""

from __future__ import annotations
from typing import Final, Mapping

from libranet.backup.module import BackupModule
from libranet.connections.module import ConnectionsModule
from libranet.eviction.module import EvictionModule
from libranet.fetcher.module import FetcherModule
from libranet.karma.module import KarmaModule
from libranet.messaging.module import ModuleBase
from libranet.modules import SPAWNED_MODULES, ModuleName
from libranet.stats.module import StatsModule
from libranet.supervision.specs import ModuleFactory, ModuleSpec
from libranet.unbundler.module import UnbundlerModule
from libranet.validator.module import ValidatorModule
from libranet.webserver.module import WebServerModule

# Each module's factory, and the class it builds, whose subscriptions the
# dispatcher delivers to it.
_MODULES: Final[Mapping[ModuleName, tuple[ModuleFactory, type[ModuleBase]]]] = {
    ModuleName.WEBSERVER: (WebServerModule.of, WebServerModule),
    ModuleName.VALIDATOR: (ValidatorModule.of, ValidatorModule),
    ModuleName.STATS: (StatsModule.of, StatsModule),
    ModuleName.CONNECTIONS: (ConnectionsModule.of, ConnectionsModule),
    ModuleName.FETCHER: (FetcherModule.of, FetcherModule),
    ModuleName.UNBUNDLER: (UnbundlerModule.of, UnbundlerModule),
    ModuleName.EVICTION: (EvictionModule.of, EvictionModule),
    ModuleName.BACKUP: (BackupModule.of, BackupModule),
    ModuleName.KARMA: (KarmaModule.of, KarmaModule),
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
