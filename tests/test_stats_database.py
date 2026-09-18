"""Tests for the statistics database, against a temp SQLite file."""

from __future__ import annotations
from pathlib import Path
from typing import Iterator

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.stats.database import StatsDatabase
from libranet.stats.schema import SeekKind

CONTENT_ID = ContentId.for_data(b"some content", "sha256")
OTHER_ID = ContentId.for_data(b"other content", "sha256")
NODE_ID = ContentId.for_data(b"a node's public key", "sha256")
OTHER_NODE_ID = ContentId.for_data(b"another node's public key", "sha256")


class FakeClock:
    """A clock the tests move by hand, so durations are exact."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@fixture
def clock() -> FakeClock:
    return FakeClock()


@fixture
def database(tmp_path: Path, clock: FakeClock) -> Iterator[StatsDatabase]:
    with StatsDatabase(tmp_path / "stats" / "libranet.sqlite3", clock=clock) as database:
        yield database


def test_the_database_file_and_its_directory_are_created(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "libranet.sqlite3"

    with StatsDatabase(path):
        assert path.is_file()


def test_reopening_an_existing_database_keeps_what_it_holds(tmp_path: Path) -> None:
    path = tmp_path / "libranet.sqlite3"

    with StatsDatabase(path) as first:
        first.record_request(CONTENT_ID, external=True)

    with StatsDatabase(path) as second:
        stats = second.data_stats(CONTENT_ID)

    assert stats is not None
    assert stats.external_requests == 1


def test_nothing_is_known_about_unseen_content_or_nodes(database: StatsDatabase) -> None:
    assert database.data_stats(CONTENT_ID) is None
    assert database.node_stats(NODE_ID) is None


def test_requests_are_counted_by_where_they_came_from(database: StatsDatabase) -> None:
    database.record_request(CONTENT_ID, external=True)
    database.record_request(CONTENT_ID, external=True)
    database.record_request(CONTENT_ID, external=False)

    stats = database.data_stats(CONTENT_ID)

    assert stats is not None
    assert stats.content_id == CONTENT_ID
    assert (stats.external_requests, stats.internal_requests) == (2, 1)
    assert stats.requests == 3


def test_pushes_are_counted(database: StatsDatabase) -> None:
    database.record_push(CONTENT_ID)
    database.record_push(CONTENT_ID)

    stats = database.data_stats(CONTENT_ID)

    assert stats is not None
    assert stats.pushes == 2


def test_acquiring_records_when_it_happened(database: StatsDatabase, clock: FakeClock) -> None:
    database.record_acquired(CONTENT_ID)
    clock.advance(30)
    database.record_acquired(CONTENT_ID)

    stats = database.data_stats(CONTENT_ID)

    assert stats is not None
    assert stats.last_acquired == clock.now


def test_deleting_accumulates_how_long_content_was_held(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_acquired(CONTENT_ID)
    clock.advance(60)
    database.record_deleted(CONTENT_ID)
    clock.advance(10)
    database.record_acquired(CONTENT_ID)
    clock.advance(5)
    database.record_deleted(CONTENT_ID)

    stats = database.data_stats(CONTENT_ID)

    assert stats is not None
    assert stats.deletes == 2
    assert stats.stored_seconds == 65
    # The last time it was acquired stays true after the copy is gone.
    assert stats.last_acquired == clock.now - 5


def test_deleting_content_never_acquired_adds_no_time(database: StatsDatabase) -> None:
    database.record_deleted(CONTENT_ID)

    stats = database.data_stats(CONTENT_ID)

    assert stats is not None
    assert (stats.deletes, stats.stored_seconds, stats.last_acquired) == (1, 0, None)


def test_an_opened_connection_counts_as_an_attempt_too(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_connection_attempt(NODE_ID)
    database.record_connection_opened(NODE_ID, "http://198.51.100.7:8080")

    stats = database.node_stats(NODE_ID)

    assert stats is not None
    assert stats.node_id == NODE_ID
    assert (stats.connection_attempts, stats.successful_connections) == (2, 1)
    assert stats.last_connected == clock.now
    assert database.known_endpoints() == [("http://198.51.100.7:8080", str(NODE_ID))]


def test_closing_accumulates_connected_time_and_counts_remote_closes(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_connection_opened(NODE_ID)
    clock.advance(120)
    database.record_connection_closed(NODE_ID, remote=True)
    connected_at = clock.now - 120
    clock.advance(5)
    database.record_connection_opened(NODE_ID)
    clock.advance(45)
    database.record_connection_closed(NODE_ID, remote=False)

    stats = database.node_stats(NODE_ID)

    assert stats is not None
    assert stats.connected_seconds == 165
    assert stats.remote_disconnects == 1
    assert stats.last_connected == connected_at + 125


def test_closing_a_connection_to_an_unknown_node_records_nothing(
    database: StatsDatabase,
) -> None:
    database.record_connection_closed(NODE_ID, remote=True)

    assert database.node_stats(NODE_ID) is None


def test_transferred_bytes_accumulate_per_direction(database: StatsDatabase) -> None:
    database.record_transfer(NODE_ID, received=1024)
    database.record_transfer(NODE_ID, received=512, sent=256)
    database.record_transfer(NODE_ID)

    stats = database.node_stats(NODE_ID)

    assert stats is not None
    assert (stats.bytes_received, stats.bytes_sent) == (1536, 256)


def test_fetch_outcomes_are_counted_per_node(database: StatsDatabase) -> None:
    database.record_data_lookup(NODE_ID, found=True)
    database.record_data_lookup(NODE_ID, found=False)
    database.record_data_lookup(NODE_ID, found=False)

    stats = database.node_stats(NODE_ID)

    assert stats is not None
    assert (stats.data_found, stats.data_not_found) == (1, 2)


def test_a_node_keeps_only_the_address_it_was_last_seen_at(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_endpoint(NODE_ID, "http://198.51.100.7:8080")
    clock.advance(10)
    database.record_endpoint(NODE_ID, "http://203.0.113.9:4300")

    assert database.known_endpoints() == [("http://203.0.113.9:4300", str(NODE_ID))]


def test_known_endpoints_put_the_most_recently_connected_first(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_endpoints(
        {"http://198.51.100.7:8080": NODE_ID, "http://203.0.113.9:4300": OTHER_NODE_ID}
    )
    clock.advance(10)
    database.record_connection_opened(OTHER_NODE_ID)

    assert database.known_endpoints() == [
        ("http://203.0.113.9:4300", str(OTHER_NODE_ID)),
        ("http://198.51.100.7:8080", str(NODE_ID)),
    ]


def test_known_endpoints_can_leave_this_node_out(database: StatsDatabase) -> None:
    database.record_endpoints(
        {"http://198.51.100.7:8080": NODE_ID, "http://203.0.113.9:4300": OTHER_NODE_ID}
    )

    assert database.known_endpoints(exclude=NODE_ID) == [
        ("http://203.0.113.9:4300", str(OTHER_NODE_ID))
    ]


def test_seek_entries_are_kept_apart_per_node(database: StatsDatabase) -> None:
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
    database.record_seek(SeekKind.DATA, [str(OTHER_ID)], NODE_ID)
    database.record_seek(SeekKind.SEARCH, ["abcd"])

    assert database.seek_values(SeekKind.DATA) == [str(CONTENT_ID)]
    assert database.seek_values(SeekKind.DATA, NODE_ID) == [str(OTHER_ID)]
    assert database.seek_values(SeekKind.SEARCH) == ["abcd"]
    assert database.seek_values(SeekKind.SEARCH, NODE_ID) == []


def test_seek_entries_are_listed_most_recently_wanted_first(
    database: StatsDatabase, clock: FakeClock
) -> None:
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
    clock.advance(1)
    database.record_seek(SeekKind.DATA, [str(OTHER_ID)])
    clock.advance(1)
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID)])

    assert database.seek_values(SeekKind.DATA) == [str(CONTENT_ID), str(OTHER_ID)]


def test_a_satisfied_request_is_cleared(database: StatsDatabase) -> None:
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID), str(OTHER_ID)])
    database.clear_seek(SeekKind.DATA, str(CONTENT_ID))

    assert database.seek_values(SeekKind.DATA) == [str(OTHER_ID)]


def test_stale_seek_entries_are_pruned(database: StatsDatabase, clock: FakeClock) -> None:
    database.record_seek(SeekKind.DATA, [str(CONTENT_ID)])
    database.record_seek(SeekKind.SEARCH, ["abcd"], NODE_ID)
    clock.advance(100)
    database.record_seek(SeekKind.DATA, [str(OTHER_ID)])
    clock.advance(10)

    assert database.prune_seek(60) == 2
    assert database.seek_values(SeekKind.DATA) == [str(OTHER_ID)]
    assert database.seek_values(SeekKind.SEARCH, NODE_ID) == []


def store_hashes(database: StatsDatabase, *hashes: str) -> None:
    """Make the database aware of each hash, as a request for it would."""
    for hash_value in hashes:
        database.record_request(ContentId("sha256", hash_value), external=True)


def test_a_match_above_the_prefix_can_beat_one_below_it(database: StatsDatabase) -> None:
    # `7f...` is the nearest hash below the query and `90...` the nearest at
    # or above it, but only `90...` shares the query's top bit.
    store_hashes(database, "7" + "f" * 63, "9" + "0" * 63)

    assert database.content_ids_near("8" + "0" * 63, 1) == [ContentId("sha256", "9" + "0" * 63)]


def test_a_match_below_the_prefix_can_beat_one_above_it(database: StatsDatabase) -> None:
    # The same the other way around: `80...` sorts below the query but shares
    # a whole digit with it, while `ff...` shares one bit.
    store_hashes(database, "8" + "0" * 63, "f" * 64)

    assert database.content_ids_near("88" + "0" * 62, 1) == [ContentId("sha256", "8" + "0" * 63)]


def test_a_nearby_scan_returns_no_more_than_the_limit(database: StatsDatabase) -> None:
    store_hashes(database, *(f"{index}" + "0" * 63 for index in range(10)))

    found = database.content_ids_near("5" + "0" * 63, 3)

    # `5` matches exactly, `4` shares three bits of the first digit, `6` two.
    assert [content_id.hash[0] for content_id in found] == ["5", "4", "6"]


def test_a_nearby_scan_returns_everything_known_when_that_is_under_the_limit(
    database: StatsDatabase,
) -> None:
    store_hashes(database, "5" + "0" * 63, "6" + "0" * 63)

    assert len(database.content_ids_near("5" + "0" * 63, 8)) == 2


def test_a_nearby_scan_needs_room_for_at_least_one_result(database: StatsDatabase) -> None:
    with raises(ValueError, match="limit must be at least 1"):
        database.content_ids_near("5", 0)


def test_a_closed_database_cannot_be_used(tmp_path: Path) -> None:
    database = StatsDatabase(tmp_path / "libranet.sqlite3")
    database.close()

    with raises(Exception):
        database.record_push(CONTENT_ID)
