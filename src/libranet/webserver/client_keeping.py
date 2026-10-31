"""Keeping the clients connected to the web server by their Karma (Phase 6 Step 142).

A server keeps a client of more Karma connected longer, and may close one of
less that adds nothing (HandshakeProtocol §5.2). Past a client's first
``network.client_grace_requests`` requests, which answer its first-contact
exchange (§3), one whose Karma is below both the median of the clients
connected and this node's own, and that has pushed nothing this node seeks
for ``network.client_value_seconds``, is closed. One of the median's Karma or
more is kept, so that none is closed while every client holds as much as
the others, as while the node follows no chain, and so is one of this node's
Karma or more, so that a node turns away none richer than itself. None is
closed unless more than ``network.client_keep_count`` are connected, those
being closed not counted, so that requests answered at once leave that many.

A client is a peer whose key verified a request on a connection
(:mod:`libranet.webserver.inbound_peers`). What it did is forgotten when its
last connection closes, so one that connects again starts afresh. Its Karma
is its balance as the chain this node follows states it, in
``balances.json`` (:class:`~libranet.protocol.karma_balances.KarmaBalances`).
What it pushes is sought if this node's own seek list names it, or a search
the list names would find it (HttpApi §10.7.2). Each file is kept as it was
read until it is replaced (:class:`~libranet.atomic_file.LastGoodRead`), and
until it is first written, nothing is sought, and no client holds Karma.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from functools import cache
from json import loads
from logging import getLogger
from pathlib import Path
from statistics import median
from threading import Lock
from time import time
from typing import Callable, Iterable

from libranet.atomic_file import LastGoodRead
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.protocol.karma_balances import KARMA_BALANCES_FILE, KarmaBalances
from libranet.protocol.lists import parse_seek_list
from libranet.protocol.search import LocalSearch

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class _SeekList:
    """The content ids, as text, and the search prefixes a seek list names."""

    data: frozenset[str] = frozenset()
    searches: tuple[str, ...] = ()

    @classmethod
    def parsed(cls, data: bytes) -> _SeekList:
        """What the seek list ``data`` names.

        Raises:
            ValueError: it is not JSON, or no seek list.
        """
        named, searches = parse_seek_list(loads(data))
        return cls(frozenset(named), tuple(searches))


class SoughtContent:
    """What this node's own seek list at ``path`` asks for, read again whenever it is replaced.

    A search it names asks for what ``search`` matches to its prefix. Safe
    to use from several threads at once, as the web server's are.
    """

    def __init__(self, path: Path, search: LocalSearch) -> None:
        self._search = search
        self._file = LastGoodRead(
            path, _SeekList.parsed, _SeekList(), what="this node's own seek list", logger=_LOGGER
        )

    @classmethod
    def of(cls, storage: StorageConfig) -> SoughtContent:
        """The seek list the stats module derives, its searches matched as ``/data/search`` does."""
        return cls(
            storage.seek_list_path, LocalSearch.of(CasStore.source_of_truth(storage), storage)
        )

    def seeks(self, content_id: ContentId) -> bool:
        """Whether the seek list asks for ``content_id``, as it says now."""
        sought = self._file.current()
        return str(content_id) in sought.data or any(
            self._search.matching(prefix, [content_id]) for prefix in sought.searches
        )


class HeldKarma:
    """The Kismet each identity holds, as ``balances.json`` in ``karma_dir`` states it now.

    It is read again whenever it is replaced. Safe to use from several
    threads at once, as the web server's are.
    """

    def __init__(self, karma_dir: Path) -> None:
        self._file = LastGoodRead(
            karma_dir / KARMA_BALANCES_FILE,
            KarmaBalances.parsed,
            KarmaBalances(),
            what="the Karma balances",
            logger=_LOGGER,
        )

    def kismet(self, identities: Iterable[ContentId]) -> dict[ContentId, int]:
        """The Kismet each of ``identities`` holds; none for one the balances do not name."""
        balances = self._file.current()
        return {identity: balances.balance(identity).kismet.count for identity in identities}


@dataclass(frozen=True)
class _ClientUse:
    """The requests a client has made, and when it last pushed what is sought, or else connected.

    ``closing`` says it is to be closed, each of its connections as each is
    next answered, unless it pushes what is sought first.
    """

    valued_at: float
    requests: int = 0
    closing: bool = False


class ClientKeeping:
    """Which clients of the web server are kept connected, and which are closed.

    Each is kept for its first ``grace_requests`` requests, and while it has
    pushed something ``sought`` within ``value_seconds``, and after, unless
    it holds less Karma, as ``karma`` says, than both the median of the
    clients connected and the node ``node_id`` says this is, and more than
    ``keep_count`` are. Safe to use from several threads at once, as the web
    server's are.
    """

    def __init__(
        self,
        grace_requests: int,
        value_seconds: float,
        sought: SoughtContent,
        karma: HeldKarma,
        *,
        keep_count: int,
        node_id: Callable[[], ContentId],
        clock: Callable[[], float] = time,
    ) -> None:
        self._grace_requests = grace_requests
        self._value_seconds = value_seconds
        self._keep_count = keep_count
        self._node_id = node_id
        self._sought = sought
        self._karma = karma
        self._clock = clock
        self._uses: dict[ContentId, _ClientUse] = {}
        self._lock = Lock()

    @classmethod
    def of(cls, config: LibranetConfig, *, clock: Callable[[], float] = time) -> ClientKeeping:
        """The keeping of clients a node's configuration sets."""
        network, storage = config.network, config.storage
        return cls(
            network.client_grace_requests,
            network.client_value_seconds,
            SoughtContent.of(storage),
            HeldKarma(storage.karma_dir),
            keep_count=network.client_keep_count,
            # Asked with each client closed or kept, and its key does not change.
            node_id=cache(lambda: NodeIdentity.load(config).node_id),
            clock=clock,
        )

    def connected(self, client: ContentId) -> None:
        """Start counting what ``client``, connected now, does."""
        with self._lock:
            self._uses[client] = _ClientUse(self._clock())

    def disconnected(self, client: ContentId) -> None:
        """Forget what ``client``, whose last connection has closed, did."""
        with self._lock:
            self._uses.pop(client, None)

    def pushed(self, client: ContentId, content_id: ContentId) -> None:
        """Note that ``client`` pushed ``content_id``, which keeps it if this node seeks that."""
        if not self._sought.seeks(content_id):
            return

        with self._lock:
            use = self._uses.get(client)

            if use is not None:
                self._uses[client] = replace(use, valued_at=self._clock(), closing=False)

    def served(self, client: ContentId) -> bool:
        """Count a request of ``client``'s answered; whether it is kept connected."""
        with self._lock:
            use = self._uses.get(client)

            if use is None:
                return True

            if use.closing:
                return False

            use = replace(use, requests=use.requests + 1)
            self._uses[client] = use

            if use.requests <= self._grace_requests:
                return True

            if self._clock() - use.valued_at < self._value_seconds:
                return True

            closing = self._closes(client)
            self._uses[client] = replace(use, closing=closing)
            return not closing

    def _closes(self, client: ContentId) -> bool:
        """Whether ``client``, past its grace and adding nothing, is closed; the lock is held.

        It is decided under the lock, so that requests answered at once close
        no more than leave ``keep_count``. That it is, is logged.
        """
        connected = [each for each, use in self._uses.items() if not use.closing]

        if len(connected) <= self._keep_count:
            return False

        kismet = self._karma.kismet(connected)
        middle = median(kismet.values())
        node_id = self._node_id()
        own = self._karma.kismet([node_id])[node_id]

        if kismet[client] >= min(middle, own):
            return False

        _LOGGER.info(
            "Closing the connection of %s, which holds %s Kismet, below both the median of %s of "
            "the %s clients connected and the %s this node holds, and has pushed nothing this "
            "node seeks for %s seconds",
            client,
            kismet[client],
            middle,
            len(connected),
            own,
            self._value_seconds,
        )
        return True
