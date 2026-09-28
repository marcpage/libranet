"""Tests for turning node-list endpoints into addresses to dial."""

from __future__ import annotations

from pytest import mark

from libranet.connections.endpoints import PeerAddress


@mark.parametrize(
    ("endpoint", "expected"),
    [
        ("http://203.0.113.42:4300", PeerAddress("203.0.113.42", 4300)),
        ("http://Peer.Example", PeerAddress("peer.example", 80)),
        ("HTTP://peer.example:8080/ignored", PeerAddress("peer.example", 8080)),
        ("http://[2001:db8::1]:4300", PeerAddress("2001:db8::1", 4300)),
    ],
)
def test_http_endpoints_are_dialed_at_their_host_and_port(
    endpoint: str, expected: PeerAddress
) -> None:
    assert PeerAddress.of(endpoint) == expected


@mark.parametrize(
    "endpoint",
    [
        "https://peer.example:443",  # TLS is deferred past v1
        "ftp://peer.example",
        "http://",
        "http://peer.example:99999",
        "http://[::1",
        "peer.example:8080",
    ],
)
def test_endpoints_this_node_cannot_dial_are_refused(endpoint: str) -> None:
    assert PeerAddress.of(endpoint) is None
