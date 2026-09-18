"""Tests for ranking content identifiers against a hash prefix."""

from __future__ import annotations

from pytest import mark, raises

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

    ranked = nearest("abcf", candidates, 4)

    assert [content_id.hash[:4] for content_id in ranked] == ["abcf", "abc1", "ab00", "0000"]


def test_nearest_returns_no_more_than_the_limit() -> None:
    candidates = [_id(f"{index:04x}") for index in range(20)]

    assert len(nearest("0005", candidates, 3)) == 3


def test_nearest_of_nothing_is_nothing() -> None:
    assert nearest("abcd", [], 4) == []


def test_equal_matches_break_ties_on_the_identifier() -> None:
    # Same hash under two algorithms matches a prefix equally well.
    candidates = [_id("abcd", "sha256"), _id("abcd", "sha512")]

    assert nearest("abcd", reversed(candidates), 2) == sorted(candidates, key=str)


def test_nearest_needs_room_for_at_least_one_result() -> None:
    with raises(ValueError, match="limit must be at least 1"):
        nearest("abcd", [_id("abcd")], 0)
