"""Deriving the plain ``/data/nodes`` and ``/data/seek`` files, and the candidate list.

The web server serves the first two as static files (Step 9), so the stats
module rewrites them periodically rather than answering a request per read.
The node list is what this node publishes; the candidate list is what the
connection manager dials from (Phase 2 Step 23). A file is replaced only
when its contents actually change, which keeps its modification time
meaningful and saves the candidate list's reader from reacting to a rewrite
that says nothing new.

The candidate list names every node this node knows an address for, but
itself, with every address it knows, in the order to try them. Nodes that
have been reached come first, the most recently reached first, then the
rest, the most recently learned of first; each node's addresses follow the
same rule::

    {"nodes": [{"node_id": "sha256/<hex>", "endpoints": ["http://203.0.113.9:4300", ...]}]}
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.config.models import StatsConfig, StorageConfig
from libranet.stats.database import StatsDatabase
from libranet.stats.lists import render_candidate_list, render_node_list, render_seek_list
from libranet.stats.schema import SeekKind


@dataclass(frozen=True)
class DerivedLists:
    """The three files a derivation maintains, and whether the candidate list changed.

    Only the candidate list carries a changed flag, because only it is
    announced: a new one is the connection manager's cue to reconsider its
    peer mix (Step 11). Nothing is published about the other two — a peer
    reads them from the file when it asks — so whether they were rewritten
    is this module's business alone.
    """

    node_list: Path
    seek_list: Path
    candidate_list: Path
    candidate_list_changed: bool


class ListDeriver:
    """Writes the node list and seek list this node publishes, and its candidate list.

    ``endpoints`` and ``node_id`` describe this node itself: a node list must
    always name its sender (HttpApi §10.1), and it heads the list because no
    peer's address is better known to us than our own.
    """

    def __init__(
        self,
        database: StatsDatabase,
        storage: StorageConfig,
        stats: StatsConfig,
        endpoints: Sequence[str],
        node_id: ContentId,
    ) -> None:
        self._database = database
        self._storage = storage
        self._stats = stats
        self._endpoints = tuple(endpoints)
        self._node_id = node_id

    def derive(self) -> DerivedLists:
        """Rewrite whichever of the files the database no longer agrees with.

        Outstanding requests and addresses not worth keeping are pruned
        first, so neither list offers them.
        """
        self._database.prune_seek(self._stats.seek_entry_ttl_seconds)
        self._database.prune_addresses(
            self._stats.max_addresses_per_node, self._stats.max_address_failures
        )
        _write_if_changed(self._storage.node_list_path, self._node_list_body())
        _write_if_changed(self._storage.seek_list_path, self._seek_list_body())
        candidate_list_changed = _write_if_changed(
            self._storage.candidate_list_path,
            render_candidate_list(self._database.candidate_endpoints(exclude=self._node_id)),
        )
        return DerivedLists(
            node_list=self._storage.node_list_path,
            seek_list=self._storage.seek_list_path,
            candidate_list=self._storage.candidate_list_path,
            candidate_list_changed=candidate_list_changed,
        )

    def _node_list_body(self) -> bytes:
        """This node's own entries, then each peer's last known good address, best first."""
        entries = [(endpoint, str(self._node_id)) for endpoint in self._endpoints]
        entries.extend(self._database.last_good_endpoints(exclude=self._node_id))
        return render_node_list(entries, self._stats.max_list_bytes)

    def _seek_list_body(self) -> bytes:
        return render_seek_list(
            self._database.seek_values(SeekKind.DATA),
            self._database.seek_values(SeekKind.SEARCH),
            self._stats.max_list_bytes,
        )


def _write_if_changed(path: Path, body: bytes) -> bool:
    """Replace ``path`` with ``body`` unless it already holds exactly that."""
    try:
        if path.read_bytes() == body:
            return False

    except OSError:
        pass

    write_atomically(path, body)
    return True
