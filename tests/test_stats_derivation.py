"""Tests for deriving the plain node and seek list files."""

from __future__ import annotations
from json import loads
from pathlib import Path
from typing import Iterator

from pytest import fixture

from libranet.cas.content_id import ContentId
from libranet.config.models import StatsConfig, StorageConfig
from libranet.stats.database import StatsDatabase
from libranet.stats.derivation import ListDeriver
from libranet.stats.schema import SeekKind

SELF_ID = ContentId.for_data(b"this node's public key", "sha256")
PEER_ID = ContentId.for_data(b"a peer's public key", "sha256")
OTHER_PEER_ID = ContentId.for_data(b"another peer's public key", "sha256")
CONTENT_ID = ContentId.for_data(b"content this node wants", "sha256")
SELF_ENDPOINT = "http://localhost:8080"


@fixture
def storage(tmp_path: Path) -> StorageConfig:
    return StorageConfig(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")


@fixture
def database(storage: StorageConfig) -> Iterator[StatsDatabase]:
    with StatsDatabase(storage.database_path) as database:
        yield database


@fixture
def deriver(database: StatsDatabase, storage: StorageConfig) -> ListDeriver:
    return ListDeriver(database, storage, StatsConfig(), SELF_ENDPOINT, SELF_ID)


def test_the_node_list_leads_with_this_node(
    deriver: ListDeriver, database: StatsDatabase, storage: StorageConfig
) -> None:
    database.record_endpoint(PEER_ID, "http://203.0.113.9:4300")

    lists = deriver.derive()

    assert lists.node_list == storage.node_list_path
    assert list(loads(lists.node_list.read_bytes())["nodes"]) == [
        SELF_ENDPOINT,
        "http://203.0.113.9:4300",
    ]


def test_a_stale_address_for_this_node_is_not_republished(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    database.record_endpoint(SELF_ID, "http://198.51.100.7:9999")

    lists = deriver.derive()

    assert loads(lists.node_list.read_bytes())["nodes"] == {SELF_ENDPOINT: str(SELF_ID)}


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


def test_both_files_exist_even_with_nothing_to_report(deriver: ListDeriver) -> None:
    lists = deriver.derive()

    assert loads(lists.node_list.read_bytes()) == {"nodes": {SELF_ENDPOINT: str(SELF_ID)}}
    assert loads(lists.seek_list.read_bytes()) == {"data": [], "search": []}
    assert (lists.node_list_changed, lists.seek_list_changed) == (True, True)


def test_a_derivation_that_changes_nothing_rewrites_nothing(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    first = deriver.derive()
    written_at = first.node_list.stat().st_mtime_ns

    unchanged = deriver.derive()

    assert (unchanged.node_list_changed, unchanged.seek_list_changed) == (False, False)
    assert unchanged.node_list.stat().st_mtime_ns == written_at

    database.record_endpoint(PEER_ID, "http://203.0.113.9:4300")
    changed = deriver.derive()

    assert (changed.node_list_changed, changed.seek_list_changed) == (True, False)


def test_stale_outstanding_requests_stop_being_advertised(
    database: StatsDatabase, storage: StorageConfig
) -> None:
    now = [1_000.0]
    database_with_clock = StatsDatabase(storage.database_path, clock=lambda: now[0])
    deriver = ListDeriver(
        database_with_clock,
        storage,
        StatsConfig(seek_entry_ttl_seconds=60.0),
        SELF_ENDPOINT,
        SELF_ID,
    )

    with database_with_clock:
        database_with_clock.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
        now[0] += 90
        lists = deriver.derive()

    assert loads(lists.seek_list.read_bytes())["data"] == []


def test_peers_never_connected_to_are_still_offered(
    deriver: ListDeriver, database: StatsDatabase
) -> None:
    database.record_endpoint(PEER_ID, "http://203.0.113.9:4300")
    database.record_connection_opened(OTHER_PEER_ID, "http://198.51.100.7:8080")

    lists = deriver.derive()

    assert list(loads(lists.node_list.read_bytes())["nodes"]) == [
        SELF_ENDPOINT,
        "http://198.51.100.7:8080",
        "http://203.0.113.9:4300",
    ]
