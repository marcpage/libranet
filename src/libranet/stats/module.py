"""The stats module process: the node's memory of data and peers.

It is the only process that opens the SQLite file. Everything it records
arrives as a broadcast from another module, and everything it publishes
leaves as one of the two derived list files plus a notice that the node list
changed. Between derivations it does nothing but record, so a burst of
requests costs one small statement each.

The payloads it consumes, by event:

``data.requested``     ``{"algorithm", "hash", "external"}`` (Step 5)
``data.not_found``     ``{"algorithm", "hash"}`` (Step 5)
``data.search_requested`` ``{"prefix", "cache_path"}`` (Step 5)
``data.stored``        ``{"algorithm", "hash", "node_id", "size"}`` (Step 7)
``data.rejected``      ``{"algorithm", "hash", "node_id"}`` (Step 7)
``nodes.received``     ``{"nodes": {endpoint: node id}}`` (Steps 9 and 11)
``seek.received``      ``{"node_id", "data": [...], "search": [...]}`` (Step 9)
``connection.opened``  ``{"node_id", "endpoint"}`` (Step 11)
``connection.closed``  ``{"node_id", "remote"}`` (Step 11)
``connection.failed``  ``{"node_id", "endpoint"}`` (Step 11)
``data.sent``          ``{"algorithm", "hash", "node_id", "size"}`` (Step 11)
``fetch.attempted``    ``{"algorithm", "hash", "node_id", "found"}`` (Step 11)

A miss on ``GET /data/{algorithm}/{hash}`` and a ``GET /data/search/{prefix}``
are both requests this node could not answer, so each becomes an entry in
its own seek list until the content arrives or the entry ages out. Storing
content clears its entry.

It publishes ``nodes.updated`` — ``{"path": "<node list file>"}`` — whenever
the derived node list changes, which is the connection manager's cue to
reconsider its peer mix (Step 11).
"""

from __future__ import annotations
from logging import Logger
from time import time
from typing import Callable, ClassVar, Mapping, TypeVar

from libranet.cas.content_id import ContentId
from libranet.cas.errors import CasError
from libranet.config.models import LibranetConfig
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, event_of
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import ListDeriver
from libranet.stats.enrichment import SearchEnricher
from libranet.stats.schema import SeekKind
from libranet.webserver.search import SearchCache, normalize_prefix

_Component = TypeVar("_Component")


class StatsModule(ModuleBase):
    """Records what the node sees and derives the lists it publishes."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.DATA_REQUESTED,
            EventType.DATA_NOT_FOUND,
            EventType.SEARCH_REQUESTED,
            EventType.DATA_STORED,
            EventType.DATA_REJECTED,
            EventType.NODES_RECEIVED,
            EventType.SEEK_RECEIVED,
            EventType.CONNECTION_OPENED,
            EventType.CONNECTION_CLOSED,
            EventType.CONNECTION_FAILED,
            EventType.DATA_SENT,
            EventType.FETCH_ATTEMPTED,
        }
    )

    def __init__(
        self,
        name: ModuleName,
        queues: ModuleQueues,
        config: LibranetConfig,
        *,
        logger: Logger | None = None,
        clock: Callable[[], float] = time,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        super().__init__(name, queues, logger=logger, clock=clock, poll_interval=poll_interval)
        self._config = config
        self._database: StatsDatabase | None = None
        self._deriver: ListDeriver | None = None
        self._enricher: SearchEnricher | None = None
        self._derived_at = 0.0
        self._handlers: Mapping[EventType, Callable[[Message], None]] = {
            EventType.DATA_REQUESTED: self._on_data_requested,
            EventType.DATA_NOT_FOUND: self._on_data_not_found,
            EventType.SEARCH_REQUESTED: self._on_search_requested,
            EventType.DATA_STORED: self._on_data_stored,
            EventType.DATA_REJECTED: self._on_data_rejected,
            EventType.NODES_RECEIVED: self._on_nodes_received,
            EventType.SEEK_RECEIVED: self._on_seek_received,
            EventType.CONNECTION_OPENED: self._on_connection_opened,
            EventType.CONNECTION_CLOSED: self._on_connection_closed,
            EventType.CONNECTION_FAILED: self._on_connection_failed,
            EventType.DATA_SENT: self._on_data_sent,
            EventType.FETCH_ATTEMPTED: self._on_fetch_attempted,
        }

    @property
    def database(self) -> StatsDatabase:
        """The open database; only available while the module is running."""
        return _started(self._database)

    @property
    def deriver(self) -> ListDeriver:
        """The derived-list writer; only available while the module is running."""
        return _started(self._deriver)

    @property
    def enricher(self) -> SearchEnricher:
        """The search-cache enricher; only available while the module is running."""
        return _started(self._enricher)

    def on_start(self) -> None:
        """Open the database and write the derived lists before anything asks for them.

        The node identity is read here rather than passed across the process
        boundary, for the same reason the web server reads it: the private
        key stays on disk.
        """
        storage = self._config.storage
        identity = load_node_identity(self._config)
        self._database = StatsDatabase(storage.database_path, clock=self._clock)
        self._deriver = ListDeriver(
            self._database,
            storage,
            self._config.stats,
            self._config.network.advertised_endpoint(),
            identity.node_id,
        )
        self._enricher = SearchEnricher(
            self._database,
            SearchCache(
                storage.search_cache_dir,
                storage.search_cache_ttl_seconds,
                storage.hash_prefix_length,
                clock=self._clock,
            ),
            storage.search_max_results,
        )
        self.logger.info("Stats database open at %s", storage.database_path)
        self.derive()

    def on_idle(self) -> None:
        """Re-derive the lists once the configured interval has passed."""
        if self._clock() - self._derived_at >= self._config.stats.derive_interval_seconds:
            self.derive()

    def on_stop(self) -> None:
        """Close the database, whichever way the module stopped."""
        if self._database is not None:
            self._database.close()
            self._database = None

        self._deriver = None
        self._enricher = None

    def derive(self) -> None:
        """Rewrite the derived lists and announce a node list that changed."""
        self._derived_at = self._clock()
        lists = self.deriver.derive()

        if lists.node_list_changed:
            self.publish(EventType.NODE_LIST_UPDATED, {"path": str(lists.node_list)})
            self.logger.debug("Node list rewritten at %s", lists.node_list)

    def handle(self, message: Message) -> None:
        """Record one broadcast; a malformed one raises and :meth:`run` logs it."""
        self._handlers[event_of(message)](message)

    def _on_data_requested(self, message: Message) -> None:
        self.database.record_request(_content_id(message), external=bool(message["external"]))

    def _on_data_not_found(self, message: Message) -> None:
        self.database.record_seek(SeekKind.DATA, [str(_content_id(message))])

    def _on_search_requested(self, message: Message) -> None:
        prefix = normalize_prefix(message["prefix"])
        self.database.record_seek(SeekKind.SEARCH, [prefix])

        if self.enricher.enrich(prefix):
            self.logger.debug("Enriched the cached search for %s", prefix)

    def _on_data_stored(self, message: Message) -> None:
        content_id = _content_id(message)
        self.database.record_push(content_id)
        self.database.record_acquired(content_id)
        self.database.clear_seek(SeekKind.DATA, str(content_id))
        self.database.record_transfer(
            ContentId.parse(message["node_id"]), received=int(message["size"])
        )

    def _on_data_rejected(self, message: Message) -> None:
        self.database.record_push(_content_id(message))

    def _on_nodes_received(self, message: Message) -> None:
        self.database.record_endpoints(self._node_ids(message["nodes"]))

    def _on_seek_received(self, message: Message) -> None:
        node_id = ContentId.parse(message["node_id"])
        self.database.record_seek(SeekKind.DATA, message.get("data", ()), node_id)
        self.database.record_seek(SeekKind.SEARCH, message.get("search", ()), node_id)

    def _on_connection_opened(self, message: Message) -> None:
        self.database.record_connection_opened(
            ContentId.parse(message["node_id"]), message.get("endpoint")
        )

    def _on_connection_closed(self, message: Message) -> None:
        self.database.record_connection_closed(
            ContentId.parse(message["node_id"]), remote=bool(message.get("remote", False))
        )

    def _on_connection_failed(self, message: Message) -> None:
        self.database.record_connection_attempt(ContentId.parse(message["node_id"]))

    def _on_data_sent(self, message: Message) -> None:
        self.database.record_transfer(
            ContentId.parse(message["node_id"]), sent=int(message["size"])
        )

    def _on_fetch_attempted(self, message: Message) -> None:
        self.database.record_data_lookup(
            ContentId.parse(message["node_id"]), found=bool(message["found"])
        )

    def _node_ids(self, nodes: Mapping[str, str]) -> dict[str, ContentId]:
        """A received node list with its identifiers parsed, bad entries dropped.

        One unusable entry does not cost us the rest of a peer's list.
        """
        parsed: dict[str, ContentId] = {}

        for endpoint, node_id in nodes.items():
            try:
                parsed[endpoint] = ContentId.parse(node_id)

            except CasError:
                self.logger.debug("Ignoring node list entry %s: %r", endpoint, node_id)

        return parsed


def _started(component: _Component | None) -> _Component:
    """``component`` if the module is running, otherwise a clear failure.

    Everything the module works with is built in
    :meth:`~StatsModule.on_start` and released in
    :meth:`~StatsModule.on_stop`, so reaching one outside that window is a
    caller error rather than a state to handle.
    """
    if component is None:
        raise RuntimeError("The stats module is not running")

    return component


def _content_id(message: Message) -> ContentId:
    """The content identifier a message names in its ``algorithm`` and ``hash``."""
    return ContentId.create(message["algorithm"], message["hash"])


def stats_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`StatsModule`."""
    return StatsModule(name, queues, config)
