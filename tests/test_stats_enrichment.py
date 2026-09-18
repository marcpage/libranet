"""Tests for improving cached search responses from the database."""

from __future__ import annotations
from json import dumps, loads
from pathlib import Path
from time import time
from typing import Iterator

from pytest import fixture, raises

from libranet.cas.content_id import ContentId
from libranet.stats.database import StatsDatabase
from libranet.stats.enrichment import SearchEnricher
from libranet.webserver.search import SearchCache

PREFIX = "8" + "0" * 63
CACHED_ID = ContentId("sha256", "8" + "f" * 63)
NEARER_ID = ContentId("sha256", "8" + "0" * 62 + "1")
FURTHER_ID = ContentId("sha256", "f" * 64)


class FakeClock:
    """Wall-clock time the test can jump forward.

    It starts from the real clock because cache freshness is measured
    against the cache file's modification time.
    """

    def __init__(self) -> None:
        self.now = time()

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@fixture
def clock() -> FakeClock:
    return FakeClock()


@fixture
def database(tmp_path: Path) -> Iterator[StatsDatabase]:
    with StatsDatabase(tmp_path / "libranet.sqlite3") as database:
        yield database


@fixture
def cache(tmp_path: Path, clock: FakeClock) -> SearchCache:
    return SearchCache(tmp_path / "search", ttl_seconds=300.0, prefix_length=4, clock=clock)


@fixture
def enricher(database: StatsDatabase, cache: SearchCache) -> SearchEnricher:
    return SearchEnricher(database, cache, max_results=4)


def cached_results(cache: SearchCache) -> list[str]:
    body = cache.load(PREFIX)
    assert body is not None
    results: list[str] = loads(body)["results"]
    return results


def save(cache: SearchCache, *content_ids: ContentId) -> None:
    cache.save(
        PREFIX, dumps({"results": [str(content_id) for content_id in content_ids]}).encode("utf-8")
    )


def known(database: StatsDatabase, *content_ids: ContentId) -> None:
    for content_id in content_ids:
        database.record_request(content_id, external=True)


def test_a_better_match_the_node_only_heard_of_is_added(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    save(cache, CACHED_ID)
    known(database, NEARER_ID)

    assert enricher.enrich(PREFIX)
    assert cached_results(cache) == [str(NEARER_ID), str(CACHED_ID)]


def test_enrichment_never_exceeds_the_result_limit(
    database: StatsDatabase, cache: SearchCache
) -> None:
    save(cache, CACHED_ID)
    known(database, *(ContentId("sha256", f"8{index:063x}") for index in range(10)))

    assert SearchEnricher(database, cache, max_results=3).enrich(PREFIX)
    assert len(cached_results(cache)) == 3


def test_a_cached_response_the_database_cannot_improve_is_left_alone(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    save(cache, NEARER_ID, CACHED_ID)
    known(database, NEARER_ID, CACHED_ID)

    assert not enricher.enrich(PREFIX)
    assert cached_results(cache) == [str(NEARER_ID), str(CACHED_ID)]


def test_a_poorer_match_is_offered_while_there_is_room_for_it(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    save(cache, NEARER_ID, CACHED_ID)
    known(database, FURTHER_ID)

    assert enricher.enrich(PREFIX)
    assert cached_results(cache) == [str(NEARER_ID), str(CACHED_ID), str(FURTHER_ID)]


def test_an_expired_cache_file_is_left_for_the_web_server_to_rebuild(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache, clock: FakeClock
) -> None:
    save(cache, CACHED_ID)
    known(database, NEARER_ID)
    clock.advance(301)

    assert not enricher.enrich(PREFIX)
    assert cache.load(PREFIX) is None


def test_a_prefix_never_searched_for_is_not_invented(
    enricher: SearchEnricher, database: StatsDatabase
) -> None:
    known(database, NEARER_ID)

    assert not enricher.enrich(PREFIX)


def test_an_unreadable_cache_file_is_rebuilt_from_what_is_known(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    cache.save(PREFIX, b"not json at all")
    known(database, NEARER_ID)

    assert enricher.enrich(PREFIX)
    assert cached_results(cache) == [str(NEARER_ID)]


def test_unusable_entries_in_a_cache_file_are_dropped(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    cache.save(PREFIX, dumps({"results": ["not an id", 7, str(CACHED_ID)]}).encode("utf-8"))
    known(database, NEARER_ID)

    assert enricher.enrich(PREFIX)
    assert cached_results(cache) == [str(NEARER_ID), str(CACHED_ID)]


def test_a_cache_file_of_the_wrong_shape_is_rebuilt(
    enricher: SearchEnricher, database: StatsDatabase, cache: SearchCache
) -> None:
    for body in (b"[]", b'{"other": 1}', dumps({"results": "nope"}).encode("utf-8")):
        cache.save(PREFIX, body)
        known(database, NEARER_ID)

        assert enricher.enrich(PREFIX)
        assert cached_results(cache) == [str(NEARER_ID)]


def test_an_enricher_needs_room_for_at_least_one_result(
    database: StatsDatabase, cache: SearchCache
) -> None:
    with raises(ValueError, match="max_results must be at least 1"):
        SearchEnricher(database, cache, max_results=0)
