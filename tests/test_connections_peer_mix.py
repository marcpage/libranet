"""Tests for choosing which peers to connect to, without any sockets."""

from __future__ import annotations

from pytest import mark

from libranet.cas.content_id import ContentId
from libranet.connections.candidates import Candidate
from libranet.connections.peer_mix import bucket_of, choose_candidates


def node(leading: str, tail: str = "0") -> ContentId:
    """A node id whose hash starts with ``leading``, padded with ``tail``."""
    return ContentId.create("sha256", leading + tail * (64 - len(leading)))


def candidate(leading: str, tail: str = "0") -> Candidate:
    node_id = node(leading, tail)
    return Candidate(f"http://{leading}{tail}.test", node_id)


def endpoints(chosen: list[Candidate]) -> list[str]:
    return [chosen_one.endpoint for chosen_one in chosen]


@mark.parametrize(
    ("leading", "bits", "bucket"),
    [
        ("0", 4, 0),
        ("a", 4, 10),
        ("f", 4, 15),
        ("8", 1, 1),
        ("7", 1, 0),
        ("a8", 5, 0b10101),
        ("a7", 5, 0b10100),
        ("c3", 8, 0xC3),
        ("1234", 16, 0x1234),
    ],
)
def test_a_bucket_is_the_value_of_the_leading_bits(leading: str, bits: int, bucket: int) -> None:
    assert bucket_of(node(leading), bits) == bucket


def test_the_best_candidate_in_each_bucket_is_chosen() -> None:
    candidates = [candidate("1", "a"), candidate("2"), candidate("1", "b"), candidate("3")]

    chosen = choose_candidates(candidates, [], min_connections=3, bucket_bits=4)

    assert chosen == [candidates[0], candidates[1], candidates[3]]


def test_buckets_already_covered_are_not_filled_again() -> None:
    candidates = [candidate("1", "a"), candidate("2"), candidate("3")]

    chosen = choose_candidates(candidates, [node("1", "f")], min_connections=2, bucket_bits=4)

    assert chosen == [candidates[1]]


def test_buckets_stop_being_filled_once_enough_are_covered() -> None:
    candidates = [candidate(digit) for digit in "0123456789"]

    chosen = choose_candidates(candidates, [node("f")], min_connections=4, bucket_bits=4)

    assert endpoints(chosen) == endpoints(candidates[:3])


def test_too_few_buckets_are_made_up_with_peers_in_covered_ones() -> None:
    same_bucket = [candidate("5", tail) for tail in "abc"]

    chosen = choose_candidates(same_bucket, [], min_connections=3, bucket_bits=4)

    assert chosen == same_bucket


def test_a_candidate_without_a_known_id_only_makes_up_the_number() -> None:
    unknown = Candidate("http://seed.test", None)
    known = candidate("4")

    assert choose_candidates([unknown, known], [], min_connections=1, bucket_bits=4) == [known]
    assert choose_candidates([unknown, known], [], min_connections=2, bucket_bits=4) == [
        known,
        unknown,
    ]


def test_connections_of_unknown_id_count_towards_the_number_but_cover_no_bucket() -> None:
    same_bucket = [candidate("5", tail) for tail in "abc"]
    spread = [candidate("1"), candidate("2")]

    assert choose_candidates(same_bucket, [None], min_connections=3, bucket_bits=4) == [
        same_bucket[0],
        same_bucket[1],
    ]
    assert choose_candidates(spread, [None, None], min_connections=3, bucket_bits=4) == spread


def test_a_node_is_never_chosen_twice_or_while_connected() -> None:
    twin = Candidate("http://twin.test", node("6"))
    candidates = [candidate("6"), twin, candidate("7", "1"), candidate("7", "1")]

    chosen = choose_candidates(candidates, [node("8")], min_connections=16, bucket_bits=4)

    assert chosen == [candidates[0], candidates[2]]
    assert choose_candidates([twin], [node("6")], min_connections=16, bucket_bits=4) == []


def test_a_full_mix_chooses_nothing() -> None:
    connected: list[ContentId | None] = [node(digit) for digit in "0123456789abcdef"]

    assert choose_candidates([candidate("0", "1")], connected, 16, 4) == []
