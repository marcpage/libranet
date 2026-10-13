"""Tests for local prefix search and the search-result cache."""

from __future__ import annotations
from json import dumps
from logging import DEBUG, WARNING
from os import utime
from pathlib import Path
from typing import Iterator

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import matching_bits
from libranet.cas.store import CasStore
from libranet.config.models import StorageConfig
from libranet.protocol.errors import InvalidListError
from libranet.protocol.search import (
    LocalSearch,
    SearchCache,
    normalize_prefix,
    parse_search_results,
)
from libranet.stats.database import StatsDatabase

ANSWERED_ID = ContentId.for_data(b"sought", "sha256")


def _hash(prefix: str) -> str:
    return prefix + "0" * (64 - len(prefix))


def _store(tmp_path: Path, *prefixes: str) -> CasStore:
    store = CasStore(tmp_path / "cas", prefix_length=2)

    for prefix in prefixes:
        store.write(ContentId.create("sha256", _hash(prefix)), b"x")

    return store


def test_normalize_prefix_lower_cases() -> None:
    assert normalize_prefix("AbC") == "abc"


@mark.parametrize("text", ["", "xyz", "a" * 65, "ab-c"])
def test_normalize_prefix_rejects_invalid_prefixes(text: str) -> None:
    with raises(InvalidContentIdError):
        normalize_prefix(text)


def test_search_ranks_by_matching_bits(tmp_path: Path) -> None:
    store = _store(tmp_path, "abc1", "abcf", "ab00", "0000")

    results = LocalSearch(store, max_results=3).search("abcf")

    assert [content_id.hash[:4] for content_id in results] == ["abcf", "abc1", "ab00"]


def test_search_widens_until_enough_candidates(tmp_path: Path) -> None:
    store = _store(tmp_path, "a100", "a200", "b000")

    results = LocalSearch(store, max_results=2).search("a1ff")

    assert [content_id.hash[:4] for content_id in results] == ["a100", "a200"]


def test_search_widens_past_the_first_digit_a_bit_at_a_time(tmp_path: Path) -> None:
    # `a` is 1010 and `b` 1011: they differ in the first digit, but share three bits.
    store = _store(tmp_path, "b000", "a123")

    results = LocalSearch(store, max_results=5).search("a")

    assert [content_id.hash[:4] for content_id in results] == ["a123", "b000"]


def test_hashes_sharing_fewer_bits_are_ranked_after_those_sharing_more(tmp_path: Path) -> None:
    # Against `a` (1010): `9` (1001) shares two bits, `e` (1110) one, `2` (0010) none.
    store = _store(tmp_path, "2000", "e000", "9000", "b000", "a123")

    results = LocalSearch(store, max_results=5).search("a")

    assert [content_id.hash[:4] for content_id in results] == [
        "a123",
        "b000",
        "9000",
        "e000",
        "2000",
    ]


class _AskedFor:
    """A store that notes each prefix it is asked to scan."""

    def __init__(self, store: CasStore) -> None:
        self.store = store
        self.asked: list[str] = []

    @property
    def prefix_length(self) -> int:
        return self.store.prefix_length

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        self.asked.append(hash_prefix)
        return self.store.iter_prefix(algorithm, hash_prefix)


def test_widening_stops_once_there_are_enough_candidates(tmp_path: Path) -> None:
    source = _AskedFor(_store(tmp_path, "a100", "b000", "e000", "2000"))

    results = LocalSearch(source, max_results=2).search("a")

    assert [content_id.hash[:4] for content_id in results] == ["a100", "b000"]
    # `b` shares three bits with `a`, so no digit sharing fewer is scanned.
    assert source.asked == ["a", "b"]


@mark.parametrize("prefix", ["8", "80", "a", "7f", "0", "fff", "c3"])
def test_search_agrees_with_what_stats_finds_among_the_same_hashes(
    tmp_path: Path, prefix: str
) -> None:
    held = ["0000", "17ab", "7fff", "9000", "9f00", "a1b2", "b000", "e3e3", "fe00"]
    store = _store(tmp_path, *held)

    with StatsDatabase(tmp_path / "stats.sqlite3") as database:
        for hashed in held:
            database.record_acquired(ContentId.create("sha256", _hash(hashed)), 1)

        known = database.content_ids_near(prefix, 3)

    found = LocalSearch(store, max_results=3).search(prefix)

    # Each finds the hashes sharing the most leading bits (HttpApi §6). Where
    # several share as many at the limit, each may name a different one.
    assert [matching_bits(prefix, content_id.hash) for content_id in found] == [
        matching_bits(prefix, content_id.hash) for content_id in known
    ]


def test_a_search_of_an_empty_store_finds_nothing(tmp_path: Path) -> None:
    assert LocalSearch(_store(tmp_path), max_results=5).search("a") == []


def test_search_is_limited_to_max_results(tmp_path: Path) -> None:
    store = _store(tmp_path, "aa01", "aa02", "aa03")

    assert len(LocalSearch(store, max_results=2).search("aa")) == 2


def test_a_search_says_how_many_matches_it_gives(tmp_path: Path) -> None:
    assert LocalSearch(_store(tmp_path), max_results=7).max_results == 7


def test_search_rejects_a_zero_limit(tmp_path: Path) -> None:
    with raises(ValueError):
        LocalSearch(_store(tmp_path), max_results=0)


def test_cache_misses_when_nothing_is_saved(tmp_path: Path) -> None:
    assert SearchCache(tmp_path, ttl_seconds=10, prefix_length=2).load("abcd") is None


def test_cache_returns_saved_body_until_it_expires(tmp_path: Path) -> None:
    now = [1_000_000.0]
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2, clock=lambda: now[0])

    path = cache.save("abcd", b"{}")
    utime(path, (now[0], now[0]))

    assert path == tmp_path / "ab" / "abcd.json"
    assert cache.load("abcd") == b"{}"

    now[0] += 10

    assert cache.load("abcd") is None


def test_cache_save_replaces_existing_body(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)

    cache.save("ab", b"old")
    cache.save("ab", b"new")

    assert cache.load("ab") == b"new"
    assert [entry.name for entry in (tmp_path / "ab").iterdir()] == ["ab.json"]


def test_cache_rejects_invalid_settings(tmp_path: Path) -> None:
    with raises(ValueError):
        SearchCache(tmp_path, ttl_seconds=0, prefix_length=2)

    with raises(ValueError):
        SearchCache(tmp_path, ttl_seconds=1, prefix_length=0)


def test_results_are_cached_as_the_response_body_that_lists_them(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)
    results = [ContentId.create("sha256", _hash("abc1")), ContentId.create("sha256", _hash("ab00"))]

    body = cache.save_results("ab", results)

    assert body == b'{"results":["sha256/%s","sha256/%s"]}' % (
        _hash("abc1").encode(),
        _hash("ab00").encode(),
    )
    assert cache.load("ab") == body


def test_cached_results_are_read_back_best_first(tmp_path: Path) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)
    results = [ContentId.create("sha256", _hash("abc1")), ContentId.create("sha256", _hash("ab00"))]
    cache.save_results("ab", results)

    assert cache.load_results("ab") == results
    assert cache.load_results("cd") is None


@mark.parametrize("body", [b"not json", b"[]", b'{"found": []}', b'{"results": "sha256/00"}'])
def test_cached_results_that_cannot_be_read_list_nothing(
    tmp_path: Path, caplog: LogCaptureFixture, body: bytes
) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)
    cache.save("ab", body)

    with caplog.at_level(WARNING):
        assert cache.load_results("ab") == []

    assert "cached search for ab" in caplog.text


def test_a_cached_result_that_is_no_content_id_is_left_out(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)
    kept = ContentId.create("sha256", _hash("ab00"))
    cache.save("ab", b'{"results": [7, "sha256/zz", "%s"]}' % str(kept).encode())

    with caplog.at_level(WARNING):
        assert cache.load_results("ab") == [kept]

    assert "Dropping 7 from the cached search for ab: not a string" in caplog.text
    assert "Dropping 'sha256/zz' from the cached search for ab: " in caplog.text


def test_cached_results_under_an_unknown_algorithm_are_warned_of_once(
    tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    cache = SearchCache(tmp_path, ttl_seconds=10, prefix_length=2)
    kept = ContentId.create("sha256", _hash("ab00"))
    unknown = ["md5/" + "0" * 32, "md5/" + "1" * 32]
    cache.save("ab", dumps({"results": [*unknown, str(kept)]}).encode("utf-8"))

    with caplog.at_level(WARNING):
        assert cache.load_results("ab") == [kept]

    (warning,) = caplog.records
    assert warning.getMessage().startswith(
        "The cached search for ab names ids hashed with algorithms "
    )
    assert warning.getMessage().endswith(": 2 under md5")


def test_a_search_answer_names_its_content_ids_in_order() -> None:
    other = ContentId.for_data(b"other", "sha256")
    value = {"results": [f"SHA256/{ANSWERED_ID.hash.upper()}", str(other)]}

    assert parse_search_results(value) == [ANSWERED_ID, other]


def test_unusable_search_answer_entries_are_dropped_and_unknown_algorithms_warned_of(
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(DEBUG)
    value = {"results": ["md5/" + "0" * 32, "sha256/short", 7, str(ANSWERED_ID)]}

    assert parse_search_results(value) == [ANSWERED_ID]
    assert [record.getMessage().split(":")[0] for record in caplog.records[:2]] == [
        "Dropping 'sha256/short' from a search answer",
        "Dropping 7 from a search answer",
    ]
    warning = caplog.records[2]
    assert warning.levelno == WARNING
    assert warning.getMessage().startswith("A search answer names ids hashed with algorithms ")
    assert warning.getMessage().endswith(": 1 under md5")


@mark.parametrize("value", [None, [], "results", {}, {"results": "x"}, {"results": {}}])
def test_misshapen_search_answers_are_refused(value: object) -> None:
    with raises(InvalidListError):
        parse_search_results(value)


def test_a_node_caches_searches_where_and_for_as_long_as_it_is_configured_to(
    tmp_path: Path,
) -> None:
    storage = StorageConfig(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        search_cache_ttl_seconds=10,
        hash_prefix_length=3,
    )
    now = [1_000.0]
    cache = SearchCache.of(storage, clock=lambda: now[0])

    path = cache.save("abcd", b"{}")
    utime(path, (now[0], now[0]))

    assert path == storage.search_cache_dir / "abc" / "abcd.json"
    assert cache.load("abcd") == b"{}"

    now[0] += 10

    assert cache.load("abcd") is None
