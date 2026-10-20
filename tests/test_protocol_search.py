"""Tests for local prefix search and the search-result cache."""

from __future__ import annotations
from hashlib import sha256
from json import dumps, loads
from logging import DEBUG, WARNING
from os import utime
from pathlib import Path
from typing import Iterator
from zlib import compress, decompress

from pytest import LogCaptureFixture, mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.prefix import nearest
from libranet.cas.store import CasStore
from libranet.config.models import MIB, StorageConfig
from libranet.json_format import compact_json
from libranet.protocol.errors import InvalidListError
from libranet.protocol.search import (
    LocalSearch,
    SearchAnswer,
    SearchCache,
    normalize_prefix,
    parse_search_results,
)
from libranet.stats.database import StatsDatabase

ANSWERED_ID = ContentId.for_data(b"sought", "sha256")

# More hashes starting with `a` than one answer can name, even compressed,
# best first for a search of `a`.
_MANY = nearest(
    "a",
    (
        ContentId.create("sha256", "a" + sha256(str(index).encode()).hexdigest()[1:])
        for index in range(40_000)
    ),
)


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

    results = LocalSearch(store).search("abcf")

    assert [content_id.hash[:4] for content_id in results] == ["abcf", "abc1", "ab00", "0000"]


def test_search_widens_until_it_has_every_match(tmp_path: Path) -> None:
    store = _store(tmp_path, "a100", "a200", "b000")

    results = LocalSearch(store).search("a1ff")

    assert [content_id.hash[:4] for content_id in results] == ["a100", "a200", "b000"]


def test_search_widens_past_the_first_digit_a_bit_at_a_time(tmp_path: Path) -> None:
    # `a` is 1010 and `b` 1011: they differ in the first digit, but share three bits.
    store = _store(tmp_path, "b000", "a123")

    results = LocalSearch(store).search("a")

    assert [content_id.hash[:4] for content_id in results] == ["a123", "b000"]


def test_hashes_sharing_fewer_bits_are_ranked_after_those_sharing_more(tmp_path: Path) -> None:
    # Against `a` (1010): `9` (1001) shares two bits, `e` (1110) one, `2` (0010) none.
    store = _store(tmp_path, "2000", "e000", "9000", "b000", "a123")

    results = LocalSearch(store).search("a")

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


class _Many:
    """A store holding :data:`_MANY` under ``a``, and one hash under every other digit."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    @property
    def prefix_length(self) -> int:
        return 1

    def iter_prefix(self, algorithm: str, hash_prefix: str) -> Iterator[ContentId]:
        self.asked.append(hash_prefix)

        if algorithm != "sha256":
            return iter([])

        if hash_prefix == "a":
            return iter(_MANY)

        return iter([ContentId.create("sha256", _hash(hash_prefix))])


def test_widening_stops_once_the_answer_is_full() -> None:
    source = _Many()

    results = LocalSearch(source).search("a")

    assert 0 < len(results) < len(_MANY)
    assert results == nearest("a", _MANY)[: len(results)]
    # What shares the first digit is more than an answer can name, so no
    # digit sharing fewer bits is scanned.
    assert source.asked == ["a"]


@mark.parametrize("min_bits", [0, 2, 5, 12])
@mark.parametrize("prefix", ["8", "80", "a", "7f", "0", "fff", "c3"])
def test_search_agrees_with_what_stats_finds_among_the_same_hashes(
    tmp_path: Path, prefix: str, min_bits: int
) -> None:
    held = ["0000", "17ab", "7fff", "9000", "9f00", "a1b2", "b000", "e3e3", "fe00"]
    store = _store(tmp_path, *held)

    with StatsDatabase(tmp_path / "stats.sqlite3") as database:
        for hashed in held:
            database.record_acquired(ContentId.create("sha256", _hash(hashed)), 1)

        known = database.content_ids_near(prefix, min_bits)

    # Each finds the hashes sharing the most leading bits (HttpApi §6), and
    # every one sharing enough of them.
    assert LocalSearch(store, min_bits=min_bits).search(prefix) == known


def test_a_search_of_an_empty_store_finds_nothing(tmp_path: Path) -> None:
    assert LocalSearch(_store(tmp_path)).search("a") == []


def test_a_search_returns_only_what_shares_its_minimum_bits(tmp_path: Path) -> None:
    store = _store(tmp_path, "abc1", "abcf", "ab00", "0000")

    results = LocalSearch(store, min_bits=12).search("abcf")

    assert [content_id.hash[:4] for content_id in results] == ["abcf", "abc1"]


def test_a_prefix_shorter_than_the_minimum_bits_is_matched_whole(tmp_path: Path) -> None:
    store = _store(tmp_path, "abc1", "abcf", "ab00", "a000")

    results = LocalSearch(store, min_bits=24).search("ab")

    assert [content_id.hash[:4] for content_id in results] == ["ab00", "abc1", "abcf"]


def test_widening_stops_once_every_match_is_scanned(tmp_path: Path) -> None:
    source = _AskedFor(_store(tmp_path, "a100", "b000"))

    results = LocalSearch(source, min_bits=4).search("a1")

    assert [content_id.hash[:4] for content_id in results] == ["a100"]
    # Whatever shares the first digit is scanned, and nothing sharing less.
    assert source.asked == ["a1", "a"]


def test_widening_a_bit_at_a_time_stops_at_the_minimum_bits(tmp_path: Path) -> None:
    source = _AskedFor(_store(tmp_path, "a100", "b000", "e000"))

    results = LocalSearch(source, min_bits=2).search("a")

    assert [content_id.hash[:4] for content_id in results] == ["a100", "b000"]
    # `b` shares three bits with `a`, and `8` and `9` two; `e` shares one.
    assert source.asked == ["a", "b", "8", "9"]


def test_what_is_found_elsewhere_is_kept_only_if_it_shares_enough_bits(tmp_path: Path) -> None:
    near, far = (ContentId.create("sha256", _hash(prefix)) for prefix in ("abc0", "ab00"))

    assert LocalSearch(_store(tmp_path), min_bits=12).matching("abcf", [far, near]) == [near]


def test_a_search_says_how_many_bits_a_match_shares(tmp_path: Path) -> None:
    assert LocalSearch(_store(tmp_path)).min_bits == 0
    assert LocalSearch(_store(tmp_path), min_bits=24).min_bits == 24


def test_search_rejects_a_negative_minimum(tmp_path: Path) -> None:
    with raises(ValueError, match="min_bits"):
        LocalSearch(_store(tmp_path), min_bits=-1)


def test_a_nodes_search_is_held_to_its_storage_configuration(tmp_path: Path) -> None:
    search = LocalSearch.of(_store(tmp_path), StorageConfig(search_min_bits=12))

    assert search.min_bits == 12


def test_an_answer_that_fits_is_sent_as_it_is() -> None:
    found = [ContentId.create("sha256", _hash(prefix)) for prefix in ("ab", "cd")]

    answer = SearchAnswer.of(found, target="ab")

    assert answer.results == found
    assert loads(answer.body) == {"target": "ab", "results": [str(found[0]), str(found[1])]}
    assert (answer.compressed, answer.headers) == (False, {})


def test_an_answer_too_large_as_it_is_is_compressed() -> None:
    found = _MANY[:20_000]

    answer = SearchAnswer.of(found)

    assert len(SearchCache.body(found)) >= MIB
    assert len(answer.body) < MIB
    assert answer.results == found
    assert loads(decompress(answer.body)) == {"results": [str(found_id) for found_id in found]}
    assert (answer.compressed, answer.headers) == (True, {"Content-Encoding": "deflate"})


def test_an_answer_names_as_many_of_the_best_as_fit_once_compressed() -> None:
    answer = SearchAnswer.of(_MANY, target="a")

    assert len(answer.body) < MIB
    assert 0 < len(answer.results) < len(_MANY)
    assert answer.results == _MANY[: len(answer.results)]
    assert loads(decompress(answer.body)) == {
        "target": "a",
        "results": [str(found_id) for found_id in answer.results],
    }
    one_more = [str(found_id) for found_id in _MANY[: len(answer.results) + 1]]
    assert len(compress(compact_json({"target": "a", "results": one_more}), 6)) >= MIB


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
