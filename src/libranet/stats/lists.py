"""Rendering the plain node and seek lists the web server serves.

Both bodies are capped: HttpApi §10.6 and §10.7.1 require each to stay under
1 MiB as transferred, and say that priority decides what survives the cap.
The web server sends these bodies uncompressed, so the whole rendered body
counts. Entries are offered best-first and added while they fit, and the
first one that does not fit ends the list.

Size is tracked as it is built rather than by re-encoding after each entry,
which would be quadratic on a list of thousands. Each entry is charged the
length of its encoded parts plus the punctuation joining them, counting a
separator for the first entry too — an overestimate of one byte per list,
which keeps the rendered body strictly under the budget.
"""

from __future__ import annotations
from json import dumps
from typing import Final, Iterable, Sequence

_SEPARATORS: Final = (",", ":")

#: `{"nodes":{}}`, the smallest node list.
_NODE_LIST_BASE: Final = 12

#: `{"data":[],"search":[]}`, the smallest seek list.
_SEEK_LIST_BASE: Final = 23


def _cost(*parts: str) -> int:
    """Bytes one entry adds: its encoded parts plus one byte joining each."""
    return sum(len(dumps(part)) + 1 for part in parts)


def render_node_list(entries: Iterable[tuple[str, str]], max_bytes: int) -> bytes:
    """The ``/data/nodes`` body for ``(endpoint, node id)`` pairs (HttpApi §10.6).

    Pairs are taken in the order given, which is priority order; a repeated
    endpoint keeps the first (better) node id it was offered with.
    """
    nodes: dict[str, str] = {}
    used = _NODE_LIST_BASE

    for endpoint, node_id in entries:
        if endpoint in nodes:
            continue

        cost = _cost(endpoint, node_id)

        if used + cost >= max_bytes:
            break

        nodes[endpoint] = node_id
        used += cost

    return dumps({"nodes": nodes}, separators=_SEPARATORS).encode("utf-8")


def render_seek_list(data: Sequence[str], searches: Sequence[str], max_bytes: int) -> bytes:
    """The ``/data/seek`` body for content ids and prefixes (HttpApi §10.7.1).

    Neither list may crowd the other out, so each is first offered half of
    the budget; whatever one leaves unused is then available to the other.
    """
    budget = max_bytes - _SEEK_LIST_BASE
    wanted_data, data_used = _take_while_fits(data, budget // 2)
    wanted_searches, _ = _take_while_fits(searches, budget - data_used)
    return dumps({"data": wanted_data, "search": wanted_searches}, separators=_SEPARATORS).encode(
        "utf-8"
    )


def _take_while_fits(values: Iterable[str], budget: int) -> tuple[list[str], int]:
    """The longest leading run of ``values`` fitting in ``budget``, and its cost."""
    taken: list[str] = []
    used = 0

    for value in values:
        cost = _cost(value)

        if used + cost >= budget:
            break

        taken.append(value)
        used += cost

    return taken, used
