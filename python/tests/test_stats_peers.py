"""Tests for what this node knows of its peers, as the peer list gives it."""

from __future__ import annotations
from json import loads
from pathlib import Path
from typing import Iterator

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.messaging.events import AddressSource, ConnectionDirection
from libranet.stats.database import StatsDatabase
from libranet.stats.peers import KnownPeer, PeerList
from libranet.stats.records import NodeAddress, NodeStats

SELF_ID = ContentId.for_data(b"this node's public key", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
OTHER_PEER_ID = ContentId.for_data(b"another peer's public key", "sha256")
INBOUND_ID = ContentId.for_data(b"a peer that only dials in", "sha256")
ENDPOINT = "http://203.0.113.9:4300"
OTHER_ENDPOINT = "http://198.51.100.7:8080"
# 2026-10-14T12:00:00Z.
NOW = 1_791_979_200.0


@fixture
def now() -> list[float]:
    """The database's clock, which a test moves by hand."""
    return [NOW]


@fixture
def database(tmp_path: Path, now: list[float]) -> Iterator[StatsDatabase]:
    with StatsDatabase(tmp_path / "libranet.sqlite3", clock=lambda: now[0]) as database:
        yield database


def read(
    database: StatsDatabase, connected: ConnectedPeers | None = None, max_peers: int = 100
) -> PeerList:
    return PeerList.read(
        database,
        ConnectedPeers() if connected is None else connected,
        exclude=SELF_ID,
        max_peers=max_peers,
        now=NOW,
    )


def test_a_peer_is_given_with_its_statistics_addresses_and_connections() -> None:
    peer = KnownPeer(
        NodeStats(
            PEER_ID,
            connection_attempts=3,
            successful_connections=2,
            remote_disconnects=1,
            last_connected=NOW,
            connected_seconds=90.5,
            bytes_received=1024,
            bytes_sent=256,
            data_found=4,
            data_not_found=1,
            consecutive_failures=0,
            last_failure=NOW - 3600,
        ),
        (
            NodeAddress(
                PEER_ID,
                ENDPOINT,
                AddressSource.DIALED,
                last_learned=NOW - 60,
                first_success=NOW - 60,
                last_success=NOW,
                attempts=2,
                successes=2,
            ),
            NodeAddress(PEER_ID, OTHER_ENDPOINT, AddressSource.RELAYED, last_learned=NOW - 120),
        ),
        (ConnectionDirection.OUTBOUND,),
    )

    assert peer.value() == {
        "node_id": str(PEER_ID),
        "connected": ["outbound"],
        "stats": {
            "connection_attempts": 3,
            "successful_connections": 2,
            "remote_disconnects": 1,
            "last_connected": "2026-10-14T12:00:00Z",
            "connected_seconds": 90.5,
            "bytes_received": 1024,
            "bytes_sent": 256,
            "data_found": 4,
            "data_not_found": 1,
            "consecutive_failures": 0,
            "last_failure": "2026-10-14T11:00:00Z",
        },
        "addresses": [
            {
                "endpoint": ENDPOINT,
                "source": "dialed",
                "last_learned": "2026-10-14T11:59:00Z",
                "first_success": "2026-10-14T11:59:00Z",
                "last_success": "2026-10-14T12:00:00Z",
                "attempts": 2,
                "successes": 2,
                "consecutive_failures": 0,
            },
            {
                "endpoint": OTHER_ENDPOINT,
                "source": "relayed",
                "last_learned": "2026-10-14T11:58:00Z",
                "first_success": None,
                "last_success": None,
                "attempts": 0,
                "successes": 0,
                "consecutive_failures": 0,
            },
        ],
    }


def test_every_node_known_is_a_peer_but_this_one(database: StatsDatabase) -> None:
    database.record_connection_opened(PEER_ID, ENDPOINT)
    database.record_addresses({OTHER_ENDPOINT: OTHER_PEER_ID}, AddressSource.RELAYED)
    # Content this node makes is credited to it (Database Schema §4.2).
    database.record_transfer(SELF_ID, received_bytes=10)
    connected = ConnectedPeers()
    connected.named(ConnectionDirection.INBOUND, [INBOUND_ID])

    peers = {peer.stats.node_id: peer for peer in read(database, connected).peers}

    assert set(peers) == {PEER_ID, OTHER_PEER_ID, INBOUND_ID}
    assert peers[PEER_ID].stats == database.node_stats(PEER_ID)
    assert peers[PEER_ID].addresses == tuple(database.node_addresses(PEER_ID))
    # Known by an address alone, so with no statistics yet.
    assert peers[OTHER_PEER_ID].stats == NodeStats(OTHER_PEER_ID)
    assert [address.endpoint for address in peers[OTHER_PEER_ID].addresses] == [OTHER_ENDPOINT]
    # Connected as it dials in, with no row of either kind.
    assert peers[INBOUND_ID] == KnownPeer(NodeStats(INBOUND_ID), (), (ConnectionDirection.INBOUND,))


def test_those_connected_come_first_then_the_most_recently_connected_then_learned(
    database: StatsDatabase, now: list[float]
) -> None:
    connected_long_ago = ContentId.for_data(b"connected long ago", "sha256")
    connected_lately = ContentId.for_data(b"connected lately", "sha256")
    learned_lately = ContentId.for_data(b"learned of lately", "sha256")
    learned_long_ago = ContentId.for_data(b"learned of long ago", "sha256")
    database.record_connection_opened(connected_long_ago)
    now[0] += 10
    database.record_addresses({OTHER_ENDPOINT: learned_long_ago}, AddressSource.RELAYED)
    now[0] += 10
    database.record_connection_opened(connected_lately)
    now[0] += 10
    database.record_addresses({ENDPOINT: learned_lately}, AddressSource.RELAYED)
    connected = ConnectedPeers()
    connected.named(ConnectionDirection.INBOUND, [INBOUND_ID])

    def order(max_peers: int) -> list[ContentId]:
        return [peer.stats.node_id for peer in read(database, connected, max_peers).peers]

    assert order(10) == [
        INBOUND_ID,
        connected_lately,
        connected_long_ago,
        learned_lately,
        learned_long_ago,
    ]
    assert order(2) == [INBOUND_ID, connected_lately]


def test_the_peer_list_says_when_it_was_derived() -> None:
    peer = KnownPeer(NodeStats(PEER_ID))

    body = PeerList((peer,), NOW).body()

    assert b" " not in body
    assert loads(body) == {"derived": "2026-10-14T12:00:00Z", "peers": [peer.value()]}
