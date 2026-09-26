"""Tests for choosing which peers to connect to, without any sockets."""

from __future__ import annotations

from pytest import mark

from libranet.cas.content_id import ContentId
from libranet.config.models import PeerConfig
from libranet.connections.candidates import Candidate
from libranet.connections.peer_mix import PeerMix, bucket_of

HEX_DIGITS = "0123456789abcdef"


def node(leading: str, tail: str = "0") -> ContentId:
    """A node id whose hash starts with ``leading``, padded with ``tail``."""
    return ContentId.create("sha256", leading + tail * (64 - len(leading)))


def candidate(leading: str, tail: str = "0") -> Candidate:
    node_id = node(leading, tail)
    return Candidate((f"http://{leading}{tail}.test",), node_id)


def endpoints(chosen: list[Candidate]) -> list[str]:
    return [chosen_one.endpoints[0] for chosen_one in chosen]


# This node: its own bucket is ``a``, so its neighbors' ids start with ``a``.
OWN = node("a", "5")


def mix(
    min_connections: int,
    neighborhood: int = 0,
    bucket_bits: int = 4,
    neighborhood_bits: int = 4,
) -> PeerMix:
    """This node's peer mix; without a second set unless ``neighborhood`` asks for one."""
    return PeerMix(
        PeerConfig(
            min_outgoing_connections=min_connections,
            bucket_prefix_bits=bucket_bits,
            min_neighborhood_connections=neighborhood,
            neighborhood_prefix_bits=neighborhood_bits,
        ),
        OWN,
    )


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


# -- The first set: one peer per bucket ----------------------------------


def test_the_best_candidate_in_each_bucket_is_chosen() -> None:
    candidates = [candidate("1", "a"), candidate("2"), candidate("1", "b"), candidate("3")]

    chosen = mix(3).choose(candidates, [])

    assert chosen == [candidates[0], candidates[1], candidates[3]]


def test_buckets_already_covered_are_not_filled_again() -> None:
    candidates = [candidate("1", "a"), candidate("2"), candidate("3")]

    chosen = mix(2).choose(candidates, [node("1", "f")])

    assert chosen == [candidates[1]]


def test_buckets_stop_being_filled_once_enough_are_covered() -> None:
    candidates = [candidate(digit) for digit in "0123456789"]

    chosen = mix(4).choose(candidates, [node("f")])

    assert endpoints(chosen) == endpoints(candidates[:3])


def test_too_few_buckets_are_made_up_with_peers_in_covered_ones() -> None:
    same_bucket = [candidate("5", tail) for tail in "abc"]

    chosen = mix(3).choose(same_bucket, [])

    assert chosen == same_bucket


def test_a_candidate_without_a_known_id_only_makes_up_the_number() -> None:
    unknown = Candidate(("http://seed.test",), None)
    known = candidate("4")

    assert mix(1).choose([unknown, known], []) == [known]
    assert mix(2).choose([unknown, known], []) == [known, unknown]


def test_connections_of_unknown_id_count_towards_the_number_but_cover_no_bucket() -> None:
    same_bucket = [candidate("5", tail) for tail in "abc"]
    spread = [candidate("1"), candidate("2")]

    assert mix(3).choose(same_bucket, [None]) == [same_bucket[0], same_bucket[1]]
    assert mix(3).choose(spread, [None, None]) == spread


def test_a_node_is_never_chosen_twice_or_while_connected() -> None:
    twin = Candidate(("http://twin.test",), node("6"))
    candidates = [candidate("6"), twin, candidate("7", "1"), candidate("7", "1")]

    chosen = mix(16).choose(candidates, [node("8")])

    assert chosen == [candidates[0], candidates[2]]
    assert mix(16).choose([twin], [node("6")]) == []


def test_a_full_mix_chooses_nothing() -> None:
    connected: list[ContentId | None] = [node(digit) for digit in HEX_DIGITS]

    assert mix(16).choose([candidate("0", "1")], connected) == []


def test_without_a_second_set_a_neighbor_fills_a_bucket_like_any_other_peer() -> None:
    candidates = [candidate("a1"), candidate("a2"), candidate("3")]

    assert mix(2).choose(candidates, []) == [candidates[0], candidates[2]]


# -- The second set: one neighbor per neighborhood bucket ----------------


def test_by_default_a_peer_per_first_digit_is_followed_by_a_neighbor_per_second_digit() -> None:
    spread = [candidate(digit) for digit in HEX_DIGITS]
    neighbors = [candidate("a" + digit, "1") for digit in HEX_DIGITS]
    spare = [candidate("a0", "2"), candidate("b", "1")]

    chosen = PeerMix(PeerConfig(), OWN).choose([*spread, *neighbors, *spare], [])

    # spread[10] is a neighbor too, with second digit 0, but it fills this
    # node's own bucket in the first set, so neighbors[0] fills 0 in the second.
    assert chosen == [*spread, *neighbors]


def test_no_peer_fills_a_place_in_both_sets() -> None:
    candidates = [candidate("a3"), candidate("a3", "1"), candidate("1")]

    chosen = mix(1, neighborhood=1).choose(candidates, [])

    assert chosen == [candidates[0], candidates[1]]


def test_too_few_neighbors_are_made_up_with_peers_anywhere() -> None:
    unknown = Candidate(("http://seed.test",), None)
    candidates = [unknown, candidate("1"), candidate("a1"), candidate("2")]

    chosen = mix(1, neighborhood=2).choose(candidates, [])

    assert chosen == [candidates[1], candidates[2], unknown]


def test_a_lone_connected_neighbor_counts_in_the_second_set() -> None:
    candidates = [candidate("a3", "1"), candidate("5"), candidate("6")]

    chosen = mix(2, neighborhood=1).choose(candidates, [node("a3")])

    assert chosen == [candidates[0], candidates[1]]


def test_two_connected_neighbors_in_one_neighborhood_bucket_cover_this_nodes_own_bucket() -> None:
    candidates = [candidate("a6"), candidate("5")]

    chosen = mix(2, neighborhood=2).choose(candidates, [node("a3"), node("a3", "1")])

    assert chosen == [candidates[1], candidates[0]]


def test_a_neighbor_past_the_second_sets_number_covers_this_nodes_own_bucket() -> None:
    candidates = [candidate("a6"), candidate("5")]

    chosen = mix(2, neighborhood=1).choose(candidates, [node("a3"), node("a4")])

    assert chosen == [candidates[1]]


def test_neighborhood_buckets_are_the_bits_after_the_first_sets() -> None:
    # Five bits make this node's bucket a0 to a7; the next two split it in
    # four, so a2 and a3 share a neighborhood bucket.
    candidates = [candidate("a8"), candidate("a2"), candidate("a3"), candidate("a4")]

    chosen = mix(1, neighborhood=2, bucket_bits=5, neighborhood_bits=2).choose(
        candidates, [node("0")]
    )

    assert chosen == [candidates[1], candidates[3]]


def test_a_full_mix_of_both_sets_chooses_nothing() -> None:
    connected: list[ContentId | None] = [
        *(node(digit) for digit in HEX_DIGITS),
        *(node("a" + digit, "1") for digit in HEX_DIGITS),
    ]

    chosen = PeerMix(PeerConfig(), OWN).choose(
        [candidate("0", "1"), candidate("a0", "2")], connected
    )

    assert chosen == []
