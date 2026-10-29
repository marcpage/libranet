"""Deriving the plain ``/data/nodes`` and ``/data/seek`` files, and the other lists stats writes.

The web server serves the first two as static files (Step 9), so the stats
module rewrites them periodically rather than answering a request per read.
The node list is what this node publishes; the candidate list is what the
connection manager dials from (Phase 2 Step 23). A file is replaced only
when its contents actually change, which keeps its modification time
meaningful and saves the candidate list's reader from reacting to a rewrite
that says nothing new.

The candidate list names every node this node knows an address for, but
itself and those it has given up on for now (Phase 2 Step 26), with every
address it knows, in the order to try them. Nodes that have been reached
come first, then the rest. Within each part, those holding as much Karma as
this node or more come first, those holding least of them first, then those
holding less, those holding most of them first (Phase 6 Step 143), so that
this node dials those likeliest to keep it connected (HandshakeProtocol
§5.2). Then come the most recently reached first, or, of those never
reached, the most recently learned of first. Each node's addresses are in
the order the database tries them in::

    {"nodes": [{"node_id": "sha256/<hex>", "endpoints": ["http://203.0.113.9:4300", ...]}]}

The node list puts first the nodes whose Karma is nearest this node's own,
so that what its cap leaves out is what holds least like it (HttpApi
§10.6), and a peer is told most of the nodes near its own Karma, as it
likely is. A node's Karma is its balance as the chain this node follows
states it, which the Karma module writes to ``balances.json``, read afresh
at each derivation; a node the chain does not name holds none, as every
node does while this one follows no chain. Karma only orders the lists: no
node is left out of either for it.

The shunned list names the content this node will not hold, for the modules
that refuse it (Phase 4 Step 30), in the shape
:class:`~libranet.cas.shunned.ShunnedContent` reads.

The peer list is what this node knows of each peer, for its local clients'
pages (Phase 5 Step 107), in the shape :mod:`libranet.stats.peers` documents.
It says when it was derived, so it is written afresh each time.
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger
from pathlib import Path
from time import time
from typing import Callable, Mapping, Sequence

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.config.models import StatsConfig, StorageConfig
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.protocol.karma_balances import KarmaBalances
from libranet.stats.database import StatsDatabase
from libranet.stats.lists import render_candidate_list, render_node_list, render_seek_list
from libranet.stats.peers import PeerList
from libranet.stats.schema import SeekKind

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class DerivedLists:
    """The five files a derivation maintains, and whether the node and candidate lists changed.

    Only those two carry a changed flag, because only they are announced: a
    new candidate list is the connection manager's cue to reconsider its peer
    mix (Step 11), and a new node list its cue to pass on the nodes it names
    anew (Phase 5 Step 114). Nothing is published about the others — a peer
    reads them from the file when it asks, and a module refusing shunned
    content when it looks — so whether they were rewritten is this module's
    business alone.
    """

    node_list: Path
    seek_list: Path
    candidate_list: Path
    shunned_list: Path
    peer_list: Path
    node_list_changed: bool
    candidate_list_changed: bool


@dataclass(frozen=True)
class _HeldKismet:
    """The Kismet each node holds, by node id, and this node's own (Phase 6 Step 143)."""

    held: Mapping[str, int]
    own: int = 0

    def distance(self, node_id: str) -> int:
        """How far the Kismet ``node_id`` holds is from this node's own, either way."""
        return abs(self.held.get(node_id, 0) - self.own)

    def climbing(self, node_id: str) -> tuple[bool, int]:
        """Where ``node_id`` falls, climbing from this node's own Kismet and then descending.

        Those holding as much as this node or more sort first, those holding
        least of them first, then those holding less, the nearest first.
        """
        return self.held.get(node_id, 0) < self.own, self.distance(node_id)


class ListDeriver:
    """Writes the node list and seek list this node publishes, and its candidate list.

    ``endpoints`` and ``node_id`` describe this node itself: a node list must
    always name its sender (HttpApi §10.1), and it heads the list because no
    peer's address is better known to us than our own. ``connected`` names
    the peers connected now, for the peer list; none, if not given.
    """

    def __init__(
        self,
        database: StatsDatabase,
        storage: StorageConfig,
        stats: StatsConfig,
        endpoints: Sequence[str],
        node_id: ContentId,
        *,
        connected: ConnectedPeers | None = None,
        clock: Callable[[], float] = time,
    ) -> None:
        self._database = database
        self._storage = storage
        self._stats = stats
        self._endpoints = tuple(endpoints)
        self._node_id = node_id
        self._connected = ConnectedPeers() if connected is None else connected
        self._clock = clock

    def derive(self) -> DerivedLists:
        """Rewrite whichever of the files the database no longer agrees with.

        Outstanding requests and addresses not worth keeping are pruned
        first, so neither list offers them.

        Returns:
            The five files, and whether the node and candidate lists were
            rewritten.
        """
        self._database.prune_seek(self._stats.seek_entry_ttl_seconds)
        self._database.prune_addresses(
            self._stats.max_addresses_per_node, self._stats.max_address_failures
        )
        kismet = self._held_kismet()
        node_list_changed = _write_if_changed(
            self._storage.node_list_path, self._node_list_body(kismet)
        )
        self.write_seek_list()
        candidate_list_changed = _write_if_changed(
            self._storage.candidate_list_path, render_candidate_list(self._candidates(kismet))
        )
        self.write_shunned_list()
        write_atomically(self._storage.peer_list_path, self._peer_list().body())
        return DerivedLists(
            node_list=self._storage.node_list_path,
            seek_list=self._storage.seek_list_path,
            candidate_list=self._storage.candidate_list_path,
            shunned_list=self._storage.shunned_list_path,
            peer_list=self._storage.peer_list_path,
            node_list_changed=node_list_changed,
            candidate_list_changed=candidate_list_changed,
        )

    def write_seek_list(self) -> bool:
        """Rewrite the seek list, unless it lists what the database does already.

        Returns:
            Whether it was rewritten.
        """
        return _write_if_changed(self._storage.seek_list_path, self._seek_list_body())

    def write_shunned_list(self) -> bool:
        """Rewrite the shunned list, unless it names what the database does already.

        Returns:
            Whether it was rewritten.
        """
        return _write_if_changed(
            self._storage.shunned_list_path, ShunnedContent.body(self._database.shunned_ids())
        )

    def _held_kismet(self) -> _HeldKismet:
        """The Kismet each node the chain this node follows names holds, this node's own too.

        It names none while this node follows no chain, or if its balances
        cannot be read, which is logged.
        """
        try:
            balances = KarmaBalances.read(self._storage.karma_dir)

        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "Cannot read the Karma balances in %s, so nodes are not ordered by Karma: %s",
                self._storage.karma_dir,
                error,
            )
            return _HeldKismet({})

        held = {str(identity): balance.kismet for identity, balance in balances.balances.items()}
        return _HeldKismet(held, held.get(str(self._node_id), 0))

    def _node_list_body(self, kismet: _HeldKismet) -> bytes:
        """This node's own entries, then each peer's last known good address, best first.

        Best is the node whose Kismet is nearest this node's own, then the
        most recently reached.
        """
        entries = [(endpoint, str(self._node_id)) for endpoint in self._endpoints]
        entries.extend(
            sorted(
                self._database.last_good_endpoints(exclude=self._node_id),
                key=lambda entry: kismet.distance(entry[1]),
            )
        )
        return render_node_list(entries, self._stats.max_list_bytes)

    def _candidates(self, kismet: _HeldKismet) -> list[tuple[str, list[str]]]:
        """Every peer to try, with its endpoints, but those given up on for now.

        Those reached come first, and within each part, in the order
        :meth:`_HeldKismet.climbing` gives, then in the order the database
        gives.
        """
        given_up = self._database.given_up_nodes(
            self._stats.max_node_failures, self._stats.node_cool_off_seconds
        )
        reached = self._database.reached_nodes()
        return sorted(
            (
                (node_id, endpoints)
                for node_id, endpoints in self._database.candidate_endpoints(exclude=self._node_id)
                if node_id not in given_up
            ),
            key=lambda candidate: (candidate[0] not in reached, *kismet.climbing(candidate[0])),
        )

    def _peer_list(self) -> PeerList:
        return PeerList.read(
            self._database,
            self._connected,
            exclude=self._node_id,
            max_peers=self._stats.max_peers_listed,
            now=self._clock(),
        )

    def _seek_list_body(self) -> bytes:
        return render_seek_list(
            self._database.seek_values(SeekKind.DATA),
            self._database.seek_values(SeekKind.SEARCH),
            self._stats.max_list_bytes,
        )


def _write_if_changed(path: Path, body: bytes) -> bool:
    """Replace ``path`` with ``body`` unless it already holds exactly that.

    Returns:
        Whether it was replaced.
    """
    try:
        if path.read_bytes() == body:
            return False

    except FileNotFoundError:
        # Not logged: there is none before the first derivation.
        pass

    except OSError as error:
        _LOGGER.warning("Cannot read %s, so it is written afresh: %s", path, error)

    write_atomically(path, body)
    return True
