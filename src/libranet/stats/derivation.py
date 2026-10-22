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
come first, the most recently reached first, then the rest, the most
recently learned of first; each node's addresses follow the same rule::

    {"nodes": [{"node_id": "sha256/<hex>", "endpoints": ["http://203.0.113.9:4300", ...]}]}

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
from typing import Callable, Sequence

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.cas.shunned import ShunnedContent
from libranet.config.models import StatsConfig, StorageConfig
from libranet.messaging.connected_peers import ConnectedPeers
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
        node_list_changed = _write_if_changed(self._storage.node_list_path, self._node_list_body())
        self.write_seek_list()
        candidate_list_changed = _write_if_changed(
            self._storage.candidate_list_path, render_candidate_list(self._candidates())
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

    def _node_list_body(self) -> bytes:
        """This node's own entries, then each peer's last known good address, best first."""
        entries = [(endpoint, str(self._node_id)) for endpoint in self._endpoints]
        entries.extend(self._database.last_good_endpoints(exclude=self._node_id))
        return render_node_list(entries, self._stats.max_list_bytes)

    def _candidates(self) -> list[tuple[str, list[str]]]:
        """Every peer to try, with its endpoints, but those given up on for now."""
        given_up = self._database.given_up_nodes(
            self._stats.max_node_failures, self._stats.node_cool_off_seconds
        )
        return [
            (node_id, endpoints)
            for node_id, endpoints in self._database.candidate_endpoints(exclude=self._node_id)
            if node_id not in given_up
        ]

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
