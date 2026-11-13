"""Storage-pressure hand-off and deletion (Phase 1 Step 15).

Reacts to "new data stored" messages, checks free space, and hands off
low-priority content to closer nodes before deleting the local copy. The
stats module ranks what to let go of first (Phase 2 Step 28).
"""

from libranet.eviction.module import HAND_OFF_COPIES, EvictionModule
from libranet.eviction.pressure import FreeBytes, StoragePressure, free_bytes_under

__all__ = [
    "HAND_OFF_COPIES",
    "EvictionModule",
    "FreeBytes",
    "StoragePressure",
    "free_bytes_under",
]
