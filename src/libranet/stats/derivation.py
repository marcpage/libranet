"""Deriving the plain ``/data/nodes`` and ``/data/seek`` files from the database.

The web server serves both as static files (Step 9), so the stats module
rewrites them periodically rather than answering a request per read. A file
is replaced only when its contents actually change, which keeps its
modification time meaningful and saves the node list's readers (the
connection manager) from reacting to a rewrite that says nothing new.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from libranet.atomic_file import write_atomically
from libranet.cas.content_id import ContentId
from libranet.config.models import StatsConfig, StorageConfig
from libranet.stats.database import StatsDatabase
from libranet.stats.lists import render_node_list, render_seek_list
from libranet.stats.schema import SeekKind


@dataclass(frozen=True)
class DerivedLists:
    """Where the two derived files are, and which of them just changed."""

    node_list: Path
    seek_list: Path
    node_list_changed: bool
    seek_list_changed: bool


class ListDeriver:
    """Writes the node list and seek list this node publishes.

    ``endpoint`` and ``node_id`` describe this node itself: a node list must
    always name its sender (HttpApi §10.1), and it heads the list because no
    peer's address is better known to us than our own.
    """

    def __init__(
        self,
        database: StatsDatabase,
        storage: StorageConfig,
        stats: StatsConfig,
        endpoint: str,
        node_id: ContentId,
    ) -> None:
        self._database = database
        self._storage = storage
        self._stats = stats
        self._endpoint = endpoint
        self._node_id = node_id

    def derive(self) -> DerivedLists:
        """Rewrite whichever of the two files the database no longer agrees with."""
        self._database.prune_seek(self._stats.seek_entry_ttl_seconds)
        return DerivedLists(
            node_list=self._storage.node_list_path,
            seek_list=self._storage.seek_list_path,
            node_list_changed=_write_if_changed(
                self._storage.node_list_path, self._node_list_body()
            ),
            seek_list_changed=_write_if_changed(
                self._storage.seek_list_path, self._seek_list_body()
            ),
        )

    def _node_list_body(self) -> bytes:
        entries = [(self._endpoint, str(self._node_id))]
        entries.extend(self._database.known_endpoints(exclude=self._node_id))
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
