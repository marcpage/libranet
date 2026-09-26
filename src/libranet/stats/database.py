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

Identifiers are normalized on the way in. Every method that writes one
takes a :class:`ContentId`, which holds only the lower-case form HttpApi
§5.4 stores, so an identifier read back from a row is already normalized
and is rebuilt without being checked against its algorithm again. Seek
values are text, and callers pass them normalized, as
:func:`~libranet.webserver.list_bodies.parse_seek_list` and
:func:`~libranet.webserver.search.normalize_prefix` leave them.
"""

from __future__ import annotations
from itertools import groupby
from pathlib import Path
from sqlite3 import Connection, Cursor, Row, connect
from time import time
from types import TracebackType
from typing import Any, Callable, Final, Iterable, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import nearest
from libranet.messaging.events import AddressSource
from libranet.stats.records import DataStats, NodeAddress, NodeStats
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


def _source_rank(expression: str) -> str:
    """SQL for the rank of the source ``expression`` names, weakest lowest."""
    cases = " ".join(
        f"WHEN '{source.value}' THEN {rank}" for rank, source in enumerate(AddressSource)
    )
    return f"CASE {expression} {cases} END"


# The stronger of an address's recorded source and the one it was just
# learned from again. Built from the enum's own values, never caller input.
_STRONGER_SOURCE: Final = (
    f"CASE WHEN {_source_rank('excluded.source')} > {_source_rank('source')} "
    "THEN excluded.source ELSE source END"
)

# The order a node's addresses are tried in: those that have worked, the
# most recently first, then the rest, the most recently learned first.
_TRY_ORDER: Final = "last_success IS NULL, last_success DESC, last_learned DESC, endpoint"


class StatsDatabase:
    """Node and data statistics, where nodes may be reached, and outstanding requests.

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
        ``endpoint``, where it was reached, is an address that worked.
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
            self.record_address_worked(node_id, endpoint)

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

    # -- Where nodes may be reached --------------------------------------

    def record_addresses(self, addresses: Mapping[str, ContentId], source: AddressSource) -> None:
        """Remember addresses learned from ``source``, keyed endpoint to node id.

        An address already known is learned again: when it was learned
        moves on, and its source becomes ``source`` if that is stronger.
        """
        self._connection.executemany(
            "INSERT INTO node_addresses (node_id, endpoint, source, last_learned) "
            "VALUES (:node_id, :endpoint, :source, :now) "
            "ON CONFLICT (node_id, endpoint) DO UPDATE SET last_learned = :now, "
            f"source = {_STRONGER_SOURCE}",
            [
                {
                    "node_id": str(node_id),
                    "endpoint": endpoint,
                    "source": source.value,
                    "now": self._clock(),
                }
                for endpoint, node_id in addresses.items()
            ],
        )

    def record_address_worked(self, node_id: ContentId, endpoint: str) -> None:
        """Note that dialing ``endpoint`` reached ``node_id``, which proved its identity.

        This is the one way an address becomes one that has worked. It
        counts nothing in the node's own statistics, since a connection that
        reached the node may still not have been kept.
        """
        self._execute(
            "INSERT INTO node_addresses (node_id, endpoint, source, last_learned, "
            "first_success, last_success, attempts, successes) "
            "VALUES (:node_id, :endpoint, :source, :now, :now, :now, 1, 1) "
            "ON CONFLICT (node_id, endpoint) DO UPDATE SET source = :source, "
            "first_success = COALESCE(first_success, :now), last_success = :now, "
            "attempts = attempts + 1, successes = successes + 1, consecutive_failures = 0",
            {
                "node_id": str(node_id),
                "endpoint": endpoint,
                "source": AddressSource.DIALED.value,
                "now": self._clock(),
            },
        )

    def record_address_failed(self, node_id: ContentId, endpoint: str) -> None:
        """Count a failed attempt to reach ``node_id`` at ``endpoint``, if that address is known."""
        self._execute(
            "UPDATE node_addresses SET attempts = attempts + 1, "
            "consecutive_failures = consecutive_failures + 1 "
            "WHERE node_id = :node_id AND endpoint = :endpoint",
            {"node_id": str(node_id), "endpoint": endpoint},
        )

    def node_addresses(self, node_id: ContentId) -> list[NodeAddress]:
        """Every known address of ``node_id``, in the order they are tried."""
        rows = self._query(
            f"SELECT * FROM node_addresses WHERE node_id = :node_id ORDER BY {_TRY_ORDER}",
            {"node_id": str(node_id)},
        )
        return [NodeAddress.from_row(row) for row in rows]

    def last_good_endpoints(self, exclude: ContentId | None = None) -> list[tuple[str, str]]:
        """Each node's last known good ``(endpoint, node id)``, best first, minus ``exclude``.

        A node's last known good endpoint is the one it was last reached at
        (HttpApi §10.6). It is left out if it has failed since, so only
        addresses that worked the last time they were tried are listed.
        Best is the most recently reached.
        """
        rows = self._query(
            "SELECT endpoint, node_id FROM ("
            "SELECT endpoint, node_id, last_success, consecutive_failures, "
            "ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY last_success DESC, endpoint) "
            "AS place FROM node_addresses "
            "WHERE last_success IS NOT NULL AND node_id <> :exclude) "
            "WHERE place = 1 AND consecutive_failures = 0 "
            "ORDER BY last_success DESC, endpoint",
            {"exclude": OWN_NODE if exclude is None else str(exclude)},
        )
        return [(row["endpoint"], row["node_id"]) for row in rows]

    def candidate_endpoints(self, exclude: ContentId | None = None) -> list[tuple[str, list[str]]]:
        """Every known node id but ``exclude``, with its endpoints in the order to try them.

        Nodes that have been reached come first, the most recently reached
        first, then the rest, the most recently learned first.
        """
        rows = self._query(
            "SELECT node_id, endpoint, "
            "MAX(last_success) OVER (PARTITION BY node_id) AS node_success, "
            "MAX(last_learned) OVER (PARTITION BY node_id) AS node_learned "
            "FROM node_addresses WHERE node_id <> :exclude "
            "ORDER BY node_success IS NULL, node_success DESC, node_learned DESC, node_id, "
            f"{_TRY_ORDER}",
            {"exclude": OWN_NODE if exclude is None else str(exclude)},
        )
        return [
            (node_id, [row["endpoint"] for row in node_rows])
            for node_id, node_rows in groupby(rows, key=lambda row: str(row["node_id"]))
        ]

    def prune_addresses(self, max_per_node: int, max_failures: int) -> int:
        """Forget addresses not worth trying again.

        An address that has never worked goes once ``max_failures``
        attempts in a row have failed. Then each node keeps at most
        ``max_per_node``: those that have worked are kept first, the most
        recently reached first, then the rest, any not merely relayed first,
        the most recently learned first.

        Returns:
            How many addresses were dropped.
        """
        failing = self._execute(
            "DELETE FROM node_addresses "
            "WHERE last_success IS NULL AND consecutive_failures >= :max_failures",
            {"max_failures": max_failures},
        )
        surplus = self._execute(
            "DELETE FROM node_addresses WHERE rowid IN ("
            "SELECT rowid FROM (SELECT rowid, ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY "
            "last_success IS NULL, last_success DESC, source = :relayed, last_learned DESC, "
            "endpoint) AS place FROM node_addresses) WHERE place > :max_per_node)",
            {"relayed": AddressSource.RELAYED.value, "max_per_node": max_per_node},
        )
        return failing.rowcount + surplus.rowcount

    # -- Outstanding requests --------------------------------------------

    def record_seek(
        self, kind: SeekKind, values: Iterable[str], node_id: ContentId | None = None
    ) -> None:
        """Record outstanding requests, this node's own unless ``node_id`` is given.

        ``values`` must already be normalized. Re-recording a request already
        listed refreshes when it was asked for, so content that keeps being
        wanted keeps being advertised.
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
        # Annotated because `fetchall` is typed as returning `list[Any]`.
        rows: list[Row] = self._execute(statement, parameters).fetchall()
        return rows

    def _query_one(self, statement: str, parameters: Mapping[str, Any]) -> Row | None:
        row: Row | None = self._execute(statement, parameters).fetchone()
        return row
