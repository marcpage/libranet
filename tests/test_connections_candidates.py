"""Tests for reading the peers this node could connect to."""

from __future__ import annotations
from json import dumps
from pathlib import Path

from libranet.cas.content_id import ContentId
from libranet.config.seeds import SeedPeer
from libranet.connections.candidates import Candidate, candidate_list, seed_candidates

OWN_ID = ContentId.for_data(b"this node's key", "sha256")
FIRST_ID = ContentId.for_data(b"first peer's key", "sha256")
SECOND_ID = ContentId.for_data(b"second peer's key", "sha256")


def write_candidates(path: Path, nodes: list[tuple[str, list[str]]]) -> Path:
    path.write_text(
        dumps(
            {
                "nodes": [
                    {"node_id": node_id, "endpoints": endpoints} for node_id, endpoints in nodes
                ]
            }
        )
    )
    return path


def test_candidates_come_in_list_order_without_this_node(tmp_path: Path) -> None:
    path = write_candidates(
        tmp_path / "candidates.json",
        [
            (str(FIRST_ID), ["http://203.0.113.1:8080", "http://first.example:8080"]),
            (str(OWN_ID), ["http://203.0.113.9:8080"]),
            (str(SECOND_ID).upper(), ["http://203.0.113.2:8080"]),
        ],
    )

    assert candidate_list(path, OWN_ID) == [
        Candidate(("http://203.0.113.1:8080", "http://first.example:8080"), FIRST_ID),
        Candidate(("http://203.0.113.2:8080",), SECOND_ID),
    ]


def test_unusable_node_ids_and_nodes_without_endpoints_are_dropped(tmp_path: Path) -> None:
    path = write_candidates(
        tmp_path / "candidates.json",
        [
            ("nonsense", ["http://203.0.113.1:8080"]),
            (str(FIRST_ID), []),
            (str(SECOND_ID), ["http://203.0.113.2:8080"]),
        ],
    )

    assert candidate_list(path, OWN_ID) == [Candidate(("http://203.0.113.2:8080",), SECOND_ID)]


def test_a_missing_or_unreadable_candidate_list_names_no_peers(tmp_path: Path) -> None:
    garbled = tmp_path / "garbled.json"
    garbled.write_bytes(b"\xff not json")
    shapeless = tmp_path / "shapeless.json"
    shapeless.write_text(dumps({"nodes": {"http://203.0.113.1:8080": str(FIRST_ID)}}))

    assert candidate_list(tmp_path / "absent.json", OWN_ID) == []
    assert candidate_list(garbled, OWN_ID) == []
    assert candidate_list(shapeless, OWN_ID) == []


def test_seed_ids_are_kept_when_usable() -> None:
    seeds = (
        SeedPeer(address="http://198.51.100.1:8080", node_id=str(FIRST_ID)),
        SeedPeer(address="http://198.51.100.2:8080"),
        SeedPeer(address="http://198.51.100.3:8080", node_id="nonsense"),
    )

    assert seed_candidates(seeds) == [
        Candidate(("http://198.51.100.1:8080",), FIRST_ID),
        Candidate(("http://198.51.100.2:8080",), None),
        Candidate(("http://198.51.100.3:8080",), None),
    ]


def test_a_candidate_is_told_apart_by_its_node_id_or_else_its_endpoint() -> None:
    assert Candidate(("http://203.0.113.1:8080",), FIRST_ID).key == FIRST_ID
    assert Candidate(("http://203.0.113.1:8080",), None).key == "http://203.0.113.1:8080"


def test_a_candidate_can_keep_only_some_of_its_endpoints() -> None:
    candidate = Candidate(("http://203.0.113.1:8080", "http://first.example:8080"), FIRST_ID)

    assert candidate.keeping(lambda endpoint: "example" in endpoint) == Candidate(
        ("http://first.example:8080",), FIRST_ID
    )
    assert candidate.keeping(lambda endpoint: False) is None
