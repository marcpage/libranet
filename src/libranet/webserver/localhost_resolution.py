"""Resolving ``localhost`` in a received node list (HttpApi §10.2).

A node that does not know its own public address advertises itself under the
host ``localhost``, meaning "the address this node list came from". The
receiving node puts the connection's source address in its place before the
list is stored, so ``localhost`` is never passed on to a node that was not on
that connection. Only node lists are resolved this way; ``localhost``
anywhere else keeps its ordinary meaning.
"""

from __future__ import annotations
from ipaddress import IPv6Address, ip_address
from typing import Final
from urllib.parse import urlsplit, urlunsplit

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
