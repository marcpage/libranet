"""Tests for deriving the plain node and seek list files, and the candidate list."""

from __future__ import annotations
from json import loads
from pathlib import Path
from typing import Iterator

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.config.models import StatsConfig, StorageConfig
from libranet.messaging.events import AddressSource
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import DerivedLists, ListDeriver
from libranet.stats.schema import SeekKind

SELF_ID = ContentId.for_data(b"this node's public key", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
OTHER_PEER_ID = ContentId.for_data(b"another peer's public key", "sha256")
CONTENT_ID = ContentId.for_data(b"content this node wants", "sha256")
SELF_ENDPOINT = "http://localhost:8080"
DIRECT_ENDPOINT = "http://localhost:9090"


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


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
    assert lists.candidate_list_changed


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
    assert changed.node_list.stat().st_mtime_ns == written_at[0]
    assert changed.seek_list.stat().st_mtime_ns == written_at[1]


def test_stale_outstanding_requests_stop_being_advertised(
    database: StatsDatabase, storage: StorageConfig
) -> None:
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
