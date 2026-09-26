"""Outgoing peer connections (Phase 1 Steps 10, 11).

Owns the raw-socket client, the first-contact handshake, the peer mix, and
fetching data on the fetcher module's behalf.
"""

from libranet.connections.candidates import Candidate, candidate_list, seed_candidates
from libranet.connections.endpoints import PeerAddress, peer_address
from libranet.connections.errors import (
    ConnectionClosedError,
    MalformedResponseError,
    PeerAuthenticationError,
)
from libranet.connections.module import ConnectionsModule, connections_module_factory
from libranet.connections.peer_connection import PeerConnection, open_connection
from libranet.connections.peer_exchange import PeerExchange
from libranet.connections.peer_mix import PeerMix, bucket_of
from libranet.connections.peer_session import PeerRequest, PeerSession
from libranet.connections.request_encoding import encode_request
from libranet.connections.response_parser import PeerResponse, RequestLine, ResponseParser
from libranet.connections.reverse_dns import ResolveNames, ReverseLookup, host_names

__all__ = [
    "Candidate",
    "ConnectionClosedError",
    "ConnectionsModule",
    "MalformedResponseError",
    "PeerAddress",
    "PeerAuthenticationError",
    "PeerConnection",
    "PeerExchange",
    "PeerMix",
    "PeerRequest",
    "PeerResponse",
    "PeerSession",
    "RequestLine",
    "ResolveNames",
    "ResponseParser",
    "ReverseLookup",
    "bucket_of",
    "candidate_list",
    "connections_module_factory",
    "encode_request",
    "host_names",
    "open_connection",
    "peer_address",
    "seed_candidates",
]
