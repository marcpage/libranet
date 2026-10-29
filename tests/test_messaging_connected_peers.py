"""Tests for which peers are connected, each way, as ``peers.connected`` names them."""

from __future__ import annotations

from pytest import raises

from libranet.cas.content_id import ContentId
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.messaging.events import ConnectionDirection

PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
OTHER_PEER_ID = ContentId.for_data(b"another peer's public key", "sha256")


def test_the_peers_connected_each_way_are_replaced_when_named_again() -> None:
    connected = ConnectedPeers()

    assert connected.directions(PEER_ID) == ()

    connected.named(ConnectionDirection.INBOUND, [PEER_ID])
    connected.named(ConnectionDirection.OUTBOUND, [PEER_ID, OTHER_PEER_ID])

    assert connected.directions(PEER_ID) == (
        ConnectionDirection.OUTBOUND,
        ConnectionDirection.INBOUND,
    )

    connected.named(ConnectionDirection.OUTBOUND, [OTHER_PEER_ID])

    assert connected.directions(PEER_ID) == (ConnectionDirection.INBOUND,)
    assert connected.node_ids() == {PEER_ID, OTHER_PEER_ID}
    assert connected.node_ids(direction=ConnectionDirection.OUTBOUND) == {OTHER_PEER_ID}
    assert connected.node_ids(direction=ConnectionDirection.INBOUND) == {PEER_ID}


def test_the_payload_names_the_direction_and_the_peers_by_id_in_order() -> None:
    payload = ConnectedPeers.payload(ConnectionDirection.OUTBOUND, [PEER_ID, OTHER_PEER_ID])

    assert payload == {
        "direction": "outbound",
        "node_ids": sorted([str(PEER_ID), str(OTHER_PEER_ID)]),
    }


def test_the_peers_a_payload_names_are_read_back() -> None:
    connected = ConnectedPeers()

    connected.named_in(ConnectedPeers.payload(ConnectionDirection.INBOUND, [PEER_ID]))

    assert connected.directions(PEER_ID) == (ConnectionDirection.INBOUND,)
    assert connected.node_ids() == {PEER_ID}


def test_a_payload_naming_no_direction_is_refused_and_changes_nothing() -> None:
    connected = ConnectedPeers()
    connected.named(ConnectionDirection.INBOUND, [PEER_ID])

    with raises(ValueError):
        connected.named_in({"direction": "sideways", "node_ids": [str(OTHER_PEER_ID)]})

    assert connected.node_ids() == {PEER_ID}
