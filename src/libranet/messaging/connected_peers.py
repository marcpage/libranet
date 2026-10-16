"""Which peers are connected to this node, each way, as ``peers.connected`` names them.

The connection manager names the peers it dialed, and the web server those
that dialed it, whenever either list changes (Phase 2 Step 53). Eviction keeps
their public keys, and the stats module lists them as connected (Phase 5
Step 107). Each list replaces the last for its direction::

    peers.connected  {"direction": "outbound", "node_ids": ["sha256/<hex>", ...]}
"""

from __future__ import annotations
from typing import Any, Iterable, Mapping

from libranet.cas.content_id import ContentId
from libranet.messaging.events import ConnectionDirection


class ConnectedPeers:
    """The peers connected to this node now, each way, as ``peers.connected`` last named them."""

    def __init__(self) -> None:
        self._node_ids: dict[ConnectionDirection, frozenset[ContentId]] = {}

    @staticmethod
    def payload(direction: ConnectionDirection, node_ids: Iterable[ContentId]) -> dict[str, Any]:
        """What ``peers.connected`` says: that ``node_ids``, sorted, are connected ``direction``."""
        return {
            "direction": direction.value,
            "node_ids": sorted(str(node_id) for node_id in node_ids),
        }

    def named(self, direction: ConnectionDirection, node_ids: Iterable[ContentId]) -> None:
        """Take ``node_ids`` as the peers connected ``direction``, in place of those before."""
        self._node_ids[direction] = frozenset(node_ids)

    def named_in(self, message: Mapping[str, Any]) -> None:
        """Take the peers the ``peers.connected`` ``message`` names, in place of those before.

        Raises:
            KeyError: it lacks ``direction`` or ``node_ids``.
            ValueError: it names a direction, or an id, that is none.
        """
        self.named(
            ConnectionDirection(message["direction"]),
            (ContentId.parse(node_id) for node_id in message["node_ids"]),
        )

    def directions(self, node_id: ContentId) -> tuple[ConnectionDirection, ...]:
        """The ways ``node_id`` is connected now, none if it is not."""
        return tuple(
            direction
            for direction in ConnectionDirection
            if node_id in self._node_ids.get(direction, frozenset())
        )

    def node_ids(self) -> frozenset[ContentId]:
        """Every peer connected now, either way."""
        return frozenset().union(*self._node_ids.values())
