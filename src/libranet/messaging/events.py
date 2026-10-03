"""Every event type that travels over the message bus.

One enum, shared by every module, so a publisher and its subscribers can
never disagree on spelling. Each member notes who publishes it and who is
expected to react. Each publishing module's docstring describes the payload
it sends beyond the common envelope. A payload value that more than one
module spells gets an enum here too.
"""

from __future__ import annotations
from enum import StrEnum


class EventType(StrEnum):
    """The kind of a message, carried in its envelope."""

    # Lifecycle. The dispatcher and every module leave their receive loops on
    # it, but nothing in a running node publishes it: the supervisor stops its
    # children with stop events, and tests put it straight into an inbox.
    SHUTDOWN = "shutdown"

    # Web server read path (Step 5).
    DATA_REQUESTED = "data.requested"  # webserver → stats
    DATA_NOT_FOUND = "data.not_found"  # webserver, unbundler, backup → fetcher, stats
    SEARCH_REQUESTED = "data.search_requested"  # webserver → stats

    # Web server write path and validation (Step 7).
    PUT_COMPLETED = "data.put_completed"  # webserver, connections → validator
    # validator, backup, webserver, connections → eviction, stats, backup, connections
    DATA_STORED = "data.stored"
    DATA_REJECTED = "data.rejected"  # validator → stats

    # Node and seek lists (Steps 8 and 9).
    NODES_RECEIVED = "nodes.received"  # webserver, connections → stats, connections
    SEEK_RECEIVED = "seek.received"  # webserver → stats
    NODE_LIST_UPDATED = "nodes.updated"  # stats → connections
    ADDRESS_VERIFIED = "address.verified"  # connections → stats (Phase 2 Step 23)

    # Outgoing connections and fetching (Steps 11 and 12).
    CONNECTION_OPENED = "connection.opened"  # connections → stats
    CONNECTION_CLOSED = "connection.closed"  # connections → stats
    CONNECTION_FAILED = "connection.failed"  # connections → stats
    NODE_UNREACHED = "node.unreached"  # connections → stats (Phase 2 Step 26)
    DATA_SENT = "data.sent"  # connections → stats
    FETCH_REQUESTED = "fetch.requested"  # fetcher → connections
    FETCH_ATTEMPTED = "fetch.attempted"  # connections → stats
    FETCH_SUCCEEDED = "fetch.succeeded"  # connections → fetcher
    FETCH_FAILED = "fetch.failed"  # connections → fetcher

    # Application serving (Step 14).
    APP_PATH_NOT_FOUND = "app.path_not_found"  # webserver → unbundler
    APP_PATH_RESOLVED = "app.path_resolved"  # unbundler → webserver

    # `/config` administration surface and the backup module (Steps 18-20, 38),
    # and importing a local file (Phase 3 Step 69).
    BACKUP_JOB_CONFIGURED = "backup.job_configured"  # webserver → backup
    BACKUP_JOB_REMOVED = "backup.job_removed"  # webserver → backup
    BACKUP_RUN_REQUESTED = "backup.run_requested"  # webserver → backup
    RESTORE_REQUESTED = "backup.restore_requested"  # webserver → backup
    BUILD_REQUESTED = "backup.build_requested"  # webserver → backup
    EXPORT_REQUESTED = "backup.export_requested"  # webserver → backup
    IMPORT_REQUESTED = "backup.import_requested"  # webserver → backup
    BACKUP_STATE = "backup.state"  # backup → webserver

    # Eviction hand-off (Step 15).
    EVICTION_NOTICE = "eviction.notice"  # eviction → connections
    EVICTION_ACKNOWLEDGED = "eviction.acknowledged"  # connections → eviction
    DATA_DELETED = "data.deleted"  # eviction → stats

    # What to let go of first (Phase 2 Step 28).
    EVICTION_CANDIDATES_REQUESTED = "eviction.candidates_requested"  # eviction → stats
    EVICTION_CANDIDATES = "eviction.candidates"  # stats → eviction

    # Deleting the resolved files of applications not used lately (Phase 2 Step 29).
    APP_ACCESSED = "app.accessed"  # webserver → stats
    RESOLVED_RECLAIM_REQUESTED = "resolved.reclaim_requested"  # eviction → stats
    RESOLVED_RECLAIM = "resolved.reclaim"  # stats → unbundler
    RESOLVED_RECLAIMED = "resolved.reclaimed"  # unbundler → eviction

    # The peers connected, whose public keys are never evicted (Phase 2 Step 53).
    PEERS_CONNECTED_REQUESTED = "peers.connected_requested"  # eviction → connections, webserver
    PEERS_CONNECTED = "peers.connected"  # connections, webserver → eviction

    # Whether storage is full, so content this node creates waits (Phase 2 Step 63).
    STORAGE_FULL_REQUESTED = "storage.full_requested"  # backup → eviction
    STORAGE_FULL = "storage.full"  # eviction → backup


class AddressSource(StrEnum):
    """How this node learned an address of a peer (Phase 2 Step 23).

    Stats keeps the strongest source it has seen for each address: the
    members are listed weakest first.
    """

    RELAYED = "relayed"  # in the node list of some other node
    REVERSE_DNS = "reverse_dns"  # a name found for an observed address
    ADVERTISED = "advertised"  # in the node's own node list, as its own
    OBSERVED = "observed"  # a `localhost` entry resolved to the connection's address
    DIALED = "dialed"  # this node reached the node there


class ConnectionDirection(StrEnum):
    """Which side opened the connections a ``peers.connected`` names (Phase 2 Step 53)."""

    OUTBOUND = "outbound"  # dialed by this node's connection manager
    INBOUND = "inbound"  # dialed by the peer, to this node's web server


class PathOutcome(StrEnum):
    """What the unbundler found at an application path, as ``app.path_resolved`` reports it.

    A file is served once it is stored, so the web server keeps only the
    other outcomes, to answer later requests for the same path without
    asking again.
    """

    # The file is written where the web server looks for it.
    STORED = "stored"

    # The bundle holds nothing at the path.
    NOT_FOUND = "not_found"

    # The path names a directory, or reaches a file through a symlink; the
    # message's ``location`` is the entry path to go to instead.
    REDIRECT = "redirect"

    # The bundle, or the file, cannot be served; the message's ``detail``
    # says why.
    UNUSABLE = "unusable"


class ConflictBehavior(StrEnum):
    """What a restore or an export does where what it would write is already there.

    A restore into a directory that is not empty (BackupSpecification §5), or
    an export to an archive that exists, is refused unless it may overwrite.
    """

    REFUSE = "refuse"
    OVERWRITE = "overwrite"
