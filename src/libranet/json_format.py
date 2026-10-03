"""Compact JSON, with no space after ``,`` or ``:``.

Response bodies, the node and seek lists, cached search results, the
``/config`` credential file, each backup job's last bundle kept expanded, and
each application's store are written this way, and a byte budget counted
before writing, as for the lists and the stores, assumes it. Files an
administrator may read, such as the application registry, are indented
instead. A bundle is encoded compactly too
(:mod:`libranet.bundle.serialization`), but not with this: its bytes decide
its id, so its encoding can never change, whatever becomes of this.
"""

from __future__ import annotations
from json import dumps
from typing import Any, Final

_SEPARATORS: Final = (",", ":")


def compact_json(value: Any) -> bytes:
    """``value`` as compact JSON text, in UTF-8."""
    return dumps(value, separators=_SEPARATORS).encode("utf-8")
