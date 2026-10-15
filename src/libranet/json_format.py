"""Compact JSON, with no space after ``,`` or ``:``, indented JSON for people, and moments.

Response bodies, the node and seek lists, cached search results, the
``/config`` credential file, each backup job's last bundle kept expanded, and
each application's store are written this way, and a byte budget counted
before writing, as for the lists and the stores, assumes it. Files an
administrator may read, such as the application registry, the backup jobs,
and build records, are indented instead (:func:`pretty_json`). A bundle is
encoded compactly too (:mod:`libranet.bundle.serialization`), but not with
this: its bytes decide its id, so its encoding can never change, whatever
becomes of this.

A moment is written as RFC 3339 text, a UTC time to the second, as an
identity block writes when it was made (HttpApi §11.3), and the peer list
when each peer was last connected (HttpApi §10.8).
"""

from __future__ import annotations
from datetime import UTC, datetime
from json import dumps
from typing import Any, Final

_SEPARATORS: Final = (",", ":")
# How far each level of a file a person may read is indented.
_INDENT: Final = 2

#: How a moment is written in JSON: a UTC time to the second, as RFC 3339
#: §5.6 writes one.
MOMENT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


def compact_json(value: Any, *, sort_keys: bool = False, allow_nan: bool = True) -> bytes:
    """``value`` as compact JSON text, in UTF-8.

    ``sort_keys`` sorts every object's keys, and ``allow_nan`` unset refuses
    NaN and the infinities, which are not JSON, rather than write them.

    Raises:
        ValueError: ``allow_nan`` is unset, and ``value`` holds one of them.
    """
    return dumps(value, separators=_SEPARATORS, sort_keys=sort_keys, allow_nan=allow_nan).encode(
        "utf-8"
    )


def pretty_json(value: Any) -> bytes:
    """``value`` as JSON text indented for a person to read, in UTF-8, ending in a newline."""
    return (dumps(value, indent=_INDENT) + "\n").encode("utf-8")


def moment_text(seconds: float) -> str:
    """The moment ``seconds`` after the epoch, as JSON writes it (:data:`MOMENT_FORMAT`)."""
    return datetime.fromtimestamp(seconds, UTC).strftime(MOMENT_FORMAT)
