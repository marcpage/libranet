"""The SQLite schema the stats module owns (Phase 1 Step 8).

Every statement is ``IF NOT EXISTS``, so :func:`apply_schema` both creates a
fresh database and leaves an existing one alone. It is applied on every
start; no other process opens this file.

Four tables cover what the implementation plan asks a node to remember:

``data_stats``
    One row per content identifier the node has heard of, whether or not it
    holds the content. ``last_acquired`` is when the id was last added to
    the source of truth, and ``stored_seconds`` accumulates how long copies
    of it were held before being deleted.

``node_stats``
    One row per peer node id. ``last_connected`` is when the last successful
    connection to it was established and is never cleared, so it also serves
    as the v1 node-list priority; ``connected_seconds`` accumulates the time
    those connections lasted. ``consecutive_failures`` counts the attempts in
    a row to reach it, each dialing every address it was to be tried at,
    that reached it at none, and ``last_failure`` is when the last of them
    ended (Phase 2 Step 26).

``node_addresses``
    Every place a node id may be reached, keyed by node id and endpoint
    (Phase 2 Step 23): a node can have many, and an address is just a
    possibly ephemeral location of one. ``source`` is the strongest
    :class:`~libranet.messaging.events.AddressSource` it was learned from,
    and ``last_learned`` the last time it was. The rest record trying it:
    ``first_success`` and ``last_success`` are when a connection to the node
    there first and last proved the node's identity, and
    ``consecutive_failures`` counts the attempts since the last that did.

``seek_entries``
    Outstanding requests: content ids and search prefixes that have been
    asked for but not answered. ``node_id`` is :data:`OWN_NODE` for this
    node's own list (HttpApi §10.7.1) and a peer's id for a list it
    published to us (Step 9).
"""

from __future__ import annotations
from enum import StrEnum
from sqlite3 import Connection
from typing import Final

#: ``seek_entries.node_id`` value standing for this node's own seek list.
#: A real node id is ``{algorithm}/{hash}``, so it can never collide.
OWN_NODE: Final = ""


class SeekKind(StrEnum):
    """What one ``seek_entries`` row names, matching the two keys of §10.7.1."""

    DATA = "data"  # an exact `{algorithm}/{hash}` content identifier
    SEARCH = "search"  # a hash prefix being searched for


SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS data_stats (
        algorithm TEXT NOT NULL,
        hash TEXT NOT NULL,
        external_requests INTEGER NOT NULL DEFAULT 0,
        internal_requests INTEGER NOT NULL DEFAULT 0,
        pushes INTEGER NOT NULL DEFAULT 0,
        deletes INTEGER NOT NULL DEFAULT 0,
        last_acquired REAL,
        stored_seconds REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (algorithm, hash)
    )
    """,
    # Searches match on the hash alone, across every algorithm, and scan a
    # range of it rather than a single value.
    "CREATE INDEX IF NOT EXISTS data_stats_by_hash ON data_stats (hash)",
    """
    CREATE TABLE IF NOT EXISTS node_stats (
        node_id TEXT PRIMARY KEY,
        connection_attempts INTEGER NOT NULL DEFAULT 0,
        successful_connections INTEGER NOT NULL DEFAULT 0,
        remote_disconnects INTEGER NOT NULL DEFAULT 0,
        last_connected REAL,
        connected_seconds REAL NOT NULL DEFAULT 0,
        bytes_received INTEGER NOT NULL DEFAULT 0,
        bytes_sent INTEGER NOT NULL DEFAULT 0,
        data_found INTEGER NOT NULL DEFAULT 0,
        data_not_found INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        last_failure REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS node_addresses (
        node_id TEXT NOT NULL,
        endpoint TEXT NOT NULL,
        source TEXT NOT NULL,
        last_learned REAL NOT NULL,
        first_success REAL,
        last_success REAL,
        attempts INTEGER NOT NULL DEFAULT 0,
        successes INTEGER NOT NULL DEFAULT 0,
        consecutive_failures INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (node_id, endpoint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS seek_entries (
        node_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        requested_at REAL NOT NULL,
        PRIMARY KEY (node_id, kind, value)
    )
    """,
)


def apply_schema(connection: Connection) -> None:
    """Create any table or index this schema needs that the database lacks."""
    for statement in SCHEMA_STATEMENTS:
        connection.execute(statement)
