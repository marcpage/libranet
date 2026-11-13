"""Tests for the counts the stats module rates the servers this node is a client of by."""

from __future__ import annotations

from libranet.cas.content_id import ContentId
from libranet.messaging.server_ratings import ServerRating
from libranet.stats.ratings import RatingCounts
from libranet.stats.records import NodeStats

# In the order their ids sort.
FIRST = ContentId("sha256", "1a" * 32)
SECOND = ContentId("sha256", "2b" * 32)


def counted(node_id: ContentId, bytes_received: int, data_found: int) -> NodeStats:
    """What ``node_stats`` counts of ``node_id``, with every other count 0."""
    return NodeStats(node_id, bytes_received=bytes_received, data_found=data_found)


def test_a_server_is_rated_by_what_was_counted_since_the_counts_began() -> None:
    counts = RatingCounts([counted(FIRST, 100, 4)])
    counts.connected([FIRST])

    ratings = counts.answer([counted(FIRST, 150, 5)], [FIRST])

    assert ratings.servers == (ServerRating(FIRST, 50, 1),)


def test_a_server_is_rated_by_what_was_counted_since_the_last_answer() -> None:
    counts = RatingCounts([])
    counts.connected([FIRST])
    counts.answer([counted(FIRST, 100, 4)], [FIRST])

    ratings = counts.answer([counted(FIRST, 130, 4)], [FIRST])

    assert ratings.servers == (ServerRating(FIRST, 30, 0),)


def test_only_the_servers_connected_to_since_the_last_answer_are_rated_in_the_order_ids_sort() -> (
    None
):
    counts = RatingCounts([])
    counts.connected([SECOND])
    counts.connected([FIRST])

    # SECOND is connected to no longer, but was since the last answer, and
    # FIRST stays connected.
    first = counts.answer([counted(SECOND, 7, 1)], [FIRST])
    second = counts.answer([counted(SECOND, 9, 1)], [])

    assert first.servers == (ServerRating(FIRST, 0, 0), ServerRating(SECOND, 7, 1))
    assert second.servers == (ServerRating(FIRST, 0, 0),)
    assert counts.answer([], []).servers == ()
