"""Outgoing peer connections (Phase 1 Steps 10, 11).

Owns the raw-socket client, the first-contact handshake, the 16-connection
4-bit peer mix, and fetching data on the fetcher module's behalf.
"""

from libranet.connections.candidates import Candidate, node_list_candidates, seed_candidates
from libranet.connections.endpoints import PeerAddress, peer_address
from libranet.connections.errors import (
    ConnectionClosedError,
    MalformedResponseError,
    PeerAuthenticationError,
)
from libranet.connections.module import ConnectionsModule, connections_module_factory
from libranet.connections.peer_connection import PeerConnection, open_connection
from libranet.connections.peer_exchange import PeerExchange
from libranet.connections.peer_mix import bucket_of, choose_candidates
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.connections.request_encoding import encode_request
from libranet.connections.response_parser import PeerResponse, RequestLine, ResponseParser

__all__ = [
    "Candidate",
    "ConnectionClosedError",
    "ConnectionsModule",
    "MalformedResponseError",
    "PeerAddress",
    "PeerAuthenticationError",
    "PeerConnection",
    "PeerExchange",
    "PeerRequest",
    "PeerResponse",
    "PeerSession",
    "RequestLine",
    "ResponseParser",
    "bucket_of",
    "choose_candidates",
    "connections_module_factory",
    "encode_request",
    "node_list_candidates",
    "open_connection",
    "peer_address",
    "seed_candidates",
]
