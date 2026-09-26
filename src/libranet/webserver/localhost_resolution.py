"""Resolving ``localhost`` in a received node list (HttpApi §10.2).

A node that does not know its own public address advertises itself under the
host ``localhost``, meaning "the address this node list came from". The
receiving node puts the connection's source address in its place before the
list is stored, so ``localhost`` is never passed on to a node that was not on
that connection. Only node lists are resolved this way; ``localhost``
anywhere else keeps its ordinary meaning.

A received list is published as ``nodes.received``, and the payload says how
each of the sender's own entries was learned (Phase 2 Step 23)::

    nodes.received  {"nodes": {"http://203.0.113.42:4300": "sha256/<hex>"},
                     "sources": {"http://203.0.113.42:4300": "observed"}}

An entry resolved from ``localhost`` was *observed*: its address is the one
the connection came from. The sender's other entries were *advertised*.
``sources`` leaves out the rest, which the sender merely relayed.
"""

from __future__ import annotations
from dataclasses import dataclass
from ipaddress import IPv6Address, ip_address
from typing import Final, Mapping
from urllib.parse import urlsplit, urlunsplit

from libranet.cas.content_id import ContentId
from libranet.messaging.events import AddressSource

LOCALHOST: Final = "localhost"
_SCHEMES: Final = frozenset({"http", "https"})


def resolve_endpoint(endpoint: str, source_address: str) -> str | None:
    """``endpoint`` with a ``localhost`` host replaced by ``source_address``.

    Any other host is returned unchanged, so an endpoint resolved by an
    earlier node is never resolved again. ``None`` means the entry cannot be
    stored: it is not an ``http`` or ``https`` URL naming a host and a valid
    port, or its host is ``localhost`` and ``source_address`` is not an IP
    address to put in its place.
    """
    try:
        parts = urlsplit(endpoint)
        port = parts.port

    except ValueError:
        return None

    if parts.scheme not in _SCHEMES or not parts.hostname:
        return None

    if parts.hostname != LOCALHOST:
        return endpoint

    host = _url_host(source_address)

    if host is None:
        return None

    return urlunsplit(parts._replace(netloc=host if port is None else f"{host}:{port}"))


@dataclass(frozen=True)
class NodeListSender:
    """The node a node list came from, and the address its connection came from."""

    node_id: ContentId
    address: str

    def received(self, nodes: Mapping[str, ContentId]) -> dict[str, dict[str, str]]:
        """The ``nodes.received`` payload for ``nodes``, a node list this sender sent.

        Every ``localhost`` endpoint is resolved to :attr:`address`, and an
        entry whose endpoint cannot be stored is dropped. If two entries end
        up with the same endpoint, the later one is kept.
        """
        received: dict[str, str] = {}
        sources: dict[str, str] = {}

        for endpoint, node_id in nodes.items():
            resolved = resolve_endpoint(endpoint, self.address)

            if resolved is None:
                continue

            received[resolved] = str(node_id)
            sources.pop(resolved, None)

            if node_id == self.node_id:
                observed = resolved != endpoint
                source = AddressSource.OBSERVED if observed else AddressSource.ADVERTISED
                sources[resolved] = source.value

        return {"nodes": received, "sources": sources}


def _url_host(address: str) -> str | None:
    """``address`` written as a URL host, or ``None`` if it is not an IP address.

    An IPv4-mapped IPv6 address such as ``::ffff:203.0.113.42``, which is how
    a dual-stack listener reports an IPv4 client, is unwrapped so that peers
    without IPv6 can use the endpoint too. IPv6 hosts are bracketed (RFC 3986
    §3.2.2).
    """
    try:
        parsed = ip_address(address)

    except ValueError:
        return None

    if not isinstance(parsed, IPv6Address):
        return str(parsed)

    if parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)

    return f"[{parsed}]"
