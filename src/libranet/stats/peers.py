"""What this node knows of its peers, for the pages that show them (Phase 5 Step 107).

Each derivation writes the peer list, which the web server serves to local
clients as ``GET /data/peers`` (HttpApi §10.8)::

    {"derived": "2026-10-14T12:00:00Z",
     "peers": [{"node_id": "sha256/<hex>", "connected": ["outbound"],
                "stats": {"connection_attempts": 3, ...},
                "addresses": [{"endpoint": "http://203.0.113.9:4300", ...}]}]}

``stats`` and each address are the JSON forms of
:class:`~libranet.stats.records.NodeStats` and
:class:`~libranet.stats.records.NodeAddress`. A peer is any node with
statistics or an address, and any node connected now, but this node itself;
one connected only as it dials in may have neither. Those connected, either
way, come first, then the most recently connected, then the most recently
learned of, and the list ends at ``stats.max_peers_listed``.

Which peers are connected, and which way, the connection manager and the web
server say in ``peers.connected`` whenever that changes (Phase 2 Step 53),
so the list is as current as its derivation, and ``derived`` says when that
was.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Iterable

from libranet.cas.content_id import ContentId
from libranet.json_format import compact_json, moment_text
from libranet.messaging.events import ConnectionDirection
from libranet.stats.database import StatsDatabase
from libranet.stats.records import NodeAddress, NodeStats


class ConnectedPeers:
    """The peers connected to this node now, each way, as ``peers.connected`` last named them."""

    def __init__(self) -> None:
        self._node_ids: dict[ConnectionDirection, frozenset[ContentId]] = {}

    def named(self, direction: ConnectionDirection, node_ids: Iterable[ContentId]) -> None:
        """Take ``node_ids`` as the peers connected ``direction``, in place of those before."""
        self._node_ids[direction] = frozenset(node_ids)

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


@dataclass(frozen=True)
class KnownPeer:
    """One node this node knows of: what is known about it, where, and how it is connected now."""

    stats: NodeStats
    addresses: tuple[NodeAddress, ...] = ()
    connected: tuple[ConnectionDirection, ...] = ()

    @property
    def last_learned(self) -> float | None:
        """When an address of it was last learned, or ``None`` if it has none."""
        return max((address.last_learned for address in self.addresses), default=None)

    def order(self) -> tuple[bool, bool, float, bool, float, str]:
        """Its place in the peer list, as a sort key: the best first."""
        last_connected = self.stats.last_connected
        last_learned = self.last_learned
        return (
            not self.connected,
            last_connected is None,
            -(last_connected or 0.0),
            last_learned is None,
            -(last_learned or 0.0),
            str(self.stats.node_id),
        )

    def value(self) -> dict[str, Any]:
        """The JSON object the peer list gives for it."""
        return {
            "node_id": str(self.stats.node_id),
            "connected": [direction.value for direction in self.connected],
            "stats": self.stats.value(),
            "addresses": [address.value() for address in self.addresses],
        }


@dataclass(frozen=True)
class PeerList:
    """The peers this node knows of, the best first, and when the list was derived."""

    peers: tuple[KnownPeer, ...]
    derived_at: float

    @classmethod
    def read(
        cls,
        database: StatsDatabase,
        connected: ConnectedPeers,
        *,
        exclude: ContentId,
        max_peers: int,
        now: float,
    ) -> PeerList:
        """The ``max_peers`` best of the peers ``database`` knows and ``connected`` names.

        ``exclude`` is this node's own id, which is no peer.
        """
        stats = {node_stats.node_id: node_stats for node_stats in database.all_node_stats()}
        addresses = database.all_node_addresses()
        node_ids = (stats.keys() | addresses.keys() | connected.node_ids()) - {exclude}
        peers = sorted(
            (
                KnownPeer(
                    stats.get(node_id, NodeStats(node_id)),
                    tuple(addresses.get(node_id, ())),
                    connected.directions(node_id),
                )
                for node_id in node_ids
            ),
            key=KnownPeer.order,
        )
        return cls(tuple(peers[:max_peers]), now)

    def body(self) -> bytes:
        """The peer list, as the web server serves it."""
        return compact_json(
            {
                "derived": moment_text(self.derived_at),
                "peers": [peer.value() for peer in self.peers],
            }
        )
