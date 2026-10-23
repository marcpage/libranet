"""Tests for how the servers this node is a client of served it, as ``karma.ratings`` says."""

from __future__ import annotations
from typing import Any

from pytest import mark, raises

from libranet.cas.content_id import ContentId
from libranet.messaging.server_ratings import ServerRating, ServerRatings

# In the order their ids sort.
FIRST = ContentId("sha256", "1a" * 32)
SECOND = ContentId("sha256", "2b" * 32)
THIRD = ContentId("sha256", "3c" * 32)


def rated(*servers: tuple[ContentId, int, int]) -> ServerRatings:
    """Each of ``servers``, an id, the bytes received from it, and the requests it fulfilled."""
    return ServerRatings(tuple(ServerRating(*server) for server in servers))


def test_the_ratings_are_read_as_the_payload_says_them() -> None:
    ratings = rated((FIRST, 4_096, 3), (SECOND, 0, 0))

    assert ratings.payload() == {
        "servers": [
            {"node_id": str(FIRST), "bytes_received": 4_096, "requests_fulfilled": 3},
            {"node_id": str(SECOND), "bytes_received": 0, "requests_fulfilled": 0},
        ]
    }
    assert ServerRatings.from_payload(ratings.payload()) == ratings


def test_the_server_rated_highest_by_each_measure_is_named() -> None:
    ratings = rated((FIRST, 10, 1), (SECOND, 500, 2), (THIRD, 20, 9))

    assert ratings.best() == [SECOND, THIRD]


def test_a_server_rated_highest_by_both_is_named_once() -> None:
    assert rated((FIRST, 10, 1), (SECOND, 500, 9)).best() == [SECOND]


def test_a_tie_goes_to_the_id_that_sorts_first() -> None:
    assert rated((THIRD, 500, 4), (SECOND, 500, 9), (FIRST, 10, 9)).best() == [SECOND, FIRST]


def test_no_server_is_named_by_a_measure_none_served_by() -> None:
    assert rated((FIRST, 0, 2)).best() == [FIRST]
    assert rated((FIRST, 0, 0), (SECOND, 0, 0)).best() == []
    assert ServerRatings().best() == []


@mark.parametrize(
    ("payload", "error"),
    [
        ({}, '"servers" must be a list'),
        ({"servers": ["sha256/1a"]}, "must be an object"),
        ({"servers": [{"bytes_received": 1, "requests_fulfilled": 1}]}, '"node_id"'),
        (
            {"servers": [{"node_id": "none", "bytes_received": 1, "requests_fulfilled": 1}]},
            "none",
        ),
        (
            {"servers": [{"node_id": str(FIRST), "bytes_received": True, "requests_fulfilled": 1}]},
            '"bytes_received" must be an integer',
        ),
        (
            {"servers": [{"node_id": str(FIRST), "bytes_received": 1}]},
            '"requests_fulfilled" must be an integer',
        ),
        (
            {"servers": [{"node_id": str(FIRST), "bytes_received": 1, "requests_fulfilled": -1}]},
            "must be 0 or more",
        ),
    ],
)
def test_a_payload_that_is_not_one_is_refused(payload: dict[str, Any], error: str) -> None:
    with raises(ValueError, match=error):
        ServerRatings.from_payload(payload)
