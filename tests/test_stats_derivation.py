"""Tests for deriving the plain node and seek list files, and the candidate and peer lists."""

from __future__ import annotations
from json import loads
from logging import WARNING
from pathlib import Path
from typing import Iterator

from pytest import LogCaptureFixture, MonkeyPatch, fixture

from libranet.cas.content_id import ContentId
from libranet.config.models import StatsConfig, StorageConfig
from libranet.messaging.connected_peers import ConnectedPeers
from libranet.messaging.events import AddressSource, ConnectionDirection
from libranet.protocol.karma_balances import KarmaBalance, KarmaBalances
from libranet.protocol.karma_head import KarmaHead
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import DerivedLists, ListDeriver
from libranet.stats.schema import SeekKind

SELF_ID = ContentId.for_data(b"this node's public key", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
OTHER_PEER_ID = ContentId.for_data(b"another peer's public key", "sha256")
THIRD_PEER_ID = ContentId.for_data(b"a third peer's public key", "sha256")
FOURTH_PEER_ID = ContentId.for_data(b"a fourth peer's public key", "sha256")
CONTENT_ID = ContentId.for_data(b"content this node wants", "sha256")
SELF_ENDPOINT = "http://localhost:8080"
DIRECT_ENDPOINT = "http://localhost:9090"
HEAD = KarmaHead(ContentId("sha256", "c1" * 32), 41, 1208)


@fixture
def database(storage: StorageConfig) -> Iterator[StatsDatabase]:
    with StatsDatabase(storage.database_path) as database:
        yield database


@fixture
def deriver(database: StatsDatabase, storage: StorageConfig) -> ListDeriver:
    return ListDeriver(database, storage, StatsConfig(), [SELF_ENDPOINT], SELF_ID)


def candidates(lists: DerivedLists) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = loads(lists.candidate_list.read_bytes())["nodes"]
    return nodes


def candidate_ids(lists: DerivedLists) -> list[object]:
    return [node["node_id"] for node in candidates(lists)]


def listed_ids(lists: DerivedLists) -> list[str]:
    return list(loads(lists.node_list.read_bytes())["nodes"].values())


def hold_karma(storage: StorageConfig, kismet: dict[ContentId, int]) -> None:
    """Write the balances the Karma module would, each node named holding its Kismet."""
    balances = {node_id: KarmaBalance(amount) for node_id, amount in kismet.items()}
    KarmaBalances(HEAD, balances).write(storage.karma_dir)


def test_the_node_list_leads_with_this_node(
    deriver: ListDeriver, database: StatsDatabase, storage: StorageConfig
) -> None:
    database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")

    lists = deriver.derive()

    assert lists.node_list == storage.node_list_path
    assert list(loads(lists.node_list.read_bytes())["nodes"]) == [
        SELF_ENDPOINT,
        "http://203.0.113.9:4300",
    ]


def test_every_endpoint_this_node_has_leads_the_node_list(
    database: StatsDatabase, storage: StorageConfig
) -> None:
    deriver = ListDeriver(
        database, storage, StatsConfig(), [SELF_ENDPOINT, DIRECT_ENDPOINT], SELF_ID
    )

    lists = deriver.derive()

    assert loads(lists.node_list.read_bytes())["nodes"] == {
        SELF_ENDPOINT: str(SELF_ID),
        DIRECT_ENDPOINT: str(SELF_ID),
    }


def test_a_stale_address_for_this_node_is_not_republished(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    database.record_address_worked(SELF_ID, "http://198.51.100.7:9999")

    lists = deriver.derive()

    assert loads(lists.node_list.read_bytes())["nodes"] == {SELF_ENDPOINT: str(SELF_ID)}
    assert candidates(lists) == []


def test_the_seek_list_holds_both_kinds_of_outstanding_request(
    deriver: ListDeriver, database: StatsDatabase, storage: StorageConfig
) -> None:
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
    database.record_seek(SeekKind.SEARCH, ["0123abcd"])
    # A peer's list is persisted but is not what this node advertises.
    database.record_seek(SeekKind.SEARCH, ["ffff"], PEER_ID)

    lists = deriver.derive()

    assert lists.seek_list == storage.seek_list_path
    assert loads(lists.seek_list.read_bytes()) == {
        "data": [str(CONTENT_ID)],
        "search": ["0123abcd"],
    }


def test_every_file_exists_even_with_nothing_to_report(
    deriver: ListDeriver, storage: StorageConfig
) -> None:
    lists = deriver.derive()

    assert loads(lists.node_list.read_bytes()) == {"nodes": {SELF_ENDPOINT: str(SELF_ID)}}
    assert loads(lists.seek_list.read_bytes()) == {"data": [], "search": []}
    assert lists.candidate_list == storage.candidate_list_path
    assert candidates(lists) == []
    assert lists.node_list_changed
    assert lists.candidate_list_changed
    assert lists.shunned_list == storage.shunned_list_path
    assert loads(lists.shunned_list.read_bytes()) == {"shunned": []}


def test_the_shunned_list_names_what_the_database_blocks(
    deriver: ListDeriver, database: StatsDatabase, storage: StorageConfig
) -> None:
    deriver.derive()
    database.record_shunned(CONTENT_ID)

    rewritten = deriver.write_shunned_list()
    again = deriver.write_shunned_list()

    assert rewritten
    assert not again
    assert loads(storage.shunned_list_path.read_bytes()) == {"shunned": [str(CONTENT_ID)]}


def test_a_derivation_that_changes_nothing_rewrites_nothing(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    first = deriver.derive()
    written_at = (
        first.node_list.stat().st_mtime_ns,
        first.seek_list.stat().st_mtime_ns,
        first.candidate_list.stat().st_mtime_ns,
    )

    unchanged = deriver.derive()

    assert not unchanged.node_list_changed
    assert not unchanged.candidate_list_changed
    assert (
        unchanged.node_list.stat().st_mtime_ns,
        unchanged.seek_list.stat().st_mtime_ns,
        unchanged.candidate_list.stat().st_mtime_ns,
    ) == written_at

    database.record_addresses({"http://203.0.113.9:4300": PEER_ID}, AddressSource.RELAYED)
    changed = deriver.derive()

    assert changed.candidate_list_changed
    # An address never reached is not published, and the seek list is untouched.
    assert not changed.node_list_changed
    assert changed.node_list.stat().st_mtime_ns == written_at[0]

    database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")

    assert deriver.derive().node_list_changed
    assert changed.seek_list.stat().st_mtime_ns == written_at[1]


def test_a_list_that_cannot_be_read_is_written_afresh_and_logged(
    deriver: ListDeriver, caplog: LogCaptureFixture, monkeypatch: MonkeyPatch
) -> None:
    first = deriver.derive()
    read_bytes = Path.read_bytes

    def unreadable(path: Path) -> bytes:
        if path == first.node_list:
            raise PermissionError(f"Permission denied: {path}")

        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", unreadable)
    written_at = first.node_list.stat().st_mtime_ns

    deriver.derive()

    assert first.node_list.stat().st_mtime_ns != written_at
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(f"Cannot read {first.node_list}")


def test_stale_outstanding_requests_stop_being_advertised(storage: StorageConfig) -> None:
    now = [1_000.0]
    database_with_clock = StatsDatabase(storage.database_path, clock=lambda: now[0])
    deriver = ListDeriver(
        database_with_clock,
        storage,
        StatsConfig(seek_entry_ttl_seconds=60.0),
        [SELF_ENDPOINT],
        SELF_ID,
    )

    with database_with_clock:
        database_with_clock.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
        now[0] += 90
        lists = deriver.derive()

    assert loads(lists.seek_list.read_bytes())["data"] == []


def test_only_addresses_that_worked_are_published_but_every_one_is_a_candidate(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    database.record_addresses({"http://203.0.113.9:4300": PEER_ID}, AddressSource.RELAYED)
    database.record_address_worked(OTHER_PEER_ID, "http://198.51.100.7:8080")
    database.record_addresses(
        {"http://other.example:8080": OTHER_PEER_ID}, AddressSource.ADVERTISED
    )

    lists = deriver.derive()

    assert list(loads(lists.node_list.read_bytes())["nodes"]) == [
        SELF_ENDPOINT,
        "http://198.51.100.7:8080",
    ]
    assert candidates(lists) == [
        {
            "node_id": str(OTHER_PEER_ID),
            "endpoints": ["http://198.51.100.7:8080", "http://other.example:8080"],
        },
        {"node_id": str(PEER_ID), "endpoints": ["http://203.0.113.9:4300"]},
    ]


def test_addresses_not_worth_trying_are_pruned_before_deriving(
    database: StatsDatabase, storage: StorageConfig
) -> None:
    deriver = ListDeriver(
        database,
        storage,
        StatsConfig(max_addresses_per_node=1, max_address_failures=2),
        [SELF_ENDPOINT],
        SELF_ID,
    )
    database.record_addresses(
        {"http://203.0.113.9:4300": PEER_ID, "http://198.51.100.7:8080": OTHER_PEER_ID},
        AddressSource.RELAYED,
    )
    database.record_addresses({"http://peer.example:4300": PEER_ID}, AddressSource.ADVERTISED)

    for _ in range(2):
        database.record_address_failed(OTHER_PEER_ID, "http://198.51.100.7:8080")

    lists = deriver.derive()

    assert candidates(lists) == [
        {"node_id": str(PEER_ID), "endpoints": ["http://peer.example:4300"]}
    ]


def test_a_node_given_up_on_is_left_out_of_the_candidate_list_but_not_forgotten(
    database: StatsDatabase, storage: StorageConfig
) -> None:
    deriver = ListDeriver(
        database, storage, StatsConfig(max_node_failures=2), [SELF_ENDPOINT], SELF_ID
    )
    database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")
    database.record_address_worked(OTHER_PEER_ID, "http://198.51.100.7:8080")

    for _ in range(2):
        database.record_node_unreached(PEER_ID)

    lists = deriver.derive()

    assert candidates(lists) == [
        {"node_id": str(OTHER_PEER_ID), "endpoints": ["http://198.51.100.7:8080"]}
    ]
    assert [address.endpoint for address in database.node_addresses(PEER_ID)] == [
        "http://203.0.113.9:4300"
    ]


def test_the_peer_list_names_the_peers_known_and_how_each_is_connected(
    database: StatsDatabase, storage: StorageConfig
) -> None:
    connected = ConnectedPeers()
    connected.named(ConnectionDirection.OUTBOUND, [PEER_ID])
    now = [1_791_979_200.0]
    deriver = ListDeriver(
        database,
        storage,
        StatsConfig(max_peers_listed=1),
        [SELF_ENDPOINT],
        SELF_ID,
        connected=connected,
        clock=lambda: now[0],
    )
    database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")
    database.record_address_worked(OTHER_PEER_ID, "http://198.51.100.7:8080")

    lists = deriver.derive()
    first = loads(lists.peer_list.read_bytes())
    now[0] += 60
    connected.named(ConnectionDirection.OUTBOUND, [])
    second = loads(deriver.derive().peer_list.read_bytes())

    assert lists.peer_list == storage.peer_list_path
    # The one connected, of the most listed.
    assert first["derived"] == "2026-10-14T12:00:00Z"
    assert [(peer["node_id"], peer["connected"]) for peer in first["peers"]] == [
        (str(PEER_ID), ["outbound"])
    ]
    # Written afresh, though no peer's statistics changed.
    assert second["derived"] == "2026-10-14T12:01:00Z"
    assert second["peers"][0]["connected"] == []


def test_the_node_list_puts_the_nodes_of_more_karma_first(storage: StorageConfig) -> None:
    now = [1_000.0]

    with StatsDatabase(storage.database_path, clock=lambda: now[0]) as database:
        deriver = ListDeriver(database, storage, StatsConfig(), [SELF_ENDPOINT], SELF_ID)

        for node_id, endpoint in [
            (PEER_ID, "http://203.0.113.9:4300"),
            (OTHER_PEER_ID, "http://198.51.100.7:8080"),
            (THIRD_PEER_ID, "http://192.0.2.4:4300"),
        ]:
            database.record_address_worked(node_id, endpoint)
            now[0] += 10

        unordered = listed_ids(deriver.derive())
        hold_karma(storage, {PEER_ID: 250, THIRD_PEER_ID: 10**14})
        ordered = listed_ids(deriver.derive())

    # The most recently reached first, while none holds Karma.
    assert unordered == [str(SELF_ID), str(THIRD_PEER_ID), str(OTHER_PEER_ID), str(PEER_ID)]
    # One of no balance is still listed, after those that hold some.
    assert ordered == [str(SELF_ID), str(THIRD_PEER_ID), str(PEER_ID), str(OTHER_PEER_ID)]


def test_the_node_list_cap_leaves_out_the_nodes_of_least_karma(storage: StorageConfig) -> None:
    now = [1_000.0]

    with StatsDatabase(storage.database_path, clock=lambda: now[0]) as database:
        # Room for this node and one peer.
        deriver = ListDeriver(
            database, storage, StatsConfig(max_list_bytes=250), [SELF_ENDPOINT], SELF_ID
        )
        database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")
        now[0] += 10
        database.record_address_worked(OTHER_PEER_ID, "http://198.51.100.7:8080")
        hold_karma(storage, {PEER_ID: 1})
        lists = deriver.derive()

    assert listed_ids(lists) == [str(SELF_ID), str(PEER_ID)]


def test_the_candidate_list_puts_nodes_reached_first_then_those_of_more_karma(
    storage: StorageConfig,
) -> None:
    now = [1_000.0]

    with StatsDatabase(storage.database_path, clock=lambda: now[0]) as database:
        deriver = ListDeriver(database, storage, StatsConfig(), [SELF_ENDPOINT], SELF_ID)
        database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")
        now[0] += 10
        database.record_address_worked(OTHER_PEER_ID, "http://198.51.100.7:8080")
        now[0] += 10
        database.record_addresses({"http://192.0.2.4:4300": THIRD_PEER_ID}, AddressSource.RELAYED)
        now[0] += 10
        database.record_addresses({"http://192.0.2.5:4300": FOURTH_PEER_ID}, AddressSource.RELAYED)
        unordered = candidate_ids(deriver.derive())
        hold_karma(storage, {PEER_ID: 250, THIRD_PEER_ID: 10**14})
        ordered = candidate_ids(deriver.derive())

    assert unordered == [str(OTHER_PEER_ID), str(PEER_ID), str(FOURTH_PEER_ID), str(THIRD_PEER_ID)]
    # A node never reached waits behind those reached, whatever it holds.
    assert ordered == [str(PEER_ID), str(OTHER_PEER_ID), str(THIRD_PEER_ID), str(FOURTH_PEER_ID)]


def test_nodes_are_not_ordered_by_karma_while_no_chain_is_followed(
    storage: StorageConfig,
) -> None:
    now = [1_000.0]

    with StatsDatabase(storage.database_path, clock=lambda: now[0]) as database:
        deriver = ListDeriver(database, storage, StatsConfig(), [SELF_ENDPOINT], SELF_ID)
        database.record_addresses({"http://203.0.113.9:4300": PEER_ID}, AddressSource.RELAYED)
        now[0] += 10
        database.record_addresses(
            {"http://198.51.100.7:8080": OTHER_PEER_ID}, AddressSource.RELAYED
        )
        hold_karma(storage, {PEER_ID: 250})
        ordered = candidate_ids(deriver.derive())
        KarmaBalances().write(storage.karma_dir)
        unordered = candidate_ids(deriver.derive())

    assert ordered == [str(PEER_ID), str(OTHER_PEER_ID)]
    assert unordered == [str(OTHER_PEER_ID), str(PEER_ID)]


def test_karma_balances_that_cannot_be_read_are_logged_and_order_nothing(
    deriver: ListDeriver,
    database: StatsDatabase,
    storage: StorageConfig,
    caplog: LogCaptureFixture,
) -> None:
    database.record_address_worked(PEER_ID, "http://203.0.113.9:4300")
    storage.karma_dir.mkdir(parents=True)
    (storage.karma_dir / "balances.json").write_text("{not JSON", encoding="utf-8")

    lists = deriver.derive()

    assert candidate_ids(lists) == [str(PEER_ID)]
    assert listed_ids(lists) == [str(SELF_ID), str(PEER_ID)]
    (record,) = caplog.records
    assert record.levelno == WARNING
    assert record.getMessage().startswith(
        f"Cannot read the Karma balances in {storage.karma_dir}, so nodes are not ordered"
    )
