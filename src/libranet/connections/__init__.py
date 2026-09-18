"""Outgoing peer connections (Phase 1 Steps 10, 11).

Owns the raw-socket client, the first-contact handshake, the 16-connection
4-bit peer mix, and fetching data on the fetcher module's behalf.
"""

from libranet.connections.errors import ConnectionClosedError, MalformedResponseError
from libranet.connections.peer_connection import PeerConnection, open_connection
from libranet.connections.request_encoding import encode_request
from libranet.connections.response_parser import PeerResponse, ResponseParser

__all__ = [
    "ConnectionClosedError",
    "MalformedResponseError",
    "PeerConnection",
    "PeerResponse",
    "ResponseParser",
    "encode_request",
    "open_connection",
]
