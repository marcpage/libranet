"""Tests for keeping the clients connected to the web server by their Karma."""

from __future__ import annotations
from json import dumps
from logging import WARNING
from pathlib import Path

from pytest import LogCaptureFixture, fixture

from libranet.cas.content_id import ContentId
from libranet.config.models import LibranetConfig, NetworkConfig, StorageConfig
from libranet.identity.node_identity import NodeIdentity
from libranet.protocol.karma_amounts import Kismet
from libranet.protocol.karma_balances import Balance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead
from libranet.webserver.client_keeping import ClientKeeping, HeldKarma, SoughtContent

GRACE_REQUESTS = 3
VALUE_SECONDS = 600.0
HEAD = KarmaHead(ContentId("sha256", "c1" * 32), 4, 96)
POOR = ContentId("sha256", "a0" * 32)
RICH = ContentId("sha256", "b0" * 32)
NODE = ContentId("sha256", "90" * 32)
SOUGHT = ContentId("sha256", "d0" * 32)
UNSOUGHT = ContentId("sha256", "e0" * 32)
# The fewest bits what a search finds shares with it.
MINIMUM_BITS = 16


class Clock:
    """A clock a test moves on."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", search_min_bits=MINIMUM_BITS
    )


@fixture
def clock() -> Clock:
    return Clock()


@fixture
def keeping(storage: StorageConfig, clock: Clock) -> ClientKeeping:
    """Keeping that has :data:`RICH` hold more Karma than :data:`POOR`, both connected.

    :data:`NODE`, this node, holds more than either.
    """
    KarmaBalances(HEAD, {RICH: Balance(Kismet(250), 3), NODE: Balance(Kismet(1_000), 3)}).write(
        storage.karma_dir
    )
    seek_list(storage, data=[str(SOUGHT)])
    keeping = ClientKeeping(
        GRACE_REQUESTS,
        VALUE_SECONDS,
        SoughtContent.of(storage),
        HeldKarma(storage.karma_dir),
        keep_count=0,
        node_id=lambda: NODE,
        clock=clock,
    )
    keeping.connected(POOR)
    keeping.connected(RICH)
    return keeping


def seek_list(storage: StorageConfig, *, data: list[str], search: list[str] | None = None) -> None:
    """Write this node's own seek list, as the stats module derives it."""
    storage.seek_list_path.parent.mkdir(parents=True, exist_ok=True)
    storage.seek_list_path.write_text(dumps({"data": data, "search": search or []}))


def served(keeping: ClientKeeping, client: ContentId, requests: int) -> list[bool]:
    return [keeping.served(client) for _ in range(requests)]


def keeping_of(
    storage: StorageConfig, clock: Clock, held: dict[ContentId, int], *, keep_count: int = 0
) -> ClientKeeping:
    """Keeping past every client's grace, each of ``held`` holding that much, keeping that many.

    :data:`NODE`, this node, holds as ``held`` says, or else more than any client.
    """
    KarmaBalances(
        HEAD,
        {client: Balance(Kismet(kismet), 3) for client, kismet in {NODE: 1_000, **held}.items()},
    ).write(storage.karma_dir)
    return ClientKeeping(
        0,
        VALUE_SECONDS,
        SoughtContent.of(storage),
        HeldKarma(storage.karma_dir),
        keep_count=keep_count,
        node_id=lambda: NODE,
        clock=clock,
    )


def test_a_client_is_kept_for_its_first_requests_whatever_its_karma(
    keeping: ClientKeeping, clock: Clock
) -> None:
    clock.now += VALUE_SECONDS

    assert served(keeping, POOR, GRACE_REQUESTS + 1) == [True] * GRACE_REQUESTS + [False]


def test_a_client_of_low_karma_is_kept_until_it_has_added_nothing_for_long_enough(
    keeping: ClientKeeping, clock: Clock
) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS - 1

    assert keeping.served(POOR)

    clock.now += 1

    assert not keeping.served(POOR)


def test_a_client_of_low_karma_that_pushes_what_is_sought_is_kept(
    keeping: ClientKeeping, clock: Clock
) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS - 1
    keeping.pushed(POOR, SOUGHT)
    clock.now += VALUE_SECONDS - 1

    assert keeping.served(POOR)


def test_pushing_what_is_not_sought_keeps_no_client(keeping: ClientKeeping, clock: Clock) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS - 1
    keeping.pushed(POOR, UNSOUGHT)
    clock.now += 1

    assert not keeping.served(POOR)


def test_what_a_search_sought_would_find_keeps_the_client_pushing_it(
    storage: StorageConfig, keeping: ClientKeeping, clock: Clock
) -> None:
    seek_list(storage, data=[], search=["e0e1"])
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS - 1
    # Sharing 15 bits with the search, one fewer than a search finds.
    keeping.pushed(POOR, UNSOUGHT)
    keeping.pushed(POOR, SOUGHT)
    clock.now += 1

    assert not keeping.served(POOR)

    keeping.pushed(POOR, ContentId("sha256", "e0e1" + "ff" * 30))

    assert keeping.served(POOR)


def test_an_idle_client_of_high_karma_is_kept(keeping: ClientKeeping, clock: Clock) -> None:
    clock.now += VALUE_SECONDS

    assert served(keeping, RICH, GRACE_REQUESTS + 2) == [True] * (GRACE_REQUESTS + 2)


def test_no_client_is_closed_while_every_one_holds_as_much_as_the_others(
    storage: StorageConfig, keeping: ClientKeeping, clock: Clock
) -> None:
    KarmaBalances().write(storage.karma_dir)
    clock.now += VALUE_SECONDS

    assert served(keeping, POOR, GRACE_REQUESTS + 1) == [True] * (GRACE_REQUESTS + 1)


def test_a_client_alone_holds_the_median_and_is_kept(keeping: ClientKeeping, clock: Clock) -> None:
    keeping.disconnected(RICH)
    clock.now += VALUE_SECONDS

    assert served(keeping, POOR, GRACE_REQUESTS + 1) == [True] * (GRACE_REQUESTS + 1)


def test_no_client_is_closed_unless_more_than_the_keep_count_are_connected(
    storage: StorageConfig, clock: Clock
) -> None:
    other = ContentId("sha256", "f0" * 32)
    keeping = keeping_of(storage, clock, {RICH: 250, other: 250}, keep_count=2)
    keeping.connected(POOR)
    keeping.connected(RICH)
    clock.now += VALUE_SECONDS

    assert keeping.served(POOR)

    keeping.connected(other)

    assert not keeping.served(POOR)


def test_a_client_below_the_median_holding_as_much_as_this_node_or_more_is_kept(
    storage: StorageConfig, clock: Clock
) -> None:
    held = {POOR: 100, RICH: 300, SOUGHT: 400, NODE: 100}
    keeping = keeping_of(storage, clock, held)

    for client in (POOR, RICH, SOUGHT):
        keeping.connected(client)

    clock.now += VALUE_SECONDS

    assert keeping.served(POOR)


def test_a_client_below_both_the_median_and_this_node_is_closed_saying_both(
    storage: StorageConfig, clock: Clock, caplog: LogCaptureFixture
) -> None:
    held = {POOR: 99, RICH: 300, SOUGHT: 400, NODE: 100}
    keeping = keeping_of(storage, clock, held)

    for client in (POOR, RICH, SOUGHT):
        keeping.connected(client)

    clock.now += VALUE_SECONDS

    with caplog.at_level("INFO"):
        assert not keeping.served(POOR)

    assert "below both the median of 300 of the 3 clients connected and the 100" in caplog.text


def test_a_client_below_this_node_but_not_the_median_is_kept(
    storage: StorageConfig, clock: Clock
) -> None:
    keeping = keeping_of(storage, clock, {POOR: 100, RICH: 300, SOUGHT: 400, NODE: 500})

    for client in (POOR, RICH, SOUGHT):
        keeping.connected(client)

    clock.now += VALUE_SECONDS

    assert keeping.served(RICH)
    assert keeping.served(SOUGHT)


def test_a_node_holding_no_karma_closes_no_client(storage: StorageConfig, clock: Clock) -> None:
    keeping = keeping_of(storage, clock, {RICH: 250, NODE: 0})
    keeping.connected(POOR)
    keeping.connected(RICH)
    clock.now += VALUE_SECONDS

    assert keeping.served(POOR)


def test_those_being_closed_do_not_count_toward_those_kept(
    storage: StorageConfig, clock: Clock
) -> None:
    clients = [ContentId("sha256", f"{byte:02x}" * 32) for byte in range(5)]
    held = {client: 10 * (index + 1) for index, client in enumerate(clients)}
    keeping = keeping_of(storage, clock, held, keep_count=3)

    for client in clients:
        keeping.connected(client)

    clock.now += VALUE_SECONDS

    # None of them has yet gone, as each still has a request being answered.
    assert [keeping.served(client) for client in clients[:3]] == [False, False, True]


def test_each_connection_of_a_client_being_closed_is_closed_as_it_is_next_answered(
    keeping: ClientKeeping, clock: Clock, caplog: LogCaptureFixture
) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS

    with caplog.at_level("INFO"):
        assert served(keeping, POOR, 2) == [False, False]

    assert caplog.text.count("Closing the connection") == 1


def test_a_client_closed_that_connects_again_starts_afresh(
    keeping: ClientKeeping, clock: Clock
) -> None:
    clock.now += VALUE_SECONDS
    served(keeping, POOR, GRACE_REQUESTS + 1)
    keeping.disconnected(POOR)
    keeping.connected(POOR)

    assert served(keeping, POOR, GRACE_REQUESTS) == [True] * GRACE_REQUESTS


def test_a_client_that_connects_again_starts_afresh(keeping: ClientKeeping, clock: Clock) -> None:
    clock.now += VALUE_SECONDS
    served(keeping, POOR, GRACE_REQUESTS)
    keeping.disconnected(POOR)
    keeping.connected(POOR)

    assert served(keeping, POOR, GRACE_REQUESTS + 1) == [True] * (GRACE_REQUESTS + 1)


def test_a_client_not_connected_is_never_closed(keeping: ClientKeeping, clock: Clock) -> None:
    keeping.disconnected(POOR)
    keeping.pushed(POOR, SOUGHT)
    clock.now += VALUE_SECONDS

    assert served(keeping, POOR, GRACE_REQUESTS + 1) == [True] * (GRACE_REQUESTS + 1)


def test_closing_a_client_is_logged_with_its_karma(
    keeping: ClientKeeping, clock: Clock, caplog: LogCaptureFixture
) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    clock.now += VALUE_SECONDS

    with caplog.at_level("INFO"):
        keeping.served(POOR)

    assert f"Closing the connection of {POOR}, which holds 0 Kismet" in caplog.text


def test_karma_that_cannot_be_read_is_warned_of_once_and_closes_no_client(
    storage: StorageConfig, clock: Clock, caplog: LogCaptureFixture
) -> None:
    (storage.karma_dir / "balances.json").parent.mkdir(parents=True)
    (storage.karma_dir / "balances.json").write_text("not JSON")
    seek_list(storage, data=[])
    keeping = ClientKeeping(
        0,
        VALUE_SECONDS,
        SoughtContent.of(storage),
        HeldKarma(storage.karma_dir),
        keep_count=0,
        node_id=lambda: NODE,
        clock=clock,
    )
    keeping.connected(POOR)
    keeping.connected(RICH)
    clock.now += VALUE_SECONDS

    with caplog.at_level(WARNING):
        kept = served(keeping, POOR, 2)

    assert kept == [True, True]
    assert caplog.text.count("Cannot read the Karma balances") == 1


def test_karma_that_cannot_be_read_again_leaves_what_was_read_before(
    storage: StorageConfig, keeping: ClientKeeping, clock: Clock, caplog: LogCaptureFixture
) -> None:
    clock.now += VALUE_SECONDS
    # Past its grace, so that the balances are read.
    served(keeping, RICH, GRACE_REQUESTS + 1)
    served(keeping, POOR, GRACE_REQUESTS)
    (storage.karma_dir / "balances.json").write_text('{"balances": []}')

    with caplog.at_level(WARNING):
        assert not keeping.served(POOR)

    assert "Cannot read the Karma balances" in caplog.text


def test_a_seek_list_that_cannot_be_read_leaves_what_was_read_before(
    storage: StorageConfig, keeping: ClientKeeping, clock: Clock, caplog: LogCaptureFixture
) -> None:
    served(keeping, POOR, GRACE_REQUESTS)
    keeping.pushed(POOR, UNSOUGHT)
    storage.seek_list_path.write_text('{"data": "not an array"}')
    clock.now += VALUE_SECONDS - 1

    with caplog.at_level(WARNING):
        keeping.pushed(POOR, SOUGHT)

    clock.now += 1

    assert keeping.served(POOR)
    assert "Cannot read this node's own seek list" in caplog.text


def test_nothing_is_sought_before_the_seek_list_is_derived(storage: StorageConfig) -> None:
    assert not SoughtContent.of(storage).seeks(SOUGHT)
    assert not SoughtContent.of(storage).searches_for("e0e1")


def test_a_search_the_seek_list_names_is_searched_for(storage: StorageConfig) -> None:
    seek_list(storage, data=[], search=["e0e1"])
    sought = SoughtContent.of(storage)

    assert sought.searches_for("e0e1")
    assert not sought.searches_for("e0e2")


def test_no_identity_holds_karma_before_the_balances_are_written(storage: StorageConfig) -> None:
    assert HeldKarma(storage.karma_dir).kismet([RICH]) == {RICH: 0}


def test_keeping_follows_the_configuration(storage: StorageConfig, clock: Clock) -> None:
    config = LibranetConfig(
        network=NetworkConfig(
            client_grace_requests=1, client_value_seconds=5.0, client_keep_count=1
        ),
        storage=storage,
    )
    identity = NodeIdentity.load_or_create(config)
    KarmaBalances(
        HEAD, {RICH: Balance(Kismet(250), 3), identity.node_id: Balance(Kismet(1_000), 3)}
    ).write(storage.karma_dir)
    keeping = ClientKeeping.of(config, clock=clock)
    keeping.connected(POOR)
    keeping.connected(RICH)
    clock.now += 5.0

    assert served(keeping, POOR, 2) == [True, False]
