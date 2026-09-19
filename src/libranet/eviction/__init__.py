"""Storage-pressure hand-off and deletion (Phase 1 Step 15).

Reacts to "new data stored" messages, checks free space, and hands off
low-priority content to closer nodes before deleting the local copy.
"""

from libranet.eviction.module import (
    DEFAULT_HAND_OFF_TIMEOUT_SECONDS,
    DEFAULT_MAX_HAND_OFFS,
    HAND_OFF_COPIES,
    EvictionModule,
    eviction_module_factory,
)
from libranet.eviction.pressure import FreeBytes, StoragePressure, free_bytes_under
from libranet.eviction.priority import HeldObject, held_objects, lowest_priority_first

__all__ = [
    "DEFAULT_HAND_OFF_TIMEOUT_SECONDS",
    "DEFAULT_MAX_HAND_OFFS",
    "HAND_OFF_COPIES",
    "EvictionModule",
    "FreeBytes",
    "HeldObject",
    "StoragePressure",
    "eviction_module_factory",
    "free_bytes_under",
    "held_objects",
    "lowest_priority_first",
]
