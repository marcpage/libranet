"""Tests for ranking content identifiers against a hash prefix."""

from __future__ import annotations

from pytest import mark

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits, nearest


def _id(prefix: str, algorithm: str = "sha256") -> ContentId:
    return ContentId(algorithm, prefix + "0" * (64 - len(prefix)))


@mark.parametrize(
    ("left", "right", "bits"),
    [
        ("ab", "ab", 8),
        ("ab", "abcd", 8),
        ("a0", "b0", 3),  # 1010 vs 1011
        ("0", "8", 0),  # 0000 vs 1000
        ("0", "1", 3),
        ("", "ff", 0),
    ],
)
def test_matching_bits(left: str, right: str, bits: int) -> None:
    assert matching_bits(left, right) == bits


def test_nearest_puts_the_best_match_first() -> None:
    candidates = [_id("0000"), _id("abcf"), _id("ab00"), _id("abc1")]

    ranked = nearest("abcf", candidates)

    assert [content_id.hash[:4] for content_id in ranked] == ["abcf", "abc1", "ab00", "0000"]


def test_nearest_ranks_every_candidate() -> None:
    candidates = [_id(f"{index:04x}") for index in range(20)]

    assert sorted(nearest("0005", candidates)) == sorted(candidates)


def test_nearest_of_nothing_is_nothing() -> None:
    assert nearest("abcd", []) == []


def test_equal_matches_break_ties_on_the_identifier() -> None:
    # Same hash under two algorithms matches a prefix equally well.
    candidates = [_id("abcd", "sha256"), _id("abcd", "sha512")]

    assert nearest("abcd", reversed(candidates)) == sorted(candidates, key=str)
