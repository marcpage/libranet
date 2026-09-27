"""Tests for tracking which peers are connected to the web server."""

from __future__ import annotations
from typing import Any, Mapping

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.messaging.envelope import Message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.webserver.inbound_peers import InboundPeers

PEER = ContentId.for_data(b"a peer's key", "sha256")
OTHER = ContentId.for_data(b"another peer's key", "sha256")


class Recorder:
    """Stands in for the module's ``publish``, keeping each list of peers named."""

    def __init__(self) -> None:
        self.lists: list[list[str]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        assert event == EventType.PEERS_CONNECTED
        payload = dict(payload or {})
        assert payload["direction"] == ConnectionDirection.INBOUND
        self.lists.append(payload["node_ids"])
        return {}


@fixture
def published() -> Recorder:
    return Recorder()


@fixture
def peers(published: Recorder) -> InboundPeers:
    return InboundPeers(published)


def test_a_peer_is_named_from_its_first_verified_request_until_its_connection_closes(
    peers: InboundPeers, published: Recorder
) -> None:
    connection = peers.connection()

    connection.verified(PEER)
    connection.verified(PEER)
    connection.close()

    assert published.lists == [[str(PEER)], []]


def test_a_peer_stays_named_until_its_last_connection_closes(
    peers: InboundPeers, published: Recorder
) -> None:
    first, second = peers.connection(), peers.connection()
    first.verified(PEER)
    second.verified(PEER)

    first.close()
    assert published.lists == [[str(PEER)]]

    second.close()
    assert published.lists == [[str(PEER)], []]


def test_every_peer_connected_is_named_each_time(peers: InboundPeers, published: Recorder) -> None:
    first, second = peers.connection(), peers.connection()
    first.verified(PEER)
    second.verified(OTHER)

    first.close()

    assert published.lists == [[str(PEER)], sorted([str(PEER), str(OTHER)]), [str(OTHER)]]


def test_each_peer_signing_on_one_connection_counts_until_it_closes(
    peers: InboundPeers, published: Recorder
) -> None:
    connection = peers.connection()
    connection.verified(PEER)
    connection.verified(OTHER)

    connection.close()

    assert published.lists[-1] == []


def test_a_connection_no_peer_signed_on_names_nobody(
    peers: InboundPeers, published: Recorder
) -> None:
    peers.connection().close()

    assert published.lists == []


def test_asking_names_the_peers_connected_now(peers: InboundPeers, published: Recorder) -> None:
    peers.publish()
    peers.connection().verified(PEER)

    peers.publish()

    assert published.lists == [[], [str(PEER)], [str(PEER)]]
