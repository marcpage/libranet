"""Where to dial a peer, from the endpoint URL a node list names it by (HttpApi §10.6)."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

# HTTPS is deferred past v1, so only plain HTTP endpoints are dialed.
_SCHEME: Final = "http"
_DEFAULT_PORT: Final = 80


@dataclass(frozen=True)
class PeerAddress:
    """A host name or IP address, and the port the peer listens on there."""

    host: str
    port: int


def peer_address(endpoint: str) -> PeerAddress | None:
    """Where to dial ``endpoint``, or ``None`` if this node cannot dial it.

    A missing port is port 80 (HttpApi §10.6). Any path is ignored. IPv6
    hosts lose their brackets, as :func:`~libranet.connections.open_connection`
    expects.
    """
    try:
        parts = urlsplit(endpoint)
        port = parts.port

    except ValueError:
        return None

    if parts.scheme != _SCHEME or not parts.hostname:
        return None

    return PeerAddress(parts.hostname, port or _DEFAULT_PORT)
