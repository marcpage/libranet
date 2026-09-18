"""The connection manager module process (Phase 1 Step 11).

It owns every outgoing peer connection. It keeps up the peer mix of
HighLevelDesign §4.6 (:mod:`~libranet.connections.peer_mix`), and holds the
first-contact exchange with each new peer
(:mod:`~libranet.connections.peer_exchange`).

The mix is tended when something changes rather than on a timer: when the
module starts, when the stats module announces a new node list, when a
connection opens, fails, or closes, and when an endpoint's retry delay ends.
An endpoint rests for ``retry_delay_seconds`` after an attempt to connect to
it fails, or after its connection closes, so a peer that is down or turning
this node away is not dialed again and again. Meanwhile another peer
in the same bucket is dialed, if one is known. Connecting and talking to
peers happen on background threads, so the receive loop never waits on the
network.

For the stats module it publishes::

    connection.opened  {"node_id", "endpoint"}
    connection.closed  {"node_id", "remote"}
    connection.failed  {"node_id", "endpoint"}

A connection is opened once the peer has proven its node id, and ``remote``
is true unless this node chose to close it. A failed attempt is published
only for a peer whose node id was known beforehand.
"""

from __future__ import annotations
from functools import partial
from logging import Logger
from threading import Lock, Thread
from time import time
from typing import Callable, ClassVar

from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig
from libranet.config.seeds import SeedError, load_seed_peers
from libranet.connections.candidates import Candidate, node_list_candidates, seed_candidates
from libranet.connections.endpoints import peer_address
from libranet.connections.peer_exchange import PeerExchange
from libranet.connections.peer_mix import choose_candidates
from libranet.connections.peer_session import PeerSession
from libranet.identity.node_identity import load_node_identity
from libranet.messaging.envelope import Message
from libranet.messaging.events import EventType
from libranet.messaging.module import DEFAULT_POLL_INTERVAL_SECONDS, ModuleBase
from libranet.messaging.queues import ModuleQueues
from libranet.modules import ModuleName


class ConnectionsModule(ModuleBase):
    """Keeps this node connected to a spread of peers."""

    subscriptions: ClassVar[frozenset[EventType]] = frozenset({EventType.NODE_LIST_UPDATED})

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
        self._exchange: PeerExchange | None = None
        self._seeds: list[Candidate] = []
        # Everything below is shared with the background threads, under the lock.
        self._lock = Lock()
        self._running = False
        self._candidates: list[Candidate] = []
        self._peers: dict[ContentId, PeerSession] = {}
        # Endpoints being dialed, with the node id each is expected to have.
        self._pending: dict[str, ContentId | None] = {}
        # Endpoints not to be dialed again until the time given.
        self._resting: dict[str, float] = {}
        # Endpoints that turned out to be this node itself.
        self._own_endpoints: set[str] = set()

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
            return {node_id: session.endpoint for node_id, session in self._peers.items()}

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

        self._reload_candidates()
        self._maintain()

    def on_idle(self) -> None:
        """Dial again once endpoints have rested."""
        now = self._clock()

        with self._lock:
            rested = [endpoint for endpoint, until in self._resting.items() if until <= now]

            for endpoint in rested:
                del self._resting[endpoint]

        if rested:
            self._maintain()

    def on_stop(self) -> None:
        """Close every connection, whichever way the module stopped.

        A connection still being opened is closed as soon as it opens, and
        is never reported as opened.
        """
        with self._lock:
            self._running = False
            sessions = list(self._peers.values())

        for session in sessions:
            session.close()

    def handle(self, message: Message) -> None:
        """React to a new node list."""
        self._reload_candidates()
        self._maintain()

    def _load_seeds(self) -> list[Candidate]:
        try:
            return seed_candidates(load_seed_peers(self._config.peers.seed_file))

        except SeedError as error:
            self.logger.warning("Seed list unavailable: %s", error)
            return []

    def _reload_candidates(self) -> None:
        """Read the node list afresh, falling back to the seeds while it names no peer."""
        candidates = node_list_candidates(
            self._config.storage.node_list_path, self.exchange.node_id
        )

        with self._lock:
            self._candidates = [
                candidate
                for candidate in candidates or self._seeds
                if peer_address(candidate.endpoint) is not None
            ]

    def _maintain(self) -> None:
        """Dial whatever the peer mix is short of."""
        peers = self._config.peers

        with self._lock:
            if not self._running:
                return

            unavailable = (
                self._pending.keys()
                | self._resting.keys()
                | self._own_endpoints
                | {session.endpoint for session in self._peers.values()}
            )
            chosen = choose_candidates(
                [
                    candidate
                    for candidate in self._candidates
                    if candidate.endpoint not in unavailable
                ],
                [*self._peers, *self._pending.values()],
                peers.min_outgoing_connections,
                peers.bucket_prefix_bits,
            )

            for candidate in chosen:
                self._pending[candidate.endpoint] = candidate.node_id

        for candidate in chosen:
            self._start("connect", partial(self._connect, candidate))

    def _start(self, purpose: str, job: Callable[[], None]) -> None:
        """Run ``job`` on its own thread, which never holds up the process exiting."""
        Thread(target=job, name=f"{self.name}-{purpose}", daemon=True).start()

    def _connect(self, candidate: Candidate) -> None:
        """Dial ``candidate``, take it into the mix, then finish the first-contact exchange."""
        try:
            session = self.exchange.open(candidate.endpoint)

        except Exception as error:
            self._attempt_failed(candidate, error)
            return

        admitted = self._admit(candidate, session)
        self._maintain()

        if admitted:
            self._first_contact(session)

    def _attempt_failed(self, candidate: Candidate, error: Exception) -> None:
        if isinstance(error, OSError):
            self.logger.info("Could not connect to %s: %s", candidate.endpoint, error)

        else:
            self.logger.error("Connecting to %s failed", candidate.endpoint, exc_info=error)

        with self._lock:
            self._pending.pop(candidate.endpoint, None)
            self._resting[candidate.endpoint] = (
                self._clock() + self._config.peers.retry_delay_seconds
            )

        if candidate.node_id is not None:
            self.publish(
                EventType.CONNECTION_FAILED,
                {"node_id": str(candidate.node_id), "endpoint": candidate.endpoint},
            )

        self._maintain()

    def _admit(self, candidate: Candidate, session: PeerSession) -> bool:
        """Take a peer that has proven its node id into the mix, unless it is not wanted.

        It is not wanted if it is this node, if that node is already
        connected, or if the module is stopping.
        """
        refusal = ""

        with self._lock:
            self._pending.pop(candidate.endpoint, None)

            if not self._running:
                refusal = "the module is stopping"

            elif session.node_id == self.exchange.node_id:
                self._own_endpoints.add(candidate.endpoint)
                refusal = "it is this node"

            elif session.node_id in self._peers:
                self._resting[candidate.endpoint] = (
                    self._clock() + self._config.peers.retry_delay_seconds
                )
                refusal = f"{session.node_id} is already connected"

            else:
                self._peers[session.node_id] = session

        if refusal:
            self.logger.info("Closing the connection to %s: %s", candidate.endpoint, refusal)
            session.close()
            return False

        self.logger.info("Connected to %s at %s", session.node_id, session.endpoint)
        self.publish(
            EventType.CONNECTION_OPENED,
            {"node_id": str(session.node_id), "endpoint": session.endpoint},
        )
        session.when_closed(partial(self._closed, session))
        return True

    def _closed(self, session: PeerSession) -> None:
        """Take a peer whose connection has closed out of the mix, and replace it."""
        remote = not session.closed_locally

        with self._lock:
            if self._peers.get(session.node_id) is session:
                del self._peers[session.node_id]

            # Even when this node closed it (a response that failed
            # verification), or it would be dialed straight back.
            self._resting[session.endpoint] = self._clock() + self._config.peers.retry_delay_seconds

        self.logger.info("Connection to %s at %s closed", session.node_id, session.endpoint)
        self.publish(
            EventType.CONNECTION_CLOSED, {"node_id": str(session.node_id), "remote": remote}
        )
        self._maintain()

    def _first_contact(self, session: PeerSession) -> None:
        """Hold the rest of the first-contact exchange with a newly admitted peer."""
        try:
            self.exchange.first_contact(session)

        except OSError as error:
            self.logger.info("Exchange with %s ended early: %s", session.endpoint, error)

        except Exception:
            self.logger.exception("Exchange with %s failed", session.endpoint)


def connections_module_factory(
    name: ModuleName, config: LibranetConfig, queues: ModuleQueues
) -> ModuleBase:
    """:data:`~libranet.supervision.specs.ModuleFactory` for :class:`ConnectionsModule`."""
    return ConnectionsModule(name, queues, config)
