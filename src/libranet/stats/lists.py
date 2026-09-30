"""Rendering the plain node and seek lists the web server serves, and the candidate list.

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

The candidate list is this node's own business, never sent anywhere, so it
has no cap (Phase 2 Step 23).
"""

from __future__ import annotations
from json import dumps
from typing import Final, Iterable, Sequence

from libranet.json_format import COMPACT_SEPARATORS

#: `{"nodes":{}}`, the smallest node list.
_NODE_LIST_BASE_BYTES: Final = 12

#: `{"data":[],"search":[]}`, the smallest seek list.
_SEEK_LIST_BASE_BYTES: Final = 23


def _cost(*parts: str) -> int:
    """Bytes one entry adds: its encoded parts plus one byte joining each."""
    return sum(len(dumps(part)) + 1 for part in parts)


def render_node_list(entries: Iterable[tuple[str, str]], max_bytes: int) -> bytes:
    """The ``/data/nodes`` body for ``(endpoint, node id)`` pairs (HttpApi §10.6).

    Pairs are taken in the order given, which is priority order; a repeated
    endpoint keeps the first (better) node id it was offered with.
    """
    nodes: dict[str, str] = {}
    used_bytes = _NODE_LIST_BASE_BYTES

    for endpoint, node_id in entries:
        if endpoint in nodes:
            continue

        cost_bytes = _cost(endpoint, node_id)

        if used_bytes + cost_bytes >= max_bytes:
            break

        nodes[endpoint] = node_id
        used_bytes += cost_bytes

    return dumps({"nodes": nodes}, separators=COMPACT_SEPARATORS).encode("utf-8")


def render_candidate_list(nodes: Iterable[tuple[str, Sequence[str]]]) -> bytes:
    """The candidate list body for ``(node id, endpoints)`` pairs, in the order given.

    Its shape is documented by :mod:`libranet.stats.derivation`.
    """
    return dumps(
        {
            "nodes": [
                {"node_id": node_id, "endpoints": list(endpoints)} for node_id, endpoints in nodes
            ]
        },
        separators=COMPACT_SEPARATORS,
    ).encode("utf-8")


def render_seek_list(data: Sequence[str], searches: Sequence[str], max_bytes: int) -> bytes:
    """The ``/data/seek`` body for content ids and prefixes (HttpApi §10.7.1).

    Neither list may crowd the other out, so each is first offered half of
    the budget; whatever one leaves unused is then available to the other.
    """
    budget_bytes = max_bytes - _SEEK_LIST_BASE_BYTES
    wanted_data, data_used_bytes = _take_while_fits(data, budget_bytes // 2)
    wanted_searches, _ = _take_while_fits(searches, budget_bytes - data_used_bytes)
    return dumps(
        {"data": wanted_data, "search": wanted_searches}, separators=COMPACT_SEPARATORS
    ).encode("utf-8")


def _take_while_fits(values: Iterable[str], budget_bytes: int) -> tuple[list[str], int]:
    """The longest leading run of ``values`` fitting in ``budget_bytes``, and its cost."""
    taken: list[str] = []
    used_bytes = 0

    for value in values:
        cost_bytes = _cost(value)

        if used_bytes + cost_bytes >= budget_bytes:
            break

        taken.append(value)
        used_bytes += cost_bytes

    return taken, used_bytes
