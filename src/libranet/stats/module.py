"""The stats module process: the node's memory of data and peers.

It is the only process that opens the SQLite file. Everything it records
arrives as a broadcast from another module, and everything it publishes
leaves as one of the derived list files plus a notice that the candidate
list changed, or in answer to the eviction module. Between derivations
it does nothing but record, so a burst of requests costs one small statement
each.

The payloads it consumes, by event:

``data.requested``     ``{"algorithm", "hash", "external"}`` (Step 5)
``data.not_found``     ``{"algorithm", "hash"}`` (Step 5)
``data.search_requested`` ``{"prefix", "cache_path"}`` (Step 5)
``data.stored``        ``{"algorithm", "hash", "node_id", "size"}`` (Step 7)
``data.rejected``      ``{"algorithm", "hash", "node_id"}`` (Step 7)
``nodes.received``     ``{"nodes": {endpoint: node id}, "sources": {endpoint: source}}``
                       (Steps 9 and 11; Phase 2 Step 23)
``seek.received``      ``{"node_id", "data": [...], "search": [...]}`` (Step 9)
``connection.opened``  ``{"node_id", "endpoint"}`` (Step 11)
``connection.closed``  ``{"node_id", "remote"}`` (Step 11)
``connection.failed``  ``{"node_id", "endpoint"}`` (Step 11)
``node.unreached``     ``{"node_id"}`` (Phase 2 Step 26)
``address.verified``   ``{"node_id", "endpoint"}`` (Phase 2 Step 23)
``data.sent``          ``{"algorithm", "hash", "node_id", "size"}`` (Step 11)
``fetch.attempted``    ``{"algorithm", "hash", "node_id", "found"}`` (Step 11)
``data.deleted``       ``{"algorithm", "hash", "size"}`` (Step 15)
``eviction.candidates_requested`` ``{"bytes", "exclude"}`` (Phase 2 Step 28)
``app.accessed``       ``{"bundle"}`` (Phase 2 Step 29)
``resolved.reclaim_requested`` ``{}`` (Phase 2 Step 29)

A miss on ``GET /data/{algorithm}/{hash}`` and a ``GET /data/search/{prefix}``
are both requests this node could not answer, so each becomes an entry in
its own seek list until the content arrives or the entry ages out. Storing
content clears its entry.

What the node holds is what ``data.stored`` announced, with its size, less
what ``data.deleted`` reported gone. Asked by the eviction module for
content to let go of, it answers with the held content that scores highest
(:class:`~libranet.eviction.priority.EvictionScorer`), best first, leaving
out what ``exclude`` names, until the sizes listed add up to ``bytes`` or
the list reaches ``max_candidates`` entries::

    eviction.candidates    {"objects": [{"algorithm", "hash", "size"}, ...]}

Content no longer in the source of truth, though no ``data.deleted`` said
so, as when its file was removed by hand, is recorded as deleted rather than
listed, so eviction is never sent after content that is gone.

Asked by the eviction module to reclaim resolved application files, it
tells the unbundler which bundles an application has been served from
within ``storage.resolved_idle_seconds``, whose files are to be kept; the
unbundler deletes the rest (Phase 2 Step 29)::

    resolved.reclaim       {"keep": ["sha256/<hex>", ...]}

The bundles to keep are named rather than those to delete, so resolved
files this module has no record of, as those resolved before its database
was, are deleted too.

Addresses are kept per node (Phase 2 Step 23). Each entry of a received
node list is an address learned from the source ``sources`` names for it,
or else relayed from another node. ``connection.opened`` and
``address.verified`` each say an address reached its node; only the first
counts a connection, since ``address.verified`` is a connection closed at
once, its node being connected already by another address.
``connection.failed`` is an attempt that did not reach the node at that
address, whether nothing answered or some other node did.

``node.unreached`` is an attempt that dialed a node at every address it was
to be tried at and reached it at none (Phase 2 Step 26). After
``stats.max_node_failures`` of them in a row, the node is given up on: it is
left out of the candidate list, derived again at once, until
``stats.node_cool_off_seconds`` have passed since the last, and then tried
once more. Its addresses and statistics are kept. Reaching the node starts
the count again, and so does hearing from it: a node list whose sender
names itself, the entries ``sources`` marks ``advertised`` or ``observed``.
A node list that merely relays the node does not.

It publishes ``nodes.updated`` — ``{"path": "<candidate list file>"}`` —
whenever the derived candidate list changes, which is the connection
manager's cue to reconsider its peer mix (Step 11). The candidate list's
shape is documented in :mod:`libranet.stats.derivation`.
"""

from __future__ import annotations
from logging import Logger
from time import time
from typing import Callable, ClassVar, Final, Mapping, TypeVar

from libranet.cas.content_id import ContentId
from libranet.cas.errors import CasError
from libranet.cas.store import source_of_truth_store
from libranet.config.models import LibranetConfig
from libranet.eviction.priority import HeldObject
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, event_of
from libranet.messaging.events import AddressSource, EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import ListDeriver
from libranet.stats.enrichment import SearchEnricher
from libranet.stats.schema import SeekKind
from libranet.webserver.search import SearchCache, normalize_prefix

_Component = TypeVar("_Component")

# How a node list marks the entries in which its sender names itself.
_SENDERS_OWN: Final = frozenset({AddressSource.ADVERTISED, AddressSource.OBSERVED})

# Provisional default: the most objects one answer to the eviction module
# lists. It asks again when it has handed them all off.
DEFAULT_MAX_CANDIDATES: Final = 256


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
            EventType.NODE_UNREACHED,
            EventType.ADDRESS_VERIFIED,
            EventType.DATA_SENT,
            EventType.FETCH_ATTEMPTED,
            EventType.DATA_DELETED,
            EventType.EVICTION_CANDIDATES_REQUESTED,
            EventType.APP_ACCESSED,
            EventType.RESOLVED_RECLAIM_REQUESTED,
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
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
    ) -> None:
        if max_candidates < 1:
            raise ValueError(f"max_candidates must be at least 1, got {max_candidates}")

        super().__init__(name, queues, logger=logger, clock=clock, poll_interval=poll_interval)
        self._config = config
        self._max_candidates = max_candidates
        self._store = source_of_truth_store(config.storage)
        self._node_id: ContentId | None = None
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
            EventType.NODE_UNREACHED: self._on_node_unreached,
            EventType.ADDRESS_VERIFIED: self._on_address_verified,
            EventType.DATA_SENT: self._on_data_sent,
            EventType.FETCH_ATTEMPTED: self._on_fetch_attempted,
            EventType.DATA_DELETED: self._on_data_deleted,
            EventType.EVICTION_CANDIDATES_REQUESTED: self._on_candidates_requested,
            EventType.APP_ACCESSED: self._on_app_accessed,
            EventType.RESOLVED_RECLAIM_REQUESTED: self._on_reclaim_requested,
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
        self._node_id = identity.node_id
        self._database = StatsDatabase(storage.database_path, clock=self._clock)
        self._deriver = ListDeriver(
            self._database,
            storage,
            self._config.stats,
            self._config.network.own_endpoints(),
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
        """Rewrite the derived lists and announce a candidate list that changed."""
        self._derived_at = self._clock()
        lists = self.deriver.derive()

        if lists.candidate_list_changed:
            self.publish(EventType.NODE_LIST_UPDATED, {"path": str(lists.candidate_list)})
            self.logger.debug("Candidate list rewritten at %s", lists.candidate_list)

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
        size = int(message["size"])
        self.database.record_push(content_id)
        self.database.record_acquired(content_id, size)
        self.database.clear_seek(SeekKind.DATA, str(content_id))
        self.database.record_transfer(ContentId.parse(message["node_id"]), received=size)

    def _on_data_rejected(self, message: Message) -> None:
        self.database.record_push(_content_id(message))

    def _on_nodes_received(self, message: Message) -> None:
        sources: Mapping[str, str] = message.get("sources", {})
        learned: dict[AddressSource, dict[str, ContentId]] = {}

        for endpoint, node_id in self._node_ids(message["nodes"]).items():
            source = AddressSource(sources.get(endpoint, AddressSource.RELAYED))
            learned.setdefault(source, {})[endpoint] = node_id

        for source, addresses in learned.items():
            self.database.record_addresses(addresses, source)

        self.database.record_heard_from(
            {
                node_id
                for source, addresses in learned.items()
                if source in _SENDERS_OWN
                for node_id in addresses.values()
            }
        )

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
        node_id = ContentId.parse(message["node_id"])
        self.database.record_connection_attempt(node_id)
        self.database.record_address_failed(node_id, message["endpoint"])

    def _on_node_unreached(self, message: Message) -> None:
        """Count the failure, and give up on the node at once if that makes enough in a row."""
        node_id = ContentId.parse(message["node_id"])
        failures = self.database.record_node_unreached(node_id)
        stats = self._config.stats

        if failures >= stats.max_node_failures:
            self.logger.info(
                "Giving up on %s for %s seconds after %d failed attempts in a row",
                node_id,
                stats.node_cool_off_seconds,
                failures,
            )
            self.derive()

    def _on_address_verified(self, message: Message) -> None:
        self.database.record_address_worked(
            ContentId.parse(message["node_id"]), message["endpoint"]
        )

    def _on_data_sent(self, message: Message) -> None:
        self.database.record_transfer(
            ContentId.parse(message["node_id"]), sent=int(message["size"])
        )

    def _on_fetch_attempted(self, message: Message) -> None:
        self.database.record_data_lookup(
            ContentId.parse(message["node_id"]), found=bool(message["found"])
        )

    def _on_data_deleted(self, message: Message) -> None:
        self.database.record_deleted(_content_id(message))

    def _on_candidates_requested(self, message: Message) -> None:
        """Tell the eviction module what to let go of first, enough to free the bytes it asks."""
        wanted = int(message["bytes"])
        exclude = {ContentId.parse(text) for text in message.get("exclude", ())}
        chosen: list[HeldObject] = []
        covered = 0

        for held in self.database.eviction_order(
            _started(self._node_id), self._max_candidates, exclude
        ):
            if not self._store.exists(held.content_id):
                self.database.record_deleted(held.content_id)
                self.logger.info("%s is gone from the store, so no longer held", held.content_id)
                continue

            chosen.append(held)
            covered += held.size

            if covered >= wanted:
                break

        self.publish(
            EventType.EVICTION_CANDIDATES,
            {
                "objects": [
                    {
                        "algorithm": held.content_id.algorithm,
                        "hash": held.content_id.hash,
                        "size": held.size,
                    }
                    for held in chosen
                ]
            },
        )

    def _on_app_accessed(self, message: Message) -> None:
        self.database.record_app_access(ContentId.parse(message["bundle"]))

    def _on_reclaim_requested(self, message: Message) -> None:
        """Tell the unbundler whose resolved files to keep: those of applications used lately."""
        since = self._clock() - self._config.storage.resolved_idle_seconds
        keep = self.database.apps_accessed_since(since)
        self.publish(EventType.RESOLVED_RECLAIM, {"keep": [str(bundle) for bundle in keep]})

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
