"""The peers connected to this node's web server (Phase 2 Step 53).

Peers dial this node too, and every request one signs is checked against
its public key, so the eviction module must keep that key while the peer is
connected. A connection counts as a peer's from its first request whose
signature that key verifies until the connection closes. A peer may hold
several connections at once, and it counts until the last of them closes.

The peers connected are named, all of them, whenever one's first connection
opens or its last one closes, and when the web server starts or eviction
asks::

    peers.connected  {"direction": "inbound", "node_ids": ["sha256/<hex>", ...]}

Request threads open and close connections at the same time, so the counts
are guarded by a lock, which is held while each list is published so that
the lists go out in the order the connections changed.

Given :class:`~libranet.webserver.client_keeping.ClientKeeping`, each
request answered on a connection counts for each peer verified on it, and
the connection is closed once any of those peers is to be: one of lower
Karma that adds nothing (Phase 6 Step 142). Each upload a peer makes is
noted, since pushing what this node seeks keeps it.
"""

from __future__ import annotations
from collections import Counter
from threading import Lock

from libranet.cas.content_id import ContentId
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.messaging.publishing import Publish
from libranet.webserver.client_keeping import ClientKeeping


class InboundPeers:
    """How many connections to the web server each peer has open, and whether each is kept.

    With no ``keeping``, every peer is kept.
    """

    def __init__(self, publish: Publish, *, keeping: ClientKeeping | None = None) -> None:
        self._publish = publish
        self._keeping = keeping
        self._connections: Counter[ContentId] = Counter()
        self._lock = Lock()

    def connection(self) -> InboundConnection:
        """A newly opened connection, on which no peer is known yet."""
        return InboundConnection(self)

    def opened(self, node_id: ContentId) -> None:
        """Count one more connection of ``node_id``'s, naming the peers anew if it is its first."""
        with self._lock:
            self._connections[node_id] += 1

            if self._connections[node_id] == 1:
                if self._keeping is not None:
                    self._keeping.connected(node_id)

                self._publish_connected()

    def closed(self, node_id: ContentId) -> None:
        """Count one connection of ``node_id``'s fewer, naming the peers anew if it was its last."""
        with self._lock:
            self._connections[node_id] -= 1

            if self._connections[node_id] <= 0:
                del self._connections[node_id]

                if self._keeping is not None:
                    self._keeping.disconnected(node_id)

                self._publish_connected()

    def served(self, node_id: ContentId) -> bool:
        """Count a request of ``node_id``'s answered; whether its connections are kept open."""
        return self._keeping is None or self._keeping.served(node_id)

    def pushed(self, node_id: ContentId, content_id: ContentId) -> None:
        """Note that ``node_id`` pushed ``content_id``, which keeps it if this node seeks that."""
        if self._keeping is not None:
            self._keeping.pushed(node_id, content_id)

    def publish(self) -> None:
        """Name every peer connected now."""
        with self._lock:
            self._publish_connected()

    def _publish_connected(self) -> None:
        self._publish(
            EventType.PEERS_CONNECTED,
            ConnectedPeers.payload(ConnectionDirection.INBOUND, self._connections),
        )


class InboundConnection:
    """One connection to the web server, and the peers whose signatures were verified on it.

    Only the request thread serving the connection uses it.
    """

    def __init__(self, peers: InboundPeers) -> None:
        self._peers = peers
        self._signers: set[ContentId] = set()

    def verified(self, node_id: ContentId) -> None:
        """Note a request on this connection that ``node_id``'s public key verified."""
        if node_id not in self._signers:
            self._signers.add(node_id)
            self._peers.opened(node_id)

    def served(self) -> bool:
        """Note a request answered on this connection; whether the connection is kept open.

        It is counted for each peer verified on it, and the connection is
        closed once any of them is no longer kept.
        """
        kept = [self._peers.served(node_id) for node_id in self._signers]
        return all(kept)

    def pushed(self, node_id: ContentId, content_id: ContentId) -> None:
        """Note that ``node_id`` pushed ``content_id`` on this connection."""
        self._peers.pushed(node_id, content_id)

    def close(self) -> None:
        """Note that the connection has closed."""
        for node_id in self._signers:
            self._peers.closed(node_id)

        self._signers.clear()
