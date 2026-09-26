"""The rows of the statistics database, as values callers can work with.

Each class mirrors one table from :mod:`libranet.stats.schema`, with the
counters the implementation plan calls for. Instances are read-only
snapshots: they are built from a query and never write anything back.
Identifiers in a row were normalized on the way in
(:mod:`libranet.stats.database`), so they are rebuilt as they are.
"""

from __future__ import annotations
from dataclasses import dataclass
from sqlite3 import Row

from libranet.cas.content_id import ContentId


@dataclass(frozen=True)
class DataStats:
    """What is known about one content identifier."""

    content_id: ContentId
    external_requests: int = 0
    internal_requests: int = 0
    pushes: int = 0
    deletes: int = 0
    last_acquired: float | None = None
    stored_seconds: float = 0.0

    @classmethod
    def from_row(cls, row: Row) -> DataStats:
        """Build a snapshot from a ``data_stats`` row."""
        return cls(
            content_id=ContentId(row["algorithm"], row["hash"]),
            external_requests=row["external_requests"],
            internal_requests=row["internal_requests"],
            pushes=row["pushes"],
            deletes=row["deletes"],
            last_acquired=row["last_acquired"],
            stored_seconds=row["stored_seconds"],
        )

    @property
    def requests(self) -> int:
        """Every request for this content, local and remote."""
        return self.external_requests + self.internal_requests


@dataclass(frozen=True)
class NodeStats:
    """What is known about one peer node."""

    node_id: ContentId
    connection_attempts: int = 0
    successful_connections: int = 0
    remote_disconnects: int = 0
    last_connected: float | None = None
    connected_seconds: float = 0.0
    bytes_received: int = 0
    bytes_sent: int = 0
    data_found: int = 0
    data_not_found: int = 0

    @classmethod
    def from_row(cls, row: Row) -> NodeStats:
        """Build a snapshot from a ``node_stats`` row."""
        return cls(
            node_id=ContentId.parse(row["node_id"]),
            connection_attempts=row["connection_attempts"],
            successful_connections=row["successful_connections"],
            remote_disconnects=row["remote_disconnects"],
            last_connected=row["last_connected"],
            connected_seconds=row["connected_seconds"],
            bytes_received=row["bytes_received"],
            bytes_sent=row["bytes_sent"],
            data_found=row["data_found"],
            data_not_found=row["data_not_found"],
        )
