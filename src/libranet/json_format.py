"""Compact JSON, with no space after ``,`` or ``:``.

Response bodies, the node and seek lists, cached search results, the
``/config`` credential file, and each backup job's last bundle kept expanded
are written this way, and a byte budget counted before writing, as for the
lists, assumes it. Files an administrator may
read, such as the application registry, are indented instead. A bundle is
encoded compactly too (:mod:`libranet.bundle.serialization`), but not with
this: its bytes decide its id, so its encoding can never change, whatever
becomes of this.
"""

from __future__ import annotations
from typing import Final

COMPACT_SEPARATORS: Final = (",", ":")
