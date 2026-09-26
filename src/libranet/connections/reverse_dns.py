"""Finding names for the addresses peers are observed at (Phase 2 Step 23).

A node that does not know its public address advertises ``localhost``, which
a node receiving its list resolves to the address the connection came from
(HttpApi §10.2). That address may change; a name for it may not, since a
company's name outlives a change of address. So each address a peer is
observed at is looked up (a PTR query), and each name found is published as
another address of the same node, with the scheme and port of the endpoint it
was found for::

    nodes.received  {"nodes": {"http://peer.example.org:4300": "sha256/<hex>"},
                     "sources": {"http://peer.example.org:4300": "reverse_dns"}}

Only observed addresses are looked up, not every address relayed in someone
else's list. There is no forward confirmation: an ISP's generic names are
expected noise, and a name that leads elsewhere cannot pass the handshake's
identity check. Failing to reach the node there sorts out both.

Lookups can take seconds, so they run on a worker thread of their own. The
names found for an address, or finding none, are remembered for a while, so
an address observed again soon is not looked up again. A loopback address is
never looked up: its name is ``localhost``, which in a node list means
something else entirely. For the same reason, a name ``localhost`` found for
any address is never published.
"""

from __future__ import annotations
from ipaddress import ip_address
from logging import Logger
from queue import SimpleQueue
from socket import gethostbyaddr
from threading import Thread
from time import time
from typing import Callable, Sequence
from urllib.parse import urlsplit, urlunsplit

from libranet.messaging.events import AddressSource, EventType
from libranet.webserver.client_origin import is_local_client
from libranet.webserver.localhost_resolution import LOCALHOST
from libranet.webserver.publishing import Publish

#: Finds the names a reverse DNS lookup gives an IP address.
ResolveNames = Callable[[str], Sequence[str]]


def host_names(address: str) -> list[str]:
    """The names a reverse DNS lookup finds for ``address``; none if it finds none."""
    try:
        name, aliases, _ = gethostbyaddr(address)

    except OSError:
        return []

    return [name, *aliases]


class ReverseLookup:
    """Looks up names for observed endpoints on a worker thread, and publishes those it finds.

    ``resolve`` does the lookup itself, so tests never touch real DNS. What
    it finds for an address is remembered for ``cache_seconds``.
    """

    def __init__(
        self,
        publish: Publish,
        logger: Logger,
        cache_seconds: float,
        *,
        resolve: ResolveNames = host_names,
        clock: Callable[[], float] = time,
    ) -> None:
        self._publish = publish
        self._logger = logger
        self._cache_seconds = cache_seconds
        self._resolve = resolve
        self._clock = clock
        self._requests: SimpleQueue[tuple[str, str] | None] = SimpleQueue()
        # When each address's names expire, and the names. Only the worker
        # thread touches it.
        self._cache: dict[str, tuple[float, tuple[str, ...]]] = {}

    def start(self, name: str) -> None:
        """Start the worker thread, named ``name``, which never holds up the process exiting."""
        Thread(target=self._run, name=name, daemon=True).start()

    def stop(self) -> None:
        """Stop the worker thread once the lookups asked for already are done."""
        self._requests.put(None)

    def look_up(self, node_id: str, endpoint: str) -> None:
        """Look for names for the address ``endpoint`` names, where ``node_id`` was observed."""
        self._requests.put((node_id, endpoint))

    def _run(self) -> None:
        while (request := self._requests.get()) is not None:
            node_id, endpoint = request

            try:
                self._publish_names(node_id, endpoint)

            except Exception:
                self._logger.exception("Looking up names for %s failed", endpoint)

    def _publish_names(self, node_id: str, endpoint: str) -> None:
        """Publish ``endpoint`` with each name found for its host in the host's place."""
        parts = urlsplit(endpoint)
        host = parts.hostname

        if host is None or not _worth_looking_up(host):
            return

        named: dict[str, str] = {}

        for name in self._names(host):
            if name.lower() != LOCALHOST:
                netloc = name if parts.port is None else f"{name}:{parts.port}"
                named[urlunsplit(parts._replace(netloc=netloc))] = node_id

        if named:
            self._logger.debug("Names for %s: %s", endpoint, ", ".join(named))
            self._publish(
                EventType.NODES_RECEIVED,
                {"nodes": named, "sources": dict.fromkeys(named, AddressSource.REVERSE_DNS.value)},
            )

    def _names(self, address: str) -> tuple[str, ...]:
        """The names found for ``address``, looked up again once they are too old."""
        now = self._clock()
        cached = self._cache.get(address)

        if cached is not None and now < cached[0]:
            return cached[1]

        # Whatever else has expired goes too, so the cache holds only
        # addresses observed lately.
        self._cache = {key: entry for key, entry in self._cache.items() if now < entry[0]}
        names = tuple(self._resolve(address))
        self._cache[address] = (now + self._cache_seconds, names)
        return names


def _worth_looking_up(host: str) -> bool:
    """Whether ``host`` is an IP address, and not a loopback one."""
    try:
        ip_address(host)

    except ValueError:
        return False

    return not is_local_client(host)
