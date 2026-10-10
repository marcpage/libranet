"""The SQLite schema the stats module owns (Phase 1 Step 8).

Every statement is ``IF NOT EXISTS``, so :func:`apply_schema` both creates a
fresh database and leaves an existing one alone. It is applied on every
start; no other process opens this file.

Five tables cover what the implementation plan asks a node to remember:

``data_stats``
    One row per content identifier the node has heard of, whether or not it
    holds the content. ``last_requested`` is when it was last asked for, and
    ``last_acquired`` when it was last added to the source of truth.
    ``size`` is its size as stored while this node holds it, and ``NULL``
    otherwise (Phase 2 Step 28): what the node holds is known from the
    content announced as stored, less what is reported deleted.
    ``stored_seconds`` accumulates how long copies of it were held before
    being deleted. ``blocked_at`` is when this node blocked it, as content
    it will not hold and refuses, and ``NULL`` if it has not (Phase 4 Step
    30). A row is never deleted, so a block outlives the content it names:
    a node that forgot it would take the content back from the next peer to
    push it.

``node_stats``
    One row per peer node id. ``last_connected`` is when the last successful
    connection to it was established and is never cleared, so the time a
    connection lasted can be measured from it; ``connected_seconds``
    accumulates the time those connections lasted. It no longer orders the
    lists, which go by when each address last worked, in ``node_addresses``
    (Phase 2 Step 23). ``consecutive_failures`` counts the attempts in
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

``app_bundles``
    One row per bundle an application has been served from, and
    ``last_accessed``, when it last was (Phase 2 Step 29). The web server
    reports it at most once an hour for each bundle, so it can be up to an
    hour behind.

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
        last_requested REAL,
        last_acquired REAL,
        stored_seconds REAL NOT NULL DEFAULT 0,
        size INTEGER,
        blocked_at REAL,
        PRIMARY KEY (algorithm, hash)
    )
    """,
    # Searches match on the hash alone, across every algorithm, and scan a
    # range of it rather than a single value.
    "CREATE INDEX IF NOT EXISTS data_stats_by_hash ON data_stats (hash)",
    # Ranking what to evict looks only at content held, and finds the held
    # hashes on either side of the node id's.
    "CREATE INDEX IF NOT EXISTS data_stats_held ON data_stats (hash) WHERE size IS NOT NULL",
    # The blocked list is derived from the few rows blocked, of however many.
    "CREATE INDEX IF NOT EXISTS data_stats_blocked ON data_stats (hash) "
    "WHERE blocked_at IS NOT NULL",
    """
    CREATE TABLE IF NOT EXISTS node_stats (
        node_id TEXT NOT NULL PRIMARY KEY,
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
    CREATE TABLE IF NOT EXISTS app_bundles (
        algorithm TEXT NOT NULL,
        hash TEXT NOT NULL,
        last_accessed REAL NOT NULL,
        PRIMARY KEY (algorithm, hash)
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
