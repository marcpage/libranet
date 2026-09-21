"""The fixed set of processes a node runs.

Every module area named in the implementation plan appears here exactly
once. The supervisor spawns one process per member of
:data:`SPAWNED_MODULES` (Step 4), each message's envelope carries the name of
the module that published it (Step 3), and each module logs through a logger
named after its member (see :mod:`libranet.logging_setup`).
"""

from __future__ import annotations
from enum import StrEnum


class ModuleName(StrEnum):
    """Identifier for one module process, used for logging and messaging."""

    SUPERVISOR = "supervisor"
    DISPATCHER = "dispatcher"
    WEBSERVER = "webserver"
    CONNECTIONS = "connections"
    VALIDATOR = "validator"
    STATS = "stats"
    FETCHER = "fetcher"
    UNBUNDLER = "unbundler"
    EVICTION = "eviction"
    BACKUP = "backup"


#: Modules the supervisor spawns, in the order it starts them. The dispatcher
#: is first because every other module publishes through it.
SPAWNED_MODULES: tuple[ModuleName, ...] = (
    ModuleName.DISPATCHER,
    ModuleName.STATS,
    ModuleName.WEBSERVER,
    ModuleName.VALIDATOR,
    ModuleName.CONNECTIONS,
    ModuleName.FETCHER,
    ModuleName.UNBUNDLER,
    ModuleName.EVICTION,
    ModuleName.BACKUP,
)
