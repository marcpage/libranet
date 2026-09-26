"""The connection manager module process (Phase 1 Step 11).

It owns every outgoing peer connection. It keeps up the peer mix of
HighLevelDesign §4.6, with its second set among this node's neighbors
(:mod:`~libranet.connections.peer_mix`), holds the first-contact exchange
with each new peer and refreshes it while connected
(:mod:`~libranet.connections.peer_exchange`), and fetches content on the
fetcher's behalf.

The mix is tended when something changes rather than on a timer: when the
module starts, when the stats module announces a new candidate list, when a
connection opens, fails, or closes, and when a node's retry delay ends. A
node is dialed at each of its endpoints in turn, in the order the candidate
list gives them, until one reaches it (Phase 2 Step 23). The node rests for
``retry_delay_seconds`` once none has, or after its connection closes, so a
peer that is down or turning this node away is not dialed again and again.
Meanwhile another peer in the same bucket is dialed, if one is known. A seed
whose node id is unknown rests by its endpoint instead. Connecting and
talking to peers happen on background threads, so the receive loop never
waits on the network.

Each address a peer was observed at (``nodes.received`` marking it
``observed``), whether by this module or by the web server, is looked up in
reverse DNS on a worker thread, unless ``peers.reverse_dns`` is off, and any
names found are published as more addresses of that peer
(:mod:`~libranet.connections.reverse_dns`).

An endpoint that answers as a node other than the one expected did not reach
the node expected there, and did reach the node that answered. That node is
taken into the mix if it is not connected yet. If it is, the new connection
is closed, and the endpoint is not dialed again while the first connection
lasts; after that, it is a route to that node like any other.

A fetch (``fetch.requested`` ``{"algorithm", "hash"}``) asks the connected
peers one at a time, those whose node id shares the most leading bits with
the content's hash first (HighLevelDesign §4.7), until one sends it. Only
peers already connected are asked, each once. The outcome is published for
the fetcher::

    fetch.succeeded  {"algorithm", "hash", "node_id"}
    fetch.failed     {"algorithm", "hash"}

``fetch.succeeded`` means the content went to the validator from ``node_id``;
``fetch.failed`` means no connected peer had it.

A hand-off (``eviction.notice`` ``{"algorithm", "hash", "copies"}``) pushes
content the eviction module means to delete to the connected peers, best
match first, until ``copies`` of them accept it (HighLevelDesign §4.5). Only
peers already connected are offered it, each once. The eviction module is
told which accepted, best match first, and deletes its copy only if there
are enough::

    eviction.acknowledged  {"algorithm", "hash", "node_ids": ["sha256/<hex>", ...]}

New content (``data.stored`` ``{"algorithm", "hash", "node_id"}``), whether
this node created it or received it from ``node_id``, is pushed to the one
connected peer whose node id best matches its hash (HttpApi §7.4). It goes
there even when this node's own id matches the hash better, since that peer
may be connected to a better match still, but not when that peer is where it
came from. A peer that cannot be reached is passed over for the next best;
one that refuses the content is not. Content that finds no peer to go to
waits, in memory, until a connection opens. ``data.stored`` announces only
content this node did not hold before, so what it held already is never
pushed on.

For the stats module it publishes::

    connection.opened  {"node_id", "endpoint"}
    connection.closed  {"node_id", "remote"}
    connection.failed  {"node_id", "endpoint"}
    address.verified   {"node_id", "endpoint"}

A connection is opened once the peer has proven its node id, and ``remote``
is true unless this node chose to close it. A failed attempt is published
for each endpoint that did not reach the node expected there, whether
nothing answered or some other node did, and only for a node whose id was
known beforehand. ``address.verified`` is an endpoint that reached a node
already connected at another, whose connection was closed at once, so it
counts as no connection.
"""

from __future__ import annotations
from dataclasses import dataclass
from functools import partial
from logging import Logger
from queue import SimpleQueue
from threading import Lock, Thread
from time import time
from typing import Callable, ClassVar, Final, Mapping

from libranet.cas.content_id import ContentId
from libranet.cas.errors import ContentNotFoundError
from libranet.cas.prefix import nearest
from libranet.cas.store import source_of_truth_store
from libranet.config.models import LibranetConfig
from libranet.config.seeds import SeedError, load_seed_peers
from libranet.connections.candidates import Candidate, candidate_list, seed_candidates
from libranet.connections.endpoints import peer_address
from libranet.connections.peer_exchange import PeerExchange
from libranet.connections.peer_mix import PeerMix
from libranet.connections.peer_session import PeerSession
from libranet.connections.reverse_dns import ResolveNames, ReverseLookup, host_names
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message, event_of
from libranet.messaging.events import AddressSource, EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName

# Fetches under way at once. Each asks one peer at a time, so this also
# bounds the requests fetching adds to all connections together.
FETCH_WORKERS: Final = 8

# Pushes of new content under way at once. Each sends one body to one peer
# at a time, so this also bounds the bodies pushing holds in memory.
PUSH_WORKERS: Final = 8


@dataclass(frozen=True)
class _NewContent:
    """Content this node has just stored, and the node it came from, which may be this one."""

    content_id: ContentId
    source: ContentId


@dataclass
class _Peer:
    """A connected peer, and when its seek list is next due to be fetched again.

    ``busy`` is set while a conversation with it is under way, so a refresh
    never overlaps another.
    """

    session: PeerSession
    refresh_at: float
    busy: bool = True


class ConnectionsModule(ModuleBase):
    """Keeps this node connected to a spread of peers and fetches from them."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset(
        {
            EventType.NODE_LIST_UPDATED,
            EventType.NODES_RECEIVED,
            EventType.FETCH_REQUESTED,
            EventType.EVICTION_NOTICE,
            EventType.DATA_STORED,
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
        resolve_names: ResolveNames = host_names,
    ) -> None:
        super().__init__(name, queues, logger=logger, clock=clock, poll_interval=poll_interval)
        self._config = config
        self._source_of_truth = source_of_truth_store(config.storage)
        self._exchange: PeerExchange | None = None
        self._resolve_names = resolve_names
        self._lookup: ReverseLookup | None = None
        self._seeds: list[Candidate] = []
        # Everything below is shared with the background threads, under the lock.
        self._lock = Lock()
        self._running = False
        self._candidates: list[Candidate] = []
        self._peers: dict[ContentId, _Peer] = {}
        # Candidates being dialed, by their keys.
        self._pending: dict[ContentId | str, Candidate] = {}
        # Candidate keys not to be dialed again until the time given.
        self._resting: dict[ContentId | str, float] = {}
        # Endpoints that turned out to be this node itself.
        self._own_endpoints: set[str] = set()
        # Endpoints that reached a node connected at another endpoint, with
        # that node: not dialed again while that connection lasts.
        self._duplicates: dict[str, ContentId] = {}
        self._fetching: set[ContentId] = set()
        self._fetches: SimpleQueue[ContentId | None] = SimpleQueue()
        self._handing_off: set[ContentId] = set()
        self._pushes: SimpleQueue[_NewContent | None] = SimpleQueue()
        # New content no peer was connected to take, by content id, with the
        # node each came from: pushed once a connection opens.
        self._unpushed: dict[ContentId, ContentId] = {}
        self._handlers: Mapping[EventType, Callable[[Message], None]] = {
            EventType.NODE_LIST_UPDATED: self._on_node_list_updated,
            EventType.NODES_RECEIVED: self._on_nodes_received,
            EventType.FETCH_REQUESTED: self._on_fetch_requested,
            EventType.EVICTION_NOTICE: self._on_eviction_notice,
            EventType.DATA_STORED: self._on_data_stored,
        }

    @property
    def exchange(self) -> PeerExchange:
        """What this node says to peers; only available once the module has started."""
        if self._exchange is None:
            raise RuntimeError("The connections module is not running")

        return self._exchange

    @property
    def connected(self) -> dict[ContentId, str]:
        """The peers connected right now, by node id, with the endpoint each was reached at."""
        with self._lock:
            return {node_id: peer.session.endpoint for node_id, peer in self._peers.items()}

    def on_start(self) -> None:
        """Load this node's identity and start connecting to peers.

        The node identity is read here rather than passed across the process
        boundary, for the same reason the web server reads it: the private
        key stays on disk.
        """
        self._exchange = PeerExchange(
            load_node_identity(self._config), self._config, self.publish, self.logger
        )
        self._seeds = self._load_seeds()

        with self._lock:
            self._running = True

        for index in range(FETCH_WORKERS):
            Thread(target=self._fetch_loop, name=f"{self.name}-fetch-{index}", daemon=True).start()

        for index in range(PUSH_WORKERS):
            Thread(target=self._push_loop, name=f"{self.name}-push-{index}", daemon=True).start()

        if self._config.peers.reverse_dns:
            self._lookup = ReverseLookup(
                self.publish,
                self.logger,
                self._config.peers.reverse_dns_cache_seconds,
                resolve=self._resolve_names,
                clock=self._clock,
            )
            self._lookup.start(f"{self.name}-reverse-dns")

        self._reload_candidates()
        self._maintain()

    def on_idle(self) -> None:
        """Refresh peers whose seek list is due, and dial again once candidates have rested."""
        now = self._clock()

        with self._lock:
            rested = [key for key, until in self._resting.items() if until <= now]

            for key in rested:
                del self._resting[key]

            due = [
                peer for peer in self._peers.values() if not peer.busy and peer.refresh_at <= now
            ]

            for peer in due:
                peer.busy = True

        for peer in due:
            self._start("refresh", partial(self._talk, peer, self.exchange.refresh))

        if rested:
            self._maintain()

    def on_stop(self) -> None:
        """Close every connection, whichever way the module stopped.

        A connection still being opened is closed as soon as it opens, and
        is never reported as opened.
        """
        with self._lock:
            running = self._running
            self._running = False
            peers = list(self._peers.values())

        if running:  # tell worker threads to stop
            for _ in range(FETCH_WORKERS):
                self._fetches.put(None)

            for _ in range(PUSH_WORKERS):
                self._pushes.put(None)

            if self._lookup is not None:
                self._lookup.stop()

        for peer in peers:
            peer.session.close()

    def handle(self, message: Message) -> None:
        """React to one subscribed broadcast; a malformed one raises and :meth:`run` logs it.

        An event with no handler raises too, rather than being taken for one
        it is not.
        """
        self._handlers[event_of(message)](message)

    def _on_node_list_updated(self, message: Message) -> None:
        self._reload_candidates()
        self._maintain()

    def _on_nodes_received(self, message: Message) -> None:
        """Look up names for the addresses peers were observed at."""
        if self._lookup is None:
            return

        nodes: Mapping[str, str] = message["nodes"]

        for endpoint, source in message.get("sources", {}).items():
            if source == AddressSource.OBSERVED:
                self._lookup.look_up(nodes[endpoint], endpoint)

    def _on_fetch_requested(self, message: Message) -> None:
        content_id = ContentId.create(message["algorithm"], message["hash"])

        with self._lock:
            if content_id in self._fetching:
                return

            self._fetching.add(content_id)

        self._fetches.put(content_id)

    def _on_eviction_notice(self, message: Message) -> None:
        """Hand the content off on a thread of its own.

        The eviction module bounds how many it asks for at once.
        """
        content_id = ContentId.create(message["algorithm"], message["hash"])
        copies = int(message["copies"])

        with self._lock:
            if content_id in self._handing_off:
                return

            self._handing_off.add(content_id)

        self._start("hand-off", partial(self._hand_off, content_id, copies))

    def _on_data_stored(self, message: Message) -> None:
        self._pushes.put(
            _NewContent(
                ContentId.create(message["algorithm"], message["hash"]),
                ContentId.parse(message["node_id"]),
            )
        )

    def _load_seeds(self) -> list[Candidate]:
        try:
            return seed_candidates(load_seed_peers(self._config.peers.seed_file))

        except SeedError as error:
            self.logger.warning("Seed list unavailable: %s", error)
            return []

    def _reload_candidates(self) -> None:
        """Read the candidate list afresh, falling back to the seeds while it names no peer.

        Endpoints this node cannot dial are left out, and so is a candidate
        left with none.
        """
        candidates = candidate_list(self._config.storage.candidate_list_path, self.exchange.node_id)
        dialable = [
            usable
            for candidate in candidates or self._seeds
            if (usable := candidate.keeping(_dialable)) is not None
        ]

        with self._lock:
            self._candidates = dialable

    def _maintain(self) -> None:
        """Dial whatever the peer mix is short of."""
        with self._lock:
            if not self._running:
                return

            chosen = PeerMix(self._config.peers, self.exchange.node_id).choose(
                self._available(),
                [*self._peers, *(candidate.node_id for candidate in self._pending.values())],
            )

            for candidate in chosen:
                self._pending[candidate.key] = candidate

        for candidate in chosen:
            self._start("connect", partial(self._connect, candidate))

    def _available(self) -> list[Candidate]:
        """The candidates that may be dialed now, each with only the endpoints that may be.

        A candidate being dialed or resting may not. Nor may an endpoint
        that is this node, is in use by a connection or one being opened, or
        reaches a node connected at another endpoint. Called under the lock.
        """
        unusable = (
            self._own_endpoints
            | {peer.session.endpoint for peer in self._peers.values()}
            | {endpoint for pending in self._pending.values() for endpoint in pending.endpoints}
            | {endpoint for endpoint, node_id in self._duplicates.items() if node_id in self._peers}
        )
        available: list[Candidate] = []

        for candidate in self._candidates:
            if candidate.key in self._pending or candidate.key in self._resting:
                continue

            usable = candidate.keeping(lambda endpoint: endpoint not in unusable)

            if usable is not None:
                available.append(usable)

        return available

    def _start(self, purpose: str, job: Callable[[], None]) -> None:
        """Run ``job`` on its own thread, which never holds up the process exiting."""
        Thread(target=job, name=f"{self.name}-{purpose}", daemon=True).start()

    def _connect(self, candidate: Candidate) -> None:
        """Dial ``candidate``, take a peer it reaches into the mix, then finish the first-contact exchange.

        The candidate rests if no peer was taken in, unless its node was
        connected meanwhile at another endpoint.
        """
        peer = self._walk(candidate)

        with self._lock:
            self._pending.pop(candidate.key, None)

            if peer is None and candidate.node_id not in self._peers:
                self._resting[candidate.key] = (
                    self._clock() + self._config.peers.retry_delay_seconds
                )

        self._maintain()

        if peer is not None:
            self._push_unpushed()
            self._talk(peer, self.exchange.first_contact)

    def _walk(self, candidate: Candidate) -> _Peer | None:
        """Dial ``candidate``'s endpoints in turn until one reaches a peer taken into the mix.

        It stops early at an endpoint that reaches the node expected, even if
        that node is not taken in, and when the module is stopping.
        """
        for endpoint in candidate.endpoints:
            try:
                session = self.exchange.open(endpoint)

            except Exception as error:
                self._attempt_failed(candidate, endpoint, error)
                continue

            if candidate.node_id is not None and session.node_id != candidate.node_id:
                self.logger.info(
                    "%s answered as %s, not %s", endpoint, session.node_id, candidate.node_id
                )
                self._publish_failed(candidate.node_id, endpoint)

            peer = self._admit(session)

            if peer is not None or session.node_id == candidate.node_id:
                return peer

            with self._lock:
                if not self._running:
                    return None

        return None

    def _attempt_failed(self, candidate: Candidate, endpoint: str, error: Exception) -> None:
        if isinstance(error, OSError):
            self.logger.info("Could not connect to %s: %s", endpoint, error)

        else:
            self.logger.error("Connecting to %s failed", endpoint, exc_info=error)

        if candidate.node_id is not None:
            self._publish_failed(candidate.node_id, endpoint)

    def _publish_failed(self, node_id: ContentId, endpoint: str) -> None:
        """Tell stats that dialing ``endpoint`` did not reach ``node_id``."""
        self.publish(EventType.CONNECTION_FAILED, {"node_id": str(node_id), "endpoint": endpoint})

    def _admit(self, session: PeerSession) -> _Peer | None:
        """Take a peer that has proven its node id into the mix, unless it is not wanted.

        It is not wanted if it is this node, if that node is already
        connected, or if the module is stopping. A node already connected
        was reached at this endpoint all the same, which stats is told, and
        the endpoint is not dialed again while the first connection lasts.
        """
        now = self._clock()
        peer: _Peer | None = None
        duplicate = False

        with self._lock:
            if not self._running:
                refusal = "the module is stopping"

            elif session.node_id == self.exchange.node_id:
                self._own_endpoints.add(session.endpoint)
                refusal = "it is this node"

            elif session.node_id in self._peers:
                self._duplicates[session.endpoint] = session.node_id
                duplicate = True
                refusal = f"{session.node_id} is already connected"

            else:
                peer = _Peer(session, now + self._config.peers.seek_refresh_seconds)
                self._peers[session.node_id] = peer
                refusal = ""

        if peer is None:
            self.logger.info("Closing the connection to %s: %s", session.endpoint, refusal)
            session.close()

            if duplicate:
                self.publish(
                    EventType.ADDRESS_VERIFIED,
                    {"node_id": str(session.node_id), "endpoint": session.endpoint},
                )

            return None

        self.logger.info("Connected to %s at %s", session.node_id, session.endpoint)
        self.publish(
            EventType.CONNECTION_OPENED,
            {"node_id": str(session.node_id), "endpoint": session.endpoint},
        )
        session.when_closed(partial(self._closed, peer))
        return peer

    def _closed(self, peer: _Peer) -> None:
        """Take a peer whose connection has closed out of the mix, and replace it."""
        session = peer.session
        remote = not session.closed_locally

        with self._lock:
            if self._peers.get(session.node_id) is peer:
                del self._peers[session.node_id]
                # What reached it at another endpoint is a route to it again.
                self._duplicates = {
                    endpoint: node_id
                    for endpoint, node_id in self._duplicates.items()
                    if node_id != session.node_id
                }

            # Even when this node closed it (a response that failed
            # verification), or it would be dialed straight back. By its
            # endpoint too, for a seed dialed without knowing its node id.
            rest_until = self._clock() + self._config.peers.retry_delay_seconds
            self._resting[session.node_id] = rest_until
            self._resting[session.endpoint] = rest_until

        self.logger.info("Connection to %s at %s closed", session.node_id, session.endpoint)
        self.publish(
            EventType.CONNECTION_CLOSED, {"node_id": str(session.node_id), "remote": remote}
        )
        self._maintain()

    def _talk(self, peer: _Peer, conversation: Callable[[PeerSession], None]) -> None:
        """Hold ``conversation`` with ``peer``, then schedule its next refresh."""
        try:
            conversation(peer.session)

        except OSError as error:
            self.logger.info("Exchange with %s ended early: %s", peer.session.endpoint, error)

        except Exception:
            self.logger.exception("Exchange with %s failed", peer.session.endpoint)

        finally:
            with self._lock:
                peer.busy = False
                peer.refresh_at = self._clock() + self._config.peers.seek_refresh_seconds

    def _fetch_loop(self) -> None:
        """Fetch-worker thread: fetch requested content until told to stop."""
        while (content_id := self._fetches.get()) is not None:
            try:
                self._fetch(content_id)

            except Exception:
                self.logger.exception("Fetching %s failed", content_id)
                self.publish(
                    EventType.FETCH_FAILED,
                    {"algorithm": content_id.algorithm, "hash": content_id.hash},
                )

            finally:
                with self._lock:
                    self._fetching.discard(content_id)

    def _fetch(self, content_id: ContentId) -> None:
        """Ask connected peers for ``content_id``, best match first, until one sends it."""
        payload = {"algorithm": content_id.algorithm, "hash": content_id.hash}

        for session in self._by_match(content_id):
            try:
                found = self.exchange.retrieve(session, content_id)

            except OSError as error:
                self.logger.debug(
                    "Could not ask %s for %s: %s", session.endpoint, content_id, error
                )
                continue

            if found:
                self.publish(
                    EventType.FETCH_SUCCEEDED, {**payload, "node_id": str(session.node_id)}
                )
                return

        self.publish(EventType.FETCH_FAILED, payload)

    def _hand_off(self, content_id: ContentId, copies: int) -> None:
        """Hand-off thread: push ``content_id`` to peers until ``copies`` accept it, and say which.

        The eviction module is always answered, so it never waits on a
        hand-off that went wrong.
        """
        accepted: list[ContentId] = []

        try:
            accepted = self._accepting_peers(content_id, copies)

        except Exception:
            self.logger.exception("Handing off %s failed", content_id)

        finally:
            with self._lock:
                self._handing_off.discard(content_id)

            self.publish(
                EventType.EVICTION_ACKNOWLEDGED,
                {
                    "algorithm": content_id.algorithm,
                    "hash": content_id.hash,
                    "node_ids": [str(node_id) for node_id in accepted],
                },
            )

    def _accepting_peers(self, content_id: ContentId, copies: int) -> list[ContentId]:
        """Offer ``content_id`` to connected peers, best match first, until ``copies`` accept it.

        Returns those that did, in the order offered.
        """
        try:
            body = self._source_of_truth.read(content_id)

        except ContentNotFoundError:
            self.logger.info("%s is not held, so cannot be handed off", content_id)
            return []

        accepted: list[ContentId] = []

        for session in self._by_match(content_id):
            if len(accepted) >= copies:
                break

            try:
                if self.exchange.hand_off(session, content_id, body):
                    accepted.append(session.node_id)

            except OSError as error:
                self.logger.debug(
                    "Could not hand %s off to %s: %s", content_id, session.endpoint, error
                )

        return accepted

    def _push_loop(self) -> None:
        """Push-worker thread: push new content until told to stop."""
        while (new := self._pushes.get()) is not None:
            try:
                self._push(new)

            except Exception:
                self.logger.exception("Pushing %s failed", new.content_id)

    def _push(self, new: _NewContent) -> None:
        """Push ``new`` to the best connected peer, unless that is where it came from.

        A peer that cannot be reached is passed over for the next best.
        Content that no connected peer was there to take waits for a
        connection to open.
        """
        try:
            body = self._source_of_truth.read(new.content_id)

        except ContentNotFoundError:
            self.logger.info("%s is no longer held, so cannot be pushed", new.content_id)
            return

        tried: set[ContentId] = set()

        for session in self._by_match(new.content_id):
            if session.node_id == new.source:
                self.logger.debug("Not pushing %s back to %s", new.content_id, new.source)
                return

            tried.add(session.node_id)

            try:
                if not self.exchange.hand_off(session, new.content_id, body):
                    self.logger.debug("%s refused %s", session.endpoint, new.content_id)

                return

            except OSError as error:
                self.logger.debug(
                    "Could not push %s to %s: %s", new.content_id, session.endpoint, error
                )

        with self._lock:
            waiting = self._peers.keys() <= tried

            if waiting:
                self._unpushed[new.content_id] = new.source

        if waiting:
            self.logger.debug("No peer to push %s to yet", new.content_id)

        else:  # a peer connected meanwhile
            self._pushes.put(new)

    def _push_unpushed(self) -> None:
        """Push the new content that was waiting for a peer, now that one is connected."""
        with self._lock:
            unpushed, self._unpushed = self._unpushed, {}

        for content_id, source in unpushed.items():
            self._pushes.put(_NewContent(content_id, source))

    def _by_match(self, content_id: ContentId) -> list[PeerSession]:
        """The connected peers, those whose id best matches ``content_id``'s hash first."""
        with self._lock:
            sessions = {node_id: peer.session for node_id, peer in self._peers.items()}

        if not sessions:
            return []

        return [sessions[node_id] for node_id in nearest(content_id.hash, sessions, len(sessions))]


def _dialable(endpoint: str) -> bool:
    """Whether this node can dial ``endpoint`` at all."""
    return peer_address(endpoint) is not None


def connections_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`ConnectionsModule`."""
    return ConnectionsModule(name, queues, config)
