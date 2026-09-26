"""Storage-pressure hand-off and deletion (Phase 1 Step 15).

Reacts to "new data stored" messages, checks free space, and hands off
low-priority content to closer nodes before deleting the local copy. The
stats module ranks what to let go of first (Phase 2 Step 28).
"""

from libranet.eviction.module import (
    DEFAULT_CANDIDATES_TIMEOUT_SECONDS,
    DEFAULT_HAND_OFF_TIMEOUT_SECONDS,
    DEFAULT_MAX_HAND_OFFS,
    HAND_OFF_COPIES,
    EvictionModule,
    eviction_module_factory,
)
from libranet.eviction.pressure import FreeBytes, StoragePressure, free_bytes_under
from libranet.eviction.priority import FACTOR_FLOOR, EvictionScorer, HeldObject, held_objects

__all__ = [
    "DEFAULT_CANDIDATES_TIMEOUT_SECONDS",
    "DEFAULT_HAND_OFF_TIMEOUT_SECONDS",
    "DEFAULT_MAX_HAND_OFFS",
    "FACTOR_FLOOR",
    "HAND_OFF_COPIES",
    "EvictionModule",
    "EvictionScorer",
    "FreeBytes",
    "HeldObject",
    "StoragePressure",
    "eviction_module_factory",
    "free_bytes_under",
    "held_objects",
]
