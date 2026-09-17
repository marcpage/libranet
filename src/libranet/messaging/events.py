"""Every event type that travels over the message bus.

One enum, shared by every module, so a publisher and its subscribers can
never disagree on spelling. Each member notes who publishes it and who is
expected to react. Payload fields beyond the common envelope are still an
open item in the implementation plan and are settled by the step that first
publishes each event.
"""

from __future__ import annotations
from enum import StrEnum


class EventType(StrEnum):
    """The kind of a message, carried in its envelope."""

    # Lifecycle (supervisor → all). Every module stops its receive loop on it.
    SHUTDOWN = "shutdown"

    # Web server read path (Step 5).
    DATA_NOT_FOUND = "data.not_found"  # webserver → fetcher
    SEARCH_REQUESTED = "data.search_requested"  # webserver → stats

    # Web server write path and validation (Step 7).
    PUT_COMPLETED = "data.put_completed"  # webserver, connections → validator
    DATA_STORED = "data.stored"  # validator → eviction, stats
    DATA_REJECTED = "data.rejected"  # validator → stats

    # Node and seek lists (Steps 8 and 9).
    NODES_RECEIVED = "nodes.received"  # webserver → stats
    SEEK_RECEIVED = "seek.received"  # webserver → stats
    NODE_LIST_UPDATED = "nodes.updated"  # stats → connections

    # Outgoing connections and fetching (Steps 11 and 12).
    CONNECTION_OPENED = "connection.opened"  # connections → stats
    CONNECTION_CLOSED = "connection.closed"  # connections → stats
    FETCH_REQUESTED = "fetch.requested"  # fetcher → connections
    FETCH_SUCCEEDED = "fetch.succeeded"  # connections → fetcher
    FETCH_FAILED = "fetch.failed"  # connections → fetcher

    # Application serving (Step 14).
    APP_PATH_NOT_FOUND = "app.path_not_found"  # webserver → unbundler
    APP_PATH_RESOLVED = "app.path_resolved"  # unbundler → stats

    # Eviction hand-off (Step 15).
    EVICTION_NOTICE = "eviction.notice"  # eviction → connections
    EVICTION_ACKNOWLEDGED = "eviction.acknowledged"  # connections → eviction
