"""Where to dial a peer, from the endpoint URL a node list names it by (HttpApi §10.6)."""

from __future__ import annotations
from dataclasses import dataclass
from logging import getLogger
from typing import Final
from urllib.parse import urlsplit

_LOGGER = getLogger(__name__)

# HTTPS is deferred past v1, so only plain HTTP endpoints are dialed.
_SCHEME: Final = "http"
_DEFAULT_PORT: Final = 80


@dataclass(frozen=True)
class PeerAddress:
    """A host name or IP address, and the port the peer listens on there."""

    host: str
    port: int

    @classmethod
    def of(cls, endpoint: str) -> PeerAddress | None:
        """Where to dial ``endpoint``, or ``None`` if this node cannot dial it.

        A missing port is port 80 (HttpApi §10.6). Any path is ignored. IPv6
        hosts lose their brackets, as :meth:`~libranet.connections.PeerConnection.open`
        expects.
        """
        try:
            parts = urlsplit(endpoint)
            port = parts.port

        except ValueError as error:
            _LOGGER.warning("Cannot dial %r: %s", endpoint, error)
            return None

        # Not logged: not dialing another scheme is by design while HTTPS is
        # deferred.
        if parts.scheme != _SCHEME:
            return None

        if not parts.hostname:
            _LOGGER.warning("Cannot dial %r: it names no host", endpoint)
            return None

        return cls(parts.hostname, port or _DEFAULT_PORT)
