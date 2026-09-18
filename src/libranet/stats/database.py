"""The node's statistics database, and the only code that opens it.

One :class:`StatsDatabase` instance lives in the stats module process, which
the implementation plan makes the sole owner of the SQLite file. Everything
else learns about data and peers through messages on the bus, so nothing
else ever needs a connection.

Each ``record_*`` method is a single upserting statement: a counter for an
identifier that has no row yet creates one, so callers never have to say
whether they are inserting or updating. The connection runs in autocommit
mode, which keeps one statement one transaction — the right granularity when
every write is an independent observation and a crash should lose at most
the last one.
"""

from __future__ import annotations
from pathlib import Path
from sqlite3 import Connection, Cursor, Row, connect
from time import time
from types import TracebackType
from typing import Any, Callable, Final, Iterable, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import nearest
from libranet.stats.records import DataStats, NodeStats
from libranet.stats.schema import OWN_NODE, SeekKind, apply_schema

# Column names used to build the upsert statements below. They are private
# constants naming columns of this module's own schema, never caller input,
# which is what makes interpolating them into SQL safe.
_EXTERNAL_REQUESTS: Final = "external_requests"
_INTERNAL_REQUESTS: Final = "internal_requests"
_PUSHES: Final = "pushes"
_CONNECTION_ATTEMPTS: Final = "connection_attempts"
_BYTES_RECEIVED: Final = "bytes_received"
_BYTES_SENT: Final = "bytes_sent"
_DATA_FOUND: Final = "data_found"
_DATA_NOT_FOUND: Final = "data_not_found"


class StatsDatabase:
    """Node and data statistics, the node list, and outstanding requests.

    Usable as a context manager, which closes the connection on exit.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        # Autocommit: `isolation_level=None` stops sqlite3 from opening an
        # implicit transaction that would hold writes until a commit.
        self._connection: Connection = connect(path, isolation_level=None)
        self._connection.row_factory = Row
        # Write-ahead logging survives an abrupt stop without losing the
        # database, which matters for a process the supervisor restarts.
        self._connection.execute("PRAGMA journal_mode=WAL")
        apply_schema(self._connection)

    def close(self) -> None:
        """Release the connection; further use raises."""
        self._connection.close()

    def __enter__(self) -> StatsDatabase:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # -- Data statistics -------------------------------------------------

    def record_request(self, content_id: ContentId, *, external: bool) -> None:
        """Count one request for ``content_id`` from a peer or from this machine."""
        self._add_to_data(content_id, _EXTERNAL_REQUESTS if external else _INTERNAL_REQUESTS, 1)

    def record_push(self, content_id: ContentId) -> None:
        """Count one upload of ``content_id`` to this node, valid or not."""
        self._add_to_data(content_id, _PUSHES, 1)

    def record_acquired(self, content_id: ContentId) -> None:
        """Note that ``content_id`` was just added to the source of truth."""
        self._execute(
            "INSERT INTO data_stats (algorithm, hash, last_acquired) "
            "VALUES (:algorithm, :hash, :now) "
            "ON CONFLICT (algorithm, hash) DO UPDATE SET last_acquired = :now",
            {"algorithm": content_id.algorithm, "hash": content_id.hash, "now": self._clock()},
        )

    def record_deleted(self, content_id: ContentId) -> None:
        """Count one deletion of ``content_id`` and add how long it was held.

        ``last_acquired`` is left as it was: it is the last time the content
        was acquired, which stays true after the copy is gone.
        """
        self._execute(
            "INSERT INTO data_stats (algorithm, hash, deletes) VALUES (:algorithm, :hash, 1) "
            "ON CONFLICT (algorithm, hash) DO UPDATE SET deletes = deletes + 1, "
            "stored_seconds = stored_seconds + COALESCE(:now - last_acquired, 0)",
            {"algorithm": content_id.algorithm, "hash": content_id.hash, "now": self._clock()},
        )

    def data_stats(self, content_id: ContentId) -> DataStats | None:
        """What is known about ``content_id``, or ``None`` if nothing is."""
        row = self._query_one(
            "SELECT * FROM data_stats WHERE algorithm = :algorithm AND hash = :hash",
            {"algorithm": content_id.algorithm, "hash": content_id.hash},
        )
        return None if row is None else DataStats.from_row(row)

    def content_ids_near(self, prefix: str, limit: int) -> list[ContentId]:
        """The ``limit`` known identifiers whose hash best matches ``prefix``.

        Both sides of ``prefix`` are scanned — the nearest hashes at or above
        it and the nearest below — because the best match by leading bits can
        lie on either side (``7`` and ``9`` are both one step from ``8``, but
        only ``9`` shares its top bit). Each side can supply the whole answer,
        so each is scanned ``limit`` deep and the two are ranked together down
        to ``limit`` results. A hash outside both windows matches fewer leading
        digits than every hash inside them, so none can be missed.
        """
        if limit < 1:
            raise ValueError(f"limit must be at least 1, got {limit}")

        rows = self._query(
            "SELECT algorithm, hash FROM data_stats WHERE hash >= :prefix "
            "ORDER BY hash ASC LIMIT :limit",
            {"prefix": prefix, "limit": limit},
        ) + self._query(
            "SELECT algorithm, hash FROM data_stats WHERE hash < :prefix "
            "ORDER BY hash DESC LIMIT :limit",
            {"prefix": prefix, "limit": limit},
        )
        return nearest(prefix, (ContentId(row["algorithm"], row["hash"]) for row in rows), limit)

    # -- Node statistics -------------------------------------------------

    def record_connection_attempt(self, node_id: ContentId) -> None:
        """Count one attempt to open a connection to ``node_id``."""
        self._add_to_node(node_id, _CONNECTION_ATTEMPTS, 1)

    def record_connection_opened(self, node_id: ContentId, endpoint: str | None = None) -> None:
        """Count a connection to ``node_id`` that was established.

        A connection that opened was also attempted, so both counters move.
        ``endpoint`` is remembered as the address that identity was last
        reached at, which is what this node publishes for it (HttpApi §10.6).
        """
        self._execute(
            "INSERT INTO node_stats (node_id, connection_attempts, successful_connections, "
            "last_connected) VALUES (:node_id, 1, 1, :now) "
            "ON CONFLICT (node_id) DO UPDATE SET "
            "connection_attempts = connection_attempts + 1, "
            "successful_connections = successful_connections + 1, last_connected = :now",
            {"node_id": str(node_id), "now": self._clock()},
        )

        if endpoint is not None:
            self.record_endpoint(node_id, endpoint)

    def record_connection_closed(self, node_id: ContentId, *, remote: bool) -> None:
        """Note a connection to ``node_id`` ending, and how long it lasted.

        ``remote`` says the peer closed it rather than this node. One close
        is expected per opened connection; ``last_connected`` is left in
        place, since it is what orders the node list.
        """
        self._execute(
            "UPDATE node_stats SET remote_disconnects = remote_disconnects + :remote, "
            "connected_seconds = connected_seconds + COALESCE(:now - last_connected, 0) "
            "WHERE node_id = :node_id",
            {"node_id": str(node_id), "now": self._clock(), "remote": int(remote)},
        )

    def record_transfer(self, node_id: ContentId, *, received: int = 0, sent: int = 0) -> None:
        """Add data bytes exchanged with ``node_id``."""
        if received:
            self._add_to_node(node_id, _BYTES_RECEIVED, received)

        if sent:
            self._add_to_node(node_id, _BYTES_SENT, sent)

    def record_data_lookup(self, node_id: ContentId, *, found: bool) -> None:
        """Count one attempt to fetch data from ``node_id`` by its outcome."""
        self._add_to_node(node_id, _DATA_FOUND if found else _DATA_NOT_FOUND, 1)

    def node_stats(self, node_id: ContentId) -> NodeStats | None:
        """What is known about ``node_id``, or ``None`` if nothing is."""
        row = self._query_one(
            "SELECT * FROM node_stats WHERE node_id = :node_id", {"node_id": str(node_id)}
        )
        return None if row is None else NodeStats.from_row(row)

    # -- The node list ---------------------------------------------------

    def record_endpoint(self, node_id: ContentId, endpoint: str) -> None:
        """Remember ``endpoint`` as where ``node_id`` was last seen."""
        self._execute(
            "INSERT INTO node_endpoints (node_id, endpoint, last_seen) "
            "VALUES (:node_id, :endpoint, :now) "
            "ON CONFLICT (node_id) DO UPDATE SET endpoint = :endpoint, last_seen = :now",
            {"node_id": str(node_id), "endpoint": endpoint, "now": self._clock()},
        )

    def record_endpoints(self, endpoints: Mapping[str, ContentId]) -> None:
        """Remember a whole received node list, keyed endpoint to node id."""
        for endpoint, node_id in endpoints.items():
            self.record_endpoint(node_id, endpoint)

    def known_endpoints(self, exclude: ContentId | None = None) -> list[tuple[str, str]]:
        """Known ``(endpoint, node id)`` pairs, best first, minus ``exclude``.

        Order is the v1 proxy for peer quality the implementation plan calls
        for: most recently connected first, then most recently seen. Nodes
        never connected to sort last but are still offered, since a node that
        knows no peers has to start somewhere.
        """
        rows = self._query(
            "SELECT node_endpoints.endpoint AS endpoint, node_endpoints.node_id AS node_id "
            "FROM node_endpoints "
            "LEFT JOIN node_stats ON node_stats.node_id = node_endpoints.node_id "
            "WHERE node_endpoints.node_id <> :exclude "
            "ORDER BY COALESCE(node_stats.last_connected, 0) DESC, node_endpoints.last_seen DESC",
            {"exclude": OWN_NODE if exclude is None else str(exclude)},
        )
        return [(row["endpoint"], row["node_id"]) for row in rows]

    # -- Outstanding requests --------------------------------------------

    def record_seek(
        self, kind: SeekKind, values: Iterable[str], node_id: ContentId | None = None
    ) -> None:
        """Record outstanding requests, this node's own unless ``node_id`` is given.

        Re-recording a request already listed refreshes when it was asked
        for, so content that keeps being wanted keeps being advertised.
        """
        self._connection.executemany(
            "INSERT INTO seek_entries (node_id, kind, value, requested_at) "
            "VALUES (:node_id, :kind, :value, :now) "
            "ON CONFLICT (node_id, kind, value) DO UPDATE SET requested_at = :now",
            [
                {
                    "node_id": OWN_NODE if node_id is None else str(node_id),
                    "kind": kind.value,
                    "value": value,
                    "now": self._clock(),
                }
                for value in values
            ],
        )

    def clear_seek(self, kind: SeekKind, value: str, node_id: ContentId | None = None) -> None:
        """Drop one outstanding request, because it has been satisfied."""
        self._execute(
            "DELETE FROM seek_entries WHERE node_id = :node_id AND kind = :kind AND value = :value",
            {
                "node_id": OWN_NODE if node_id is None else str(node_id),
                "kind": kind.value,
                "value": value,
            },
        )

    def seek_values(self, kind: SeekKind, node_id: ContentId | None = None) -> list[str]:
        """Outstanding requests of one kind, most recently asked for first."""
        rows = self._query(
            "SELECT value FROM seek_entries WHERE node_id = :node_id AND kind = :kind "
            "ORDER BY requested_at DESC, value ASC",
            {"node_id": OWN_NODE if node_id is None else str(node_id), "kind": kind.value},
        )
        return [row["value"] for row in rows]

    def prune_seek(self, max_age_seconds: float) -> int:
        """Forget outstanding requests older than ``max_age_seconds``.

        Returns:
            How many entries were dropped.
        """
        cursor = self._execute(
            "DELETE FROM seek_entries WHERE requested_at < :cutoff",
            {"cutoff": self._clock() - max_age_seconds},
        )
        return cursor.rowcount

    # -- Statement plumbing ----------------------------------------------

    def _add_to_data(self, content_id: ContentId, column: str, amount: int) -> None:
        self._execute(
            f"INSERT INTO data_stats (algorithm, hash, {column}) "
            f"VALUES (:algorithm, :hash, :amount) "
            f"ON CONFLICT (algorithm, hash) DO UPDATE SET {column} = {column} + :amount",
            {"algorithm": content_id.algorithm, "hash": content_id.hash, "amount": amount},
        )

    def _add_to_node(self, node_id: ContentId, column: str, amount: int) -> None:
        self._execute(
            f"INSERT INTO node_stats (node_id, {column}) VALUES (:node_id, :amount) "
            f"ON CONFLICT (node_id) DO UPDATE SET {column} = {column} + :amount",
            {"node_id": str(node_id), "amount": amount},
        )

    def _execute(self, statement: str, parameters: Mapping[str, Any]) -> Cursor:
        return self._connection.execute(statement, parameters)

    def _query(self, statement: str, parameters: Mapping[str, Any]) -> list[Row]:
        rows: list[Row] = self._connection.execute(statement, parameters).fetchall()
        return rows

    def _query_one(self, statement: str, parameters: Mapping[str, Any]) -> Row | None:
        row: Row | None = self._connection.execute(statement, parameters).fetchone()
        return row
