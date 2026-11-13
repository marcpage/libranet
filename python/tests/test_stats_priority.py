"""Tests for the eviction score."""

from __future__ import annotations

from pytest import approx, mark

from libranet.cas.content_id import ContentId
from libranet.cas.prefix import matching_bits
from libranet.config.models import MIB
from libranet.stats.priority import FACTOR_FLOOR, SIZE_FACTOR_FLOOR, EvictionScorer

HASH_BITS = 256
NODE_ID = ContentId.for_data(b"this node's public key", "sha256")
NOW = 1_000_000.0
LONGEST_UNUSED = 100.0
MOST_REQUESTS = 10
MOST_BITS = 16

# Scored against a node whose held content was unused 100 s at most,
# requested 10 times at most, and matches the node id by 16 bits at most.
SCORER = EvictionScorer(NODE_ID.hash, NOW, LONGEST_UNUSED, MOST_REQUESTS, MOST_BITS)


def sharing(bits: int, variant: int = 0, node_id: ContentId = NODE_ID) -> ContentId:
    """A content id whose hash shares exactly ``bits`` leading bits with ``node_id``'s.

    ``variant`` changes only its last bits, giving distinct ids alike in priority.
    """
    value = int(node_id.hash, 16) ^ (1 << (HASH_BITS - 1 - bits)) ^ variant
    return ContentId("sha256", f"{value:064x}")


def floored(factor: float, floor: float = FACTOR_FLOOR) -> float:
    """What ``factor`` counts for in a score."""
    return floor + (1 - floor) * factor


def test_sharing_builds_ids_of_the_priority_asked_for() -> None:
    assert [matching_bits(NODE_ID.hash, sharing(bits).hash) for bits in (0, 3, 17, 255)] == [
        0,
        3,
        17,
        255,
    ]


# -- The score ---------------------------------------------------------------


def test_one_byte_unused_longest_never_requested_matching_nothing_scores_about_one() -> None:
    score = SCORER.score(1, 0, NOW - LONGEST_UNUSED, sharing(0).hash)

    assert score == approx(1.0, abs=1e-5)
    assert score < 1.0


@mark.parametrize(
    "size, requests, unused, bits, floor",
    [
        (1, 0, 0.0, 0, FACTOR_FLOOR),  # used just now
        (1, MOST_REQUESTS, LONGEST_UNUSED, 0, FACTOR_FLOOR),  # requested the most
        (MIB, 0, LONGEST_UNUSED, 0, SIZE_FACTOR_FLOOR),  # as large as an object may be
        (1, 0, LONGEST_UNUSED, MOST_BITS, FACTOR_FLOOR),  # the best match held
    ],
)
def test_one_factor_at_its_worst_counts_for_its_floor_not_zero(
    size: int, requests: int, unused: float, bits: int, floor: float
) -> None:
    score = SCORER.score(size, requests, NOW - unused, sharing(bits).hash)

    assert score == approx(floor, rel=1e-5)


def test_every_factor_at_its_worst_is_the_product_of_the_floors() -> None:
    score = SCORER.score(MIB, MOST_REQUESTS, NOW, sharing(MOST_BITS).hash)

    assert score == approx(FACTOR_FLOOR**3 * SIZE_FACTOR_FLOOR)


@mark.parametrize(
    "size, requests, unused, bits, factor, floor",
    [
        (1, 0, LONGEST_UNUSED / 4, 0, 0.25, FACTOR_FLOOR),
        (1, MOST_REQUESTS // 2, LONGEST_UNUSED, 0, 0.5, FACTOR_FLOOR),
        (MIB // 4, 0, LONGEST_UNUSED, 0, 0.75, SIZE_FACTOR_FLOOR),
        (1, 0, LONGEST_UNUSED, MOST_BITS // 4, 0.75, FACTOR_FLOOR),
    ],
)
def test_each_factor_is_a_fraction_of_the_extreme_held(
    size: int, requests: int, unused: float, bits: int, factor: float, floor: float
) -> None:
    score = SCORER.score(size, requests, NOW - unused, sharing(bits).hash)

    assert score == approx(floored(factor, floor), rel=1e-5)


def test_an_extreme_of_zero_leaves_its_factor_at_one() -> None:
    # Nothing held was ever requested, or unused for any time, or matches a bit.
    scorer = EvictionScorer(NODE_ID.hash, NOW, 0.0, 0, 0)

    assert scorer.score(1, 0, NOW, sharing(0).hash) == approx(1.0, abs=1e-5)


def test_when_it_was_last_used_unknown_counts_as_unused_longest() -> None:
    assert SCORER.score(1, 0, None, sharing(0).hash) == approx(1.0, abs=1e-5)


@mark.parametrize(
    "size, requests, last_used, bits, floor",
    [
        (1, 0, NOW + 60, 0, FACTOR_FLOOR),  # used after now, as the clock sees it
        (1, MOST_REQUESTS + 5, NOW - LONGEST_UNUSED, 0, FACTOR_FLOOR),
        (2 * MIB, 0, NOW - LONGEST_UNUSED, 0, SIZE_FACTOR_FLOOR),
        (1, 0, NOW - LONGEST_UNUSED, 255, FACTOR_FLOOR),  # this node's own key matches every bit
    ],
)
def test_a_factor_beyond_its_extreme_is_kept_within_its_range(
    size: int, requests: int, last_used: float, bits: int, floor: float
) -> None:
    assert SCORER.score(size, requests, last_used, sharing(bits).hash) == approx(floor, rel=1e-5)


def test_an_unused_object_of_one_mib_goes_before_a_busy_one() -> None:
    unused = SCORER.score(MIB, 0, NOW - LONGEST_UNUSED, sharing(2).hash)
    busy = SCORER.score(MIB, 9, NOW - 1, sharing(0).hash)

    assert unused > busy > 0


def test_a_small_object_in_use_goes_after_a_large_one_unused() -> None:
    in_use = SCORER.score(1_000, 3, NOW - 1, sharing(0).hash)
    large_unused = SCORER.score(MIB, 0, NOW - LONGEST_UNUSED, sharing(0).hash)

    assert in_use < large_unused


def test_a_small_object_no_longer_in_use_goes_before_a_large_one() -> None:
    small_unused = SCORER.score(1_000, 3, NOW - LONGEST_UNUSED, sharing(0).hash)
    large_unused = SCORER.score(MIB, 3, NOW - LONGEST_UNUSED, sharing(0).hash)

    assert small_unused > large_unused


def test_matching_more_bits_keeps_content_longer() -> None:
    scores = [SCORER.score(1_000, 3, NOW - 50, sharing(bits).hash) for bits in (0, 4, 8, 12)]

    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == len(scores)
