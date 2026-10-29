"""Keeping the clients connected to the web server by their Karma (Phase 6 Step 142).

A server keeps a client of more Karma connected longer, and may close one of
less that adds nothing (HandshakeProtocol §5.2). Past a client's first
``network.client_grace_requests`` requests, which answer its first-contact
exchange (§3), one whose Karma is below the median of the clients connected,
and that has pushed nothing this node seeks for
``network.client_value_seconds``, is closed. One of the median's Karma or
more is kept, so that none is closed while every client holds as much as
the others, as while the node follows no chain.

A client is a peer whose key verified a request on a connection
(:mod:`libranet.webserver.inbound_peers`). What it did is forgotten when its
last connection closes, so one that connects again starts afresh. Its Karma
is its balance as the chain this node follows states it, in
``balances.json`` (:class:`~libranet.protocol.karma_balances.KarmaBalances`).
What it pushes is sought if this node's own seek list names it, or a search
the list names would find it (HttpApi §10.7.2). Each file is kept as it was
read until it is replaced (:class:`~libranet.atomic_file.KeptRead`), and
until it is first written, nothing is sought, and no client holds Karma.
"""

from __future__ import annotations
from dataclasses import dataclass, replace
from json import loads
from logging import getLogger
from pathlib import Path
from statistics import median
from threading import Lock
from time import time
from typing import Callable, Iterable

from libranet.atomic_file import KeptRead
from libranet.cas.content_id import ContentId
from libranet.cas.store import CasStore
from libranet.config.models import LibranetConfig, StorageConfig
from libranet.protocol.karma_balances import KARMA_BALANCES_FILE, KarmaBalances
from libranet.protocol.lists import parse_seek_list
from libranet.protocol.search import LocalSearch

_LOGGER = getLogger(__name__)


@dataclass(frozen=True)
class _SeekList:
    """The content ids, as text, and the search prefixes a seek list names."""

    data: frozenset[str] = frozenset()
    searches: tuple[str, ...] = ()


class SoughtContent:
    """What this node's own seek list at ``path`` asks for, read again whenever it is replaced.

    A search it names asks for what ``search`` matches to its prefix. Safe
    to use from several threads at once, as the web server's are.
    """

    def __init__(self, path: Path, search: LocalSearch) -> None:
        self._path = path
        self._search = search
        self._lock = Lock()
        self._file = KeptRead(path, self._read)
        # What was last read, kept when the file cannot be read again.
        self._sought = _SeekList()

    @classmethod
    def of(cls, storage: StorageConfig) -> SoughtContent:
        """The seek list the stats module derives, its searches matched as ``/data/search`` does."""
        return cls(
            storage.seek_list_path, LocalSearch.of(CasStore.source_of_truth(storage), storage)
        )

    def seeks(self, content_id: ContentId) -> bool:
        """Whether the seek list asks for ``content_id``, as it says now."""
        with self._lock:
            sought = self._current()

        return str(content_id) in sought.data or any(
            self._search.matching(prefix, [content_id]) for prefix in sought.searches
        )

    def _current(self) -> _SeekList:
        """What the file holds now, read again only if it was replaced."""
        try:
            self._sought = self._file.current()

        except FileNotFoundError:
            # Not logged: the stats module has not derived the list yet.
            self._sought = _SeekList()

        except OSError as error:
            _LOGGER.warning("Cannot look at this node's own seek list at %s: %s", self._path, error)

        return self._sought

    def _read(self, path: Path) -> _SeekList:
        """What the file at ``path`` asks for; what was read before, if it cannot be read."""
        try:
            data, searches = parse_seek_list(loads(path.read_bytes()))

        except (OSError, ValueError) as error:
            _LOGGER.warning("Cannot read this node's own seek list at %s: %s", path, error)
            return self._sought

        return _SeekList(frozenset(data), tuple(searches))


class HeldKarma:
    """The Kismet each identity holds, as ``balances.json`` in ``karma_dir`` states it now.

    It is read again whenever it is replaced. Safe to use from several
    threads at once, as the web server's are.
    """

    def __init__(self, karma_dir: Path) -> None:
        self._path = karma_dir / KARMA_BALANCES_FILE
        self._lock = Lock()
        self._file = KeptRead(self._path, self._read)
        # What was last read, kept when the file cannot be read again.
        self._balances = KarmaBalances()

    def kismet(self, identities: Iterable[ContentId]) -> dict[ContentId, int]:
        """The Kismet each of ``identities`` holds; none for one the balances do not name."""
        with self._lock:
            balances = self._current()

        return {identity: balances.balance(identity).kismet for identity in identities}

    def _current(self) -> KarmaBalances:
        """What the file holds now, read again only if it was replaced."""
        try:
            self._balances = self._file.current()

        except FileNotFoundError:
            # Not logged: the Karma module has not read a head's balances yet.
            self._balances = KarmaBalances()

        except OSError as error:
            _LOGGER.warning("Cannot look at the Karma balances at %s: %s", self._path, error)

        return self._balances

    def _read(self, path: Path) -> KarmaBalances:
        """The balances the file at ``path`` states; those read before, if it cannot be read."""
        try:
            return KarmaBalances.from_value(loads(path.read_bytes()))

        except (OSError, ValueError) as error:
            _LOGGER.warning(
                "Cannot read the Karma balances at %s, so clients are kept as before: %s",
                path,
                error,
            )
            return self._balances


@dataclass(frozen=True)
class _ClientUse:
    """The requests a client has made, and when it last pushed what is sought, or else connected."""

    valued_at: float
    requests: int = 0


class ClientKeeping:
    """Which clients of the web server are kept connected, and which are closed.

    Each is kept for its first ``grace_requests`` requests, and while it has
    pushed something ``sought`` within ``value_seconds``, and after, unless
    it holds less Karma, as ``karma`` says, than the median of the clients
    connected. Safe to use from several threads at once, as the web
    server's are.
    """

    def __init__(
        self,
        grace_requests: int,
        value_seconds: float,
        sought: SoughtContent,
        karma: HeldKarma,
        *,
        clock: Callable[[], float] = time,
    ) -> None:
        self._grace_requests = grace_requests
        self._value_seconds = value_seconds
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
                self._uses[client] = replace(use, valued_at=self._clock())

    def served(self, client: ContentId) -> bool:
        """Count a request of ``client``'s answered; whether it is kept connected."""
        with self._lock:
            use = self._uses.get(client)

            if use is None:
                return True

            use = replace(use, requests=use.requests + 1)
            self._uses[client] = use

            if use.requests <= self._grace_requests:
                return True

            if self._clock() - use.valued_at < self._value_seconds:
                return True

            connected = list(self._uses)

        kismet = self._karma.kismet(connected)
        middle = median(kismet.values())

        if kismet[client] >= middle:
            return True

        _LOGGER.info(
            "Closing the connection of %s, which holds %s Kismet, below the median of %s of the "
            "%s clients connected, and has pushed nothing this node seeks for %s seconds",
            client,
            kismet[client],
            middle,
            len(connected),
            self._value_seconds,
        )
        return False
