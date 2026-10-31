"""Tests for tracking which peers are connected to the web server."""

from __future__ import annotations
from json import dumps
from pathlib import Path
from typing import Any, Mapping

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.config.models import StorageConfig
from libranet.messaging.envelope import Message
from libranet.messaging.events import ConnectionDirection, EventType
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead
from libranet.webserver.client_keeping import ClientKeeping, HeldKarma, SoughtContent
from libranet.webserver.inbound_peers import InboundPeers

PEER = ContentId.for_data(b"a peer's key", "sha256")
OTHER = ContentId.for_data(b"another peer's key", "sha256")
SOUGHT = ContentId.for_data(b"what this node seeks", "sha256")
VALUE_SECONDS = 600.0


class Recorder:
    """Stands in for the module's ``publish``, keeping each list of peers named."""

    def __init__(self) -> None:
        self.lists: list[list[str]] = []

    def __call__(self, event: EventType, payload: Mapping[str, Any] | None = None) -> Message:
        assert event == EventType.PEERS_CONNECTED
        payload = dict(payload or {})
        # Sent as its value, as every enum in a payload is.
        assert not isinstance(payload["direction"], ConnectionDirection)
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


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def kept_peers(tmp_path: Path, published: Recorder, clock: Clock) -> InboundPeers:
    """Peers kept for one request, and for as long as they push what is sought, unless rich.

    :data:`OTHER` holds more Karma than :data:`PEER`.
    """
    storage = StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    head = KarmaHead(ContentId("sha256", "c1" * 32), 4, 96)
    KarmaBalances(head, {OTHER: Balance(Kismet(250), 3)}).write(storage.karma_dir)
    storage.seek_list_path.parent.mkdir(parents=True)
    storage.seek_list_path.write_text(dumps({"data": [str(SOUGHT)]}))
    keeping = ClientKeeping(
        1,
        VALUE_SECONDS,
        SoughtContent.of(storage),
        HeldKarma(storage.karma_dir),
        keep_count=0,
        clock=clock,
    )
    return InboundPeers(published, keeping=keeping)


def test_every_connection_is_kept_without_keeping(peers: InboundPeers) -> None:
    connection = peers.connection()
    connection.verified(PEER)

    assert all(connection.served() for _ in range(100))


def test_a_connection_is_closed_once_its_peer_of_low_karma_has_added_nothing(
    kept_peers: InboundPeers, clock: Clock
) -> None:
    connection, other = kept_peers.connection(), kept_peers.connection()
    connection.verified(PEER)
    other.verified(OTHER)
    clock.now += VALUE_SECONDS

    assert [connection.served(), connection.served()] == [True, False]
    assert [other.served(), other.served()] == [True, True]


def test_requests_on_every_connection_of_a_peer_count_toward_its_grace(
    kept_peers: InboundPeers, clock: Clock
) -> None:
    first, second, other = (kept_peers.connection() for _ in range(3))
    first.verified(PEER)
    second.verified(PEER)
    other.verified(OTHER)
    clock.now += VALUE_SECONDS

    assert [first.served(), second.served()] == [True, False]


def test_a_peer_pushing_what_is_sought_on_a_connection_keeps_it(
    kept_peers: InboundPeers, clock: Clock
) -> None:
    connection, other = kept_peers.connection(), kept_peers.connection()
    connection.verified(PEER)
    other.verified(OTHER)
    connection.served()
    clock.now += VALUE_SECONDS - 1
    connection.pushed(PEER, SOUGHT)
    clock.now += 1

    assert connection.served()


def test_a_connection_is_closed_once_any_peer_signing_on_it_is(
    kept_peers: InboundPeers, clock: Clock
) -> None:
    connection = kept_peers.connection()
    connection.verified(OTHER)
    connection.verified(PEER)
    clock.now += VALUE_SECONDS

    assert [connection.served(), connection.served()] == [True, False]


def test_a_connection_no_peer_signed_on_is_kept(kept_peers: InboundPeers, clock: Clock) -> None:
    connection = kept_peers.connection()
    clock.now += VALUE_SECONDS

    assert all(connection.served() for _ in range(3))


def test_a_peer_whose_last_connection_closed_starts_afresh(
    kept_peers: InboundPeers, clock: Clock
) -> None:
    other = kept_peers.connection()
    other.verified(OTHER)
    first = kept_peers.connection()
    first.verified(PEER)
    first.served()
    clock.now += VALUE_SECONDS
    first.close()
    again = kept_peers.connection()
    again.verified(PEER)

    assert [again.served(), again.served()] == [True, True]
