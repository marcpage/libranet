"""Tests for local prefix search and the search-result cache."""

from __future__ import annotations
from os import utime
from pathlib import Path

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.cas.errors import InvalidContentIdError
from libranet.cas.store import CasStore
from libranet.webserver.search import LocalSearch, SearchCache, normalize_prefix


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


def test_search_does_not_return_hashes_sharing_no_leading_digit(tmp_path: Path) -> None:
    store = _store(tmp_path, "b000")

    assert LocalSearch(store, max_results=5).search("a") == []


def test_search_is_limited_to_max_results(tmp_path: Path) -> None:
    store = _store(tmp_path, "aa01", "aa02", "aa03")

    assert len(LocalSearch(store, max_results=2).search("aa")) == 2


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
