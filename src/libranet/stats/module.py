"""The stats module process: the node's memory of data and peers.

It is the only process that opens the SQLite file. Everything it records
arrives as a broadcast from another module, and everything it publishes leaves
as one of the derived list files plus a notice that the node or candidate list
changed, or in answer to the eviction module. Between derivations it does
nothing but record, so a burst of requests costs one small statement each.

The payloads it consumes, by event:

``data.requested``     ``{"algorithm", "hash", "external"}`` (Step 5)
``data.not_found``     ``{"algorithm", "hash"}`` (Step 5)
``data.search_requested`` ``{"prefix", "node_id"}``
                       (Step 5; ``node_id`` Phase 4 Step 93)
``data.search_answered`` ``{"prefix", "node_id", "results": [...]}``
                       (Phase 4 Step 93)
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
``data.shunned``       ``{"algorithm", "hash"}`` (Phase 4 Step 30)
``peers.connected``    ``{"direction", "node_ids": [...]}`` (Phase 5 Step 107)
``node.trust``         ``{"trusted": [...], "untrusted": [...], "only_trusted"}``
                       (Phase 5 Step 113)
``node.trust_requested`` ``{}`` (Phase 5 Step 113)
``karma.ratings_requested`` ``{}`` (Phase 6 Step 133)

A miss on ``GET /data/{algorithm}/{hash}`` and a ``GET /data/search/{prefix}``
are both requests this node could not answer, so each becomes an entry in
its own seek list until the content arrives or the entry ages out. Storing
content clears its entry. A miss for content the source of truth holds by
the time it is handled, as when its ``data.stored`` was broadcast first,
makes none. A search the seek list does not name yet has it written again at
once, rather than at the next derivation, so that the peers connected read it
on their next refresh and push what they hold that matches (HttpApi §10.7.2,
Phase 4 Step 93).

The hashes a peer answered a search with, as the connection manager asked
it (``data.search_answered``), are known from then on, held or not, and
added to the cached answer to that search, so that the next search lists
them (HttpApi §6).

``data.shunned`` names content this node will not hold, for good (Phase 4
Step 30). It is recorded, and no longer sought, and the shunned list
(:mod:`libranet.cas.shunned`) is written again at once, rather than at the
next derivation, for the modules that refuse shunned content. A miss for
shunned content is not sought, and a search is never given it.

What the node holds is what ``data.stored`` announced, with its size, less
what ``data.deleted`` reported gone. Asked by the eviction module for
content to let go of, it answers with the held content that scores highest
(:class:`~libranet.stats.priority.EvictionScorer`), best first, leaving
out what ``exclude`` names, until the sizes listed add up to ``bytes`` or
the list reaches ``storage.max_candidates`` entries::

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

It publishes ``nodes.updated``, with no payload, whenever the derived
candidate list (``storage.candidate_list_path``) or node list
(``storage.node_list_path``) changes, which is the connection manager's cue
to reconsider its peer mix (Step 11), and to pass on to its peers the nodes
the node list names anew (Phase 5 Step 114). The candidate list's shape is
documented in :mod:`libranet.stats.derivation`.

The peers connected each way, as the connection manager and the web server
name them in ``peers.connected``, are kept for the peer list a derivation
writes (:mod:`libranet.stats.peers`, Phase 5 Step 107). Stats asks for both
lists as it starts, with ``peers.connected_requested``, as the eviction
module does, since it may start again after they were last named.

Whom this node trusts and distrusts, as the web server says in ``node.trust``,
is kept in place of what was kept before, each identity's last state (Phase 5
Step 113). Asked by the web server as it starts, it says what it kept, for the
web server to go by until its own scores are built again
(:class:`~libranet.messaging.node_trust.NodeTrust`)::

    node.trust_stored      {"trusted": ["sha256/<hex>", ...], "untrusted": [...]}

Asked by the Karma module, every ``karma.rating_interval_seconds``, it
answers with each node connected outbound since it last answered, and how
much each served the node since then, for the Karma module to pay the best
(:class:`~libranet.stats.ratings.RatingCounts`, Phase 6 Step 133)::

    karma.ratings          {"servers": [{"node_id", "bytes_received", "requests_fulfilled"}, ...]}
"""

from __future__ import annotations
from dataclasses import dataclass
from logging import Logger
from time import time
from typing import Callable, ClassVar, Final, Mapping, TypeVar

from libranet.cas.content_id import ContentId
from libranet.cas.errors import CasError
from libranet.cas.store import CasStore, HeldObject
from libranet.config.models import LibranetConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.messaging.envelope import Message
from libranet.messaging.events import AddressSource, ConnectionDirection, EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.node_trust import NodeTrust
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName
from libranet.protocol.search import SearchCache, normalize_prefix
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import ListDeriver
from libranet.stats.enrichment import SearchEnricher
from libranet.stats.ratings import RatingCounts
from libranet.stats.schema import SeekKind

_Component = TypeVar("_Component")

# How a node list marks the entries in which its sender names itself.
_SENDERS_OWN: Final = frozenset({AddressSource.ADVERTISED, AddressSource.OBSERVED})


@dataclass(frozen=True)
class _Running:
    """What the module works with while it runs: built in on_start, released in on_stop."""

    node_id: ContentId
    database: StatsDatabase
    deriver: ListDeriver
    enricher: SearchEnricher
    ratings: RatingCounts

    @classmethod
    def open(
        cls, config: LibranetConfig, connected: ConnectedPeers, clock: Callable[[], float]
    ) -> _Running:
        """Read the node identity, open the database and what works on it, and count from now."""
        storage = config.storage
        identity = NodeIdentity.load(config)
        database = StatsDatabase(storage.database_path, clock=clock)
        return cls(
            identity.node_id,
            database,
            ListDeriver(
                database,
                storage,
                config.stats,
                config.network.own_endpoints(),
                identity.node_id,
                connected=connected,
                clock=clock,
            ),
            SearchEnricher(database, SearchCache.of(storage, clock=clock), storage.search_min_bits),
            RatingCounts(database.all_node_stats()),
        )


class StatsModule(ModuleBase):
    """Records what the node sees and derives the lists it publishes."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.DATA_REQUESTED,
            EventType.DATA_NOT_FOUND,
            EventType.SEARCH_REQUESTED,
            EventType.SEARCH_ANSWERED,
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
            EventType.DATA_SHUNNED,
            EventType.PEERS_CONNECTED,
            EventType.NODE_TRUST,
            EventType.NODE_TRUST_REQUESTED,
            EventType.KARMA_RATINGS_REQUESTED,
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
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_candidates: int | None = None,
    ) -> None:
        if max_candidates is None:
            max_candidates = config.storage.max_candidates

        if max_candidates < 1:
            raise ValueError(f"max_candidates must be at least 1, got {max_candidates}")

        super().__init__(
            name, queues, logger=logger, clock=clock, poll_interval_seconds=poll_interval_seconds
        )
        self._config = config
        self._max_candidates = max_candidates
        self._store = CasStore.source_of_truth(config.storage)
        self._running: _Running | None = None
        self._connected = ConnectedPeers()
        self._derived_at = 0.0
        self._route(
            {
                EventType.DATA_REQUESTED: self._on_data_requested,
                EventType.DATA_NOT_FOUND: self._on_data_not_found,
                EventType.SEARCH_REQUESTED: self._on_search_requested,
                EventType.SEARCH_ANSWERED: self._on_search_answered,
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
                EventType.DATA_SHUNNED: self._on_data_shunned,
                EventType.PEERS_CONNECTED: self._on_peers_connected,
                EventType.NODE_TRUST: self._on_node_trust,
                EventType.NODE_TRUST_REQUESTED: self._on_node_trust_requested,
                EventType.KARMA_RATINGS_REQUESTED: self._on_ratings_requested,
            }
        )

    @classmethod
    def of(cls, name: ModuleName, config: LibranetConfig, queues: ModuleQueues) -> StatsModule:
        """:data:`~libranet.supervision.specs.ModuleFactory` for this class."""
        return cls(name, queues, config)

    @property
    def database(self) -> StatsDatabase:
        """The open database; only available while the module is running."""
        return _started(self._running).database

    @property
    def deriver(self) -> ListDeriver:
        """The derived-list writer; only available while the module is running."""
        return _started(self._running).deriver

    @property
    def enricher(self) -> SearchEnricher:
        """The search-cache enricher; only available while the module is running."""
        return _started(self._running).enricher

    def on_start(self) -> None:
        """Open the database, write the lists before they are asked for, and ask who is connected.

        The node identity is read here rather than passed across the process
        boundary, for the same reason the web server reads it: the private
        key stays on disk.
        """
        self._running = _Running.open(self._config, self._connected, self._clock)
        self.logger.info("Stats database open at %s", self._config.storage.database_path)
        self.derive()
        self.publish(EventType.PEERS_CONNECTED_REQUESTED)

    def on_idle(self) -> None:
        """Re-derive the lists once the configured interval has passed."""
        if self._clock() - self._derived_at >= self._config.stats.derive_interval_seconds:
            self.derive()

    def on_stop(self) -> None:
        """Close the database, whichever way the module stopped."""
        if self._running is not None:
            self._running.database.close()
            self._running = None

    def derive(self) -> None:
        """Rewrite the derived lists and announce a node or candidate list that changed."""
        self._derived_at = self._clock()
        lists = self.deriver.derive()

        if lists.candidate_list_changed:
            self.logger.debug("Candidate list rewritten at %s", lists.candidate_list)

        if lists.node_list_changed:
            self.logger.debug("Node list rewritten at %s", lists.node_list)

        if lists.candidate_list_changed or lists.node_list_changed:
            self.publish(EventType.NODE_LIST_UPDATED)

    def _on_data_requested(self, message: Message) -> None:
        self.database.record_request(
            ContentId.from_fields(message), external=bool(message["external"])
        )

    def _on_data_not_found(self, message: Message) -> None:
        """Seek what was missed, unless it is held by now, as when its ``data.stored`` came first.

        Messages from two modules arrive in no set order. Shunned content is
        never sought.
        """
        content_id = ContentId.from_fields(message)

        if self._store.exists(content_id):
            self.logger.debug("%s is held, so is not sought", content_id)
            return

        if self.database.is_shunned(content_id):
            self.logger.debug("%s is shunned, so is not sought", content_id)
            return

        self.database.record_seek(SeekKind.DATA, [str(content_id)])

    def _on_search_requested(self, message: Message) -> None:
        """Seek ``prefix``, listing it at once if it is new, and add what is known to its answer."""
        prefix = normalize_prefix(message["prefix"])
        new = not self.database.is_sought(SeekKind.SEARCH, prefix)
        self.database.record_seek(SeekKind.SEARCH, [prefix])

        if new and self.deriver.write_seek_list():
            self.logger.debug("Seek list rewritten for the search for %s", prefix)

        self._enrich(prefix)

    def _on_search_answered(self, message: Message) -> None:
        """Know what a peer answered a search with, and add it to the cached answer."""
        prefix = normalize_prefix(message["prefix"])
        self.database.record_heard_of(ContentId.parse(text) for text in message["results"])
        self._enrich(prefix)

    def _enrich(self, prefix: str) -> None:
        if self.enricher.enrich(prefix):
            self.logger.debug("Enriched the cached search for %s", prefix)

    def _on_data_stored(self, message: Message) -> None:
        content_id = ContentId.from_fields(message)
        size_bytes = int(message["size"])
        self.database.record_push(content_id)
        self.database.record_acquired(content_id, size_bytes)
        self.database.clear_seek(SeekKind.DATA, str(content_id))
        self.database.record_transfer(
            ContentId.parse(message["node_id"]), received_bytes=size_bytes
        )

    def _on_data_rejected(self, message: Message) -> None:
        self.database.record_push(ContentId.from_fields(message))

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
            ContentId.parse(message["node_id"]), sent_bytes=int(message["size"])
        )

    def _on_fetch_attempted(self, message: Message) -> None:
        self.database.record_data_lookup(
            ContentId.parse(message["node_id"]), found=bool(message["found"])
        )

    def _on_data_deleted(self, message: Message) -> None:
        self.database.record_deleted(ContentId.from_fields(message))

    def _on_candidates_requested(self, message: Message) -> None:
        """Tell the eviction module what to let go of first, enough to free the bytes it asks."""
        wanted = int(message["bytes"])
        exclude = {ContentId.parse(text) for text in message.get("exclude", ())}
        chosen: list[HeldObject] = []
        covered = 0

        for held in self.database.eviction_order(
            _started(self._running).node_id, self._max_candidates, exclude
        ):
            if not self._store.exists(held.content_id):
                self.database.record_deleted(held.content_id)
                self.logger.info("%s is gone from the store, so no longer held", held.content_id)
                continue

            chosen.append(held)
            covered += held.size_bytes

            if covered >= wanted:
                break

        self.publish(
            EventType.EVICTION_CANDIDATES,
            {"objects": [{**held.content_id.fields(), "size": held.size_bytes} for held in chosen]},
        )

    def _on_app_accessed(self, message: Message) -> None:
        self.database.record_app_access(ContentId.parse(message["bundle"]))

    def _on_data_shunned(self, message: Message) -> None:
        """Shun the content, stop seeking it, and list it for the modules refusing it."""
        content_id = ContentId.from_fields(message)

        if not self.database.record_shunned(content_id):
            self.logger.debug("%s was shunned already", content_id)
            return

        self.database.clear_seek(SeekKind.DATA, str(content_id))
        self.deriver.write_shunned_list()
        self.logger.info("Shunned %s", content_id)

    def _on_peers_connected(self, message: Message) -> None:
        """Keep the peers connected one way, in place of those named before, to list and rate."""
        self._connected.named_in(message)
        _started(self._running).ratings.connected(
            self._connected.node_ids(direction=ConnectionDirection.OUTBOUND)
        )

    def _on_node_trust(self, message: Message) -> None:
        """Keep the state of each identity the node trusts or distrusts, in place of the last."""
        trust = NodeTrust.from_payload(_started(self._running).node_id, message)
        self.database.record_trust(trust.states)

    def _on_node_trust_requested(self, _message: Message) -> None:
        """Tell the web server the state kept of each identity the node trusts or distrusts."""
        kept = NodeTrust(_started(self._running).node_id, self.database.trust_states())
        self.publish(EventType.NODE_TRUST_STORED, kept.stored_payload())

    def _on_ratings_requested(self, _message: Message) -> None:
        """Tell the Karma module how each server dialed since it last asked served the node."""
        ratings = _started(self._running).ratings.answer(
            self.database.all_node_stats(),
            self._connected.node_ids(direction=ConnectionDirection.OUTBOUND),
        )
        self.publish(EventType.KARMA_RATINGS, ratings.payload())
        self.logger.debug("Rated %d servers for the Karma module", len(ratings.servers))

    def _on_reclaim_requested(self, _message: Message) -> None:
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
